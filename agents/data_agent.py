import os
import sys
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.llm_pick import pick_llm
from Models.schema import RouterSchema, DataAgentSchema
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from agents.etl_analyst import etl_analyst
from agents.sql_analyst import sql_analyst


llm = pick_llm("claude")

llm_router = llm.with_structured_output(RouterSchema)

# How many earlier question/answer pairs the agents see when reading a follow-up
HISTORY_TURNS = 3
# Long answers are cut in the history; the start carries what follow-ups refer to
HISTORY_ANSWER_CHARS = 600

# What each SQL agent step is called in the interface
SQL_STEP_LABELS = {
    "curate_ques": "Understood the question",
    "prompt_query_context": "Added the database schema and rules",
    "generate_sql": "Wrote the SQL query",
    "is_safe_sql": "Checked that the query is read-only",
    "execute_sql": "Ran the query as the read-only user",
    "fix_sql": "The query failed, so it was rewritten",
    "canceled_sql": "Refused the request",
    "represent_final_answer": "Wrote the answer",
}


# ---------------------------- CONVERSATION HISTORY ---------------------------- #

def format_history(messages: list, turns: int = HISTORY_TURNS) -> str:
    """
    The last few exchanges before the current message, as plain text.
    Returns "" for the first message of a chat, so a standalone question is
    handled exactly as before conversation memory existed.
    """
    earlier = messages[:-1][-2 * turns:]
    lines = []
    for message in earlier:
        if isinstance(message, HumanMessage):
            lines.append(f"User: {message.content}")
        elif isinstance(message, AIMessage):
            understood = message.additional_kwargs.get("standalone_question", "")
            answer = message.text.strip()
            if len(answer) > HISTORY_ANSWER_CHARS:
                answer = answer[:HISTORY_ANSWER_CHARS] + " ..."
            if understood:
                lines.append(f"(The assistant understood this as: {understood})")
            lines.append(f"Assistant: {answer}")
    return "\n".join(lines)


def route_question(question: str, history: str = "") -> str:
    """Ask the router whether this is a database question ("sql") or a file/API task ("etl")."""
    if not history:
        prompt = question  # same input as before conversation memory
    else:
        prompt = (
            f"Conversation so far:\n{history}\n\n"
            f"New message: {question}\n\n"
            "Classify the new message. Use the conversation only to understand what it refers to."
        )
    return llm_router.invoke(prompt).model_dump()["answer"]


# ---------------------------- DATA AGENT GRAPH ---------------------------- #


def router_node(state: DataAgentSchema):

    question = state.messages[-1].content
    history = format_history(state.messages)

    route_response = route_question(question, history)

    label = "Sent to the SQL Analyst" if route_response == "sql" else "Sent to the ETL Analyst"
    get_stream_writer()({"step": label})

    return {"route_response": route_response, "turn": {"route": route_response, "steps": [label],
                                                       "used_history": bool(history)}}


def etl_node(state: DataAgentSchema):

    question = state.messages[-1].content
    history = format_history(state.messages)
    request = question if not history else f"Conversation so far:\n{history}\n\nNew request: {question}"

    write = get_stream_writer()
    turn = dict(state.turn)
    steps, actions, calls, last_message = list(turn.get("steps", [])), [], {}, None

    for update in etl_analyst.stream({"messages": [HumanMessage(content=request)]}, stream_mode="updates"):
        for node, changes in update.items():
            for message in (changes or {}).get("messages", []):
                last_message = message
                for call in getattr(message, "tool_calls", None) or []:
                    action = {"tool": call["name"], "args": call["args"], "result": ""}
                    calls[call["id"]] = action
                    actions.append(action)
                    label = f"Used {call['name']}"
                    steps.append(label)
                    write({"step": label})
                if isinstance(message, ToolMessage) and message.tool_call_id in calls:
                    calls[message.tool_call_id]["result"] = str(message.content)

    answer = last_message.text if last_message is not None else ""
    turn.update({"answer": answer, "steps": steps, "etl_actions": actions, "standalone_question": question})

    # Add only the ETL agent's final reply to the conversation
    return {"messages": [AIMessage(content=answer)], "turn": turn}


def sql_node(state: DataAgentSchema):

    question = state.messages[-1].content

    input_schema = {
        "messages": [],
        "user_question": question,
        "history": format_history(state.messages),
        "curated_ques": "",
        "prompt_query_context": "",
        "generated_sql_query": "",
        "is_safe": "No",
        "comments": "",
        "sql_query_execution_result": "",
        "final_answer": ""
    }

    write = get_stream_writer()
    turn = dict(state.turn)
    steps = list(turn.get("steps", []))
    final = {}

    # stream_mode="updates" yields {node_name: fields_it_changed} after each step,
    # so the interface can show progress while the agent works.
    for update in sql_analyst.stream(input_schema, stream_mode="updates"):
        for node, changes in update.items():
            final.update(changes or {})
            label = SQL_STEP_LABELS.get(node, node)
            steps.append(label)
            write({"step": label})

    refused = "Refused the request" in steps
    result = final.get("sql_query_execution_result", "")
    rows, error = None, ""
    if not refused and result:
        if result.startswith("Error"):
            error = result
        elif result.startswith("["):
            rows = json.loads(result)
        else:  # "The query did not return any rows."
            rows = []

    answer = final.get("final_answer", "")
    standalone = final.get("curated_ques", "")
    turn.update({
        "answer": answer,
        "standalone_question": standalone,
        "sql": final.get("generated_sql_query", ""),
        "rows": rows,
        "error": error,
        "refused": refused,
        "refusal_type": final.get("refusal_type", ""),
        "retries": final.get("retry_count", 0),
        "steps": steps,
    })

    # Add only the SQL agent's final answer to the conversation; the question it
    # understood is kept with it so later follow-ups can build on it.
    return {"messages": [AIMessage(content=answer, additional_kwargs={"standalone_question": standalone})],
            "turn": turn}


data_agent_graph = StateGraph(DataAgentSchema)

data_agent_graph.add_node("router_node", router_node)
data_agent_graph.add_node("etl_node", etl_node)
data_agent_graph.add_node("sql_node", sql_node)

data_agent_graph.add_edge(START, "router_node")

def route_edge(state: DataAgentSchema) -> str:
    if state.route_response == "sql":
        return "sql_node"
    elif state.route_response == "etl":
        return "etl_node"
    else:
        raise ValueError(f"Invalid route response: {state.route_response}")


data_agent_graph.add_conditional_edges("router_node", route_edge,
                                      {
                                          "sql_node": "sql_node",
                                          "etl_node": "etl_node"
                                      })

data_agent_graph.add_edge("sql_node", END)
data_agent_graph.add_edge("etl_node", END)

# The checkpointer keeps each chat's messages, keyed by the thread_id passed in the
# config, so follow-up questions can refer to earlier ones. InMemorySaver lives as long
# as the process; swap in a database-backed saver to keep chats across restarts.
data_agent = data_agent_graph.compile(checkpointer=InMemorySaver())


if __name__ == "__main__":

    # Optional: save a picture of the graph (needs internet for Mermaid rendering)
    from IPython.display import Image
    img = Image(data_agent.get_graph().draw_mermaid_png())
    with open("data_agent_graph.png", "wb") as f:
        f.write(img.data)

    config = {"configurable": {"thread_id": "demo"}}
    for question in ["How many rides were requested in each month of 2025?", "And in 2026?"]:
        response = data_agent.invoke({"messages": [HumanMessage(content=question)]}, config)
        print(f"Q: {question}\nA: {response['messages'][-1].text}\n")
