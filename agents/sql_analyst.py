import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.llm_pick import pick_llm
from utils.database import DatabaseUtil, reader_connection_details
from utils.sql_guard import is_read_only
from utils.schema_notes import AGENT_TABLES, DATA_NOTES
from functools import lru_cache
from Models.schema import AgentSchema, JudgeSchema
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

import re

def clean_sql(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        text = match.group(1)
    return text.strip()

# The SQL step replies with this marker instead of SQL when asked to change data
WRITE_REQUEST_MARKER = "WRITE_REQUEST"

# How many times the agent may rewrite a query after a database error
MAX_SQL_RETRIES = 2


@lru_cache(maxsize=1)
def get_schema_context() -> str:
    """
    Describe the agent's tables once per process and reuse it for every question,
    instead of querying the database schema on every request.
    """
    db = DatabaseUtil(reader_connection_details())
    schema_info = db.schema_details("public", tables=AGENT_TABLES)
    if schema_info.startswith("Error"):
        get_schema_context.cache_clear()  # don't cache a failed attempt
    return schema_info

# ---------------------------- AI Agent Code -----------------------------------------------

def curate_ques(state: AgentSchema) -> AgentSchema:

    user_question = state.user_question #Bcz this is pydantic model object, that is why we wrote state.user_question instead of state['user_question']

    llm = pick_llm("low")  # Pick the appropriate LLM based on the specified level

    prompt = f"""
    Rewrite the following question about a database so it is clear and specific.
    Keep the same meaning and do not add new requirements.
    Return ONLY the rewritten question as a single sentence, with no explanation,
    headings or alternatives.

    Question: {user_question}
    """

    # .text works whether Claude replies with plain text or a list of content blocks
    response = llm.invoke(prompt).text.strip()

    # Return only the fields this step changed. Returning the whole state would
    # make LangGraph add the existing messages again (the list uses an `add` reducer).
    return {"curated_ques": response, "messages": [HumanMessage(content=response)]}


def prompt_query_context(state: AgentSchema) -> AgentSchema:

    curated_question = state.curated_ques

    schema_info = get_schema_context()

    prompt = f"""
You are an expert PostgreSQL analyst. Write ONE PostgreSQL query that answers the
user's question, using the database described below.

Rules:
1. Use only the tables and columns listed in the schema.
2. Only add filters that the question asks for or that the data notes require.
   Don't add extra conditions.
3. Row limits: if the question asks for individual records (for example a list of
   rides or users), return at most 10 rows unless the user asks for a different number.
   For counts, totals, averages and breakdowns (by category, city, month, etc.),
   return every group and never add a LIMIT that could cut groups off. For "top N"
   questions use LIMIT N. For "the most" or "the highest", include ties.
4. Percentages and rates: the denominator must be the whole group named in the
   question. Check that joins don't shrink or duplicate it.
5. When counting things that may have no matching rows (for example drivers with no
   rides), start from the table that lists all of them and use NOT EXISTS or a LEFT JOIN.
6. Give computed columns clear names with AS.
7. Return only the SQL query, with no explanation, because it will be executed directly.
8. This agent can only read data. If the user asks to add, change or delete data, or to
   create, alter or drop tables (even as part of a larger request), don't write SQL:
   reply with exactly {WRITE_REQUEST_MARKER}

Data notes:
{DATA_NOTES}

{schema_info}

User's question: {curated_question}
"""

    return {"prompt_query_context": prompt}

#Generate SQL Query Node
def generate_sql(state: AgentSchema) -> AgentSchema:

    prompt = state.prompt_query_context

    llm = pick_llm("medium")  # Pick the appropriate LLM based on the specified level
    generated_sql_query = llm.invoke(prompt).text

    return {"generated_sql_query": clean_sql(generated_sql_query)}


# Safe node
def is_safe_sql(state: AgentSchema) -> AgentSchema:

    sql_query = state.generated_sql_query

    # Step 0: the SQL step recognised a request to change data and wrote no SQL.
    if sql_query.strip().strip("`.").upper() == WRITE_REQUEST_MARKER:
        return {"is_safe": "No", "refusal_type": "write_request",
                "comments": "The request asks to change data, and this agent can only read data."}

    # Step 1: rule-based check. Instant, free and always gives the same answer.
    # If it fails, the query is rejected without calling the LLM judge.
    allowed, reason = is_read_only(sql_query)
    if not allowed:
        return {"is_safe": "No", "refusal_type": "guard", "comments": f"Blocked by the SQL guard: {reason}"}

    # Step 2: LLM judge as a second opinion.
    llm = pick_llm("medium")
    llm_judge = llm.with_structured_output(JudgeSchema)

    prompt = f"""
    You are an SQL analyst Judge for data security. Your task is to determine whether the SQL query is safe or not.
    The SQL query should only be used for data retrieval and should not modify the database in any way. Neither the SQL query not the prompt
    should contain SQL commands that modify the database, such as INSERT, UPDATE, DELETE, DROP, or any other commands that alter the database structure or content.
    If the SQL query is safe, respond with "Yes" and provide any relevant comments. If it is not safe, respond with "No" and provide any relevant comments.
    Here's the sql query to evaluate:
    {sql_query} """

    response = llm_judge.invoke(prompt).model_dump()
    refusal_type = "" if response['answer'] == "Yes" else "judge"
    return {"is_safe": response['answer'], "comments": response['comments'], "refusal_type": refusal_type}


#canceled SQL Query Node
def canceled_sql(state: AgentSchema) -> AgentSchema:

    comments = state.comments.removeprefix("Blocked by the SQL guard: ")

    if state.refusal_type == "write_request":
        final_answer = (
            "I can only read data, so I can't make changes to the database, such as adding, "
            "updating or deleting records, or creating or dropping tables. I can help you look "
            "at the data instead, for example by listing the records you wanted to change."
        )
    elif state.refusal_type == "guard" and "isn't a valid SQL query" in comments:
        final_answer = (
            "I couldn't turn this request into a valid read-only query, so nothing was run. "
            "Try asking it as a question about the data."
        )
    elif state.refusal_type == "guard":
        final_answer = (
            f"I didn't run this request because the query I wrote didn't pass the safety check: {comments} "
            "Try asking it as a question about the data."
        )
    else:
        final_answer = f"I didn't run this request because the safety review flagged the query: {comments}"

    return {"final_answer": final_answer, "messages": [AIMessage(content=final_answer)]}

# Execute SQL Query Node
def execute_sql(state: AgentSchema) -> AgentSchema:

    sql_query = state.generated_sql_query

    # Always connect with the read-only database user (agent_reader)
    obj = DatabaseUtil(reader_connection_details())

    execution_result = obj.execute_query(sql_query)
    return {"sql_query_execution_result": execution_result}

# Fix SQL Node: called when the query failed with a database error
def fix_sql(state: AgentSchema) -> AgentSchema:

    llm = pick_llm("medium")

    prompt = f"""{state.prompt_query_context}

Your previous query failed when it ran on the database.

Previous query:
{state.generated_sql_query}

Database error:
{state.sql_query_execution_result}

Write a corrected query that answers the same question. Follow all the rules above.
Return only the SQL query.
"""
    corrected_sql = llm.invoke(prompt).text

    return {"generated_sql_query": clean_sql(corrected_sql), "retry_count": state.retry_count + 1}


# Represent the final answer Node
def represent_final_answer(state: AgentSchema) -> AgentSchema:

    execution_result = state.sql_query_execution_result
    curated_question = state.curated_ques

    llm = pick_llm("low")

    prompt = f"""
    You are an SQL analyst agent. Your task is to provide a final answer to the user based on the
    execution result of the SQL query and the user's original question. The final answer should be
    concise, clear, and directly address the user's query. Avoid including any SQL code or technical
    details in the final answer. The final answer should be in a user-friendly format that is easy to
    understand. If the execution result is empty or does not provide a clear answer to the user's question, explain this in the final answer.
    Base the answer only on the execution result. Don't guess about data that isn't in the result
    (for example, don't claim the data stops at a certain date unless the result shows it). \n
    Here is the execution result: {execution_result} \n
    Here is the user's original question: {curated_question}
    """

    llm_response = llm.invoke(prompt).text  # Get the final answer from the LLM

    return {"final_answer": llm_response, "messages": [AIMessage(content=llm_response)]}


# -----------------------------------------Graph Building---------------------------------------------------------------

sql_agent_graph = StateGraph(AgentSchema)

# Nodes
sql_agent_graph.add_node(curate_ques,name="curate_ques")
sql_agent_graph.add_node(prompt_query_context,name="prompt_query_context")
sql_agent_graph.add_node(generate_sql,name="generate_sql")
sql_agent_graph.add_node(is_safe_sql,name="is_safe_sql")
sql_agent_graph.add_node(canceled_sql,name="canceled_sql")
sql_agent_graph.add_node(execute_sql,name="execute_sql")
sql_agent_graph.add_node(fix_sql,name="fix_sql")
sql_agent_graph.add_node(represent_final_answer,name="represent_final_answer")

# Edges
sql_agent_graph.add_edge(START, "curate_ques")
sql_agent_graph.add_edge("curate_ques", "prompt_query_context")
sql_agent_graph.add_edge("prompt_query_context", "generate_sql")
sql_agent_graph.add_edge("generate_sql", "is_safe_sql")

# Codintional Edge Function
def is_safe_sql_edge(state: AgentSchema) -> str:
    is_safe = state.is_safe

    if is_safe.lower() == "yes":
        return "execute_sql"

    else :
        return "canceled_sql"

sql_agent_graph.add_conditional_edges("is_safe_sql", is_safe_sql_edge,
                                      {
                                          "execute_sql": "execute_sql",
                                          "canceled_sql": "canceled_sql"
                                      })

# sql_agent_graph.add_edge("is_safe_sql", "execute_sql")
# sql_agent_graph.add_edge("is_safe_sql", "canceled_sql")

# Self-correction: if the query failed with a database error, rewrite it (up to
# MAX_SQL_RETRIES times). The rewritten query goes through the safety checks again.
def after_execute_edge(state: AgentSchema) -> str:
    failed = state.sql_query_execution_result.startswith("Error executing query")
    if failed and state.retry_count < MAX_SQL_RETRIES:
        return "fix_sql"
    return "represent_final_answer"

sql_agent_graph.add_conditional_edges("execute_sql", after_execute_edge,
                                      {
                                          "fix_sql": "fix_sql",
                                          "represent_final_answer": "represent_final_answer"
                                      })
sql_agent_graph.add_edge("fix_sql", "is_safe_sql")

sql_agent_graph.add_edge("canceled_sql", END)
sql_agent_graph.add_edge("represent_final_answer", END)

# Compile the Graph
sql_analyst = sql_agent_graph.compile()  # It will compile the graph, will take everything (node, edges, conditional edges) and create a runnable workflow

if __name__ == "__main__":


    # Optional
    from IPython.display import display, Image
    img = Image(sql_analyst.get_graph().draw_mermaid_png())
    with open("sql_analyst_graph.png", "wb") as f:
        f.write(img.data)

    input_schema = {
        "messages": [],
        "user_question": "What are the different types of Payment Methods we have in our database",
        "curated_ques": "",
        "prompt_query_context": "",
        "generated_sql_query": "",
        "is_safe": "No",
        "comments": "",
        "sql_query_execution_result": "",
        "final_answer": ""
    }

    # Execute the Graph
    sql_analyst_response = sql_analyst.invoke(input_schema)
    print(sql_analyst_response['messages'])  # Print the final output of the graph execution
    print("********************************")

    print(sql_analyst_response['generated_sql_query'])  # Print the generated SQL query

    print("********************************")

    print(sql_analyst_response['sql_query_execution_result'])  # Print the result of executing the SQL query

    print("********************************")

    print(sql_analyst_response['prompt_query_context'])  # Print the prompt query context