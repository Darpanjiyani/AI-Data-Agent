"""
One entry point for any interface (the Streamlit app, scripts, tests).

ask(question) routes the request to the SQL or ETL agent and returns everything an
interface needs to show the answer: the generated SQL, the result rows, whether the
request was refused, retries, time and tokens.
"""

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.messages import HumanMessage, ToolMessage

from agents.data_agent import llm_router
from agents.etl_analyst import etl_analyst
from agents.sql_analyst import sql_analyst

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

StepCallback = Optional[Callable[[str], None]]


@dataclass
class AgentReply:
    question: str
    route: str                          # "sql" or "etl"
    answer: str = ""
    sql: str = ""                       # final SQL query (SQL route)
    rows: Optional[list] = None         # query result rows, if the query ran
    refused: bool = False
    refusal_type: str = ""              # "write_request", "guard", "judge"
    retries: int = 0
    error: str = ""                     # database error, if every attempt failed
    steps: list = field(default_factory=list)       # what the agent did, in order
    etl_actions: list = field(default_factory=list) # ETL tool calls and their results
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


def route_question(question: str) -> str:
    """Ask the router whether this is a database question ("sql") or a file/API task ("etl")."""
    return llm_router.invoke(question).model_dump()["answer"]


def _run_sql(reply: AgentReply, on_step: StepCallback) -> None:
    state = {
        "messages": [],
        "user_question": reply.question,
        "curated_ques": "",
        "prompt_query_context": "",
        "generated_sql_query": "",
        "is_safe": "No",
        "comments": "",
        "sql_query_execution_result": "",
        "final_answer": "",
    }

    final = {}
    # stream_mode="updates" yields {node_name: fields_it_changed} after each step,
    # so the interface can show progress while the agent works.
    for update in sql_analyst.stream(state, stream_mode="updates"):
        for node, changes in update.items():
            final.update(changes or {})
            label = SQL_STEP_LABELS.get(node, node)
            reply.steps.append(label)
            if on_step:
                on_step(label)

    reply.answer = final.get("final_answer", "")
    reply.sql = final.get("generated_sql_query", "")
    reply.retries = final.get("retry_count", 0)
    reply.refusal_type = final.get("refusal_type", "")
    reply.refused = "Refused the request" in reply.steps

    result = final.get("sql_query_execution_result", "")
    if reply.refused or not result:
        return
    if result.startswith("Error"):
        reply.error = result
    elif result.startswith("["):
        reply.rows = json.loads(result)
    else:  # "The query did not return any rows."
        reply.rows = []


def _run_etl(reply: AgentReply, on_step: StepCallback) -> None:
    last_message = None
    calls = {}

    for update in etl_analyst.stream({"messages": [HumanMessage(content=reply.question)]},
                                     stream_mode="updates"):
        for node, changes in update.items():
            for message in (changes or {}).get("messages", []):
                last_message = message
                for call in getattr(message, "tool_calls", None) or []:
                    action = {"tool": call["name"], "args": call["args"], "result": ""}
                    calls[call["id"]] = action
                    reply.etl_actions.append(action)
                    label = f"Used {call['name']}"
                    reply.steps.append(label)
                    if on_step:
                        on_step(label)
                if isinstance(message, ToolMessage) and message.tool_call_id in calls:
                    calls[message.tool_call_id]["result"] = str(message.content)

    reply.answer = last_message.text if last_message is not None else ""


def ask(question: str, on_step: StepCallback = None) -> AgentReply:
    """Route a request, run the right agent, and return the full reply."""
    start = time.perf_counter()

    with get_usage_metadata_callback() as usage:
        route = route_question(question)
        reply = AgentReply(question=question, route=route)
        label = "Sent to the SQL Analyst" if route == "sql" else "Sent to the ETL Analyst"
        reply.steps.append(label)
        if on_step:
            on_step(label)

        if route == "sql":
            _run_sql(reply, on_step)
        else:
            _run_etl(reply, on_step)

    reply.seconds = time.perf_counter() - start
    reply.input_tokens = sum(u.get("input_tokens", 0) for u in usage.usage_metadata.values())
    reply.output_tokens = sum(u.get("output_tokens", 0) for u in usage.usage_metadata.values())
    return reply


if __name__ == "__main__":
    reply = ask("How many rides are there for each ride status?", on_step=print)
    print(reply.answer)
    print(reply.sql)
    print(reply.rows)
