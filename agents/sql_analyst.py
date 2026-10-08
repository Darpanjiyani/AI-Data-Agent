import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.llm_pick import pick_llm
from utils.database import DatabaseUtil, reader_connection_details
from utils.sql_guard import is_read_only
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

    response = llm.invoke(prompt).content.strip()

    # Return only the fields this step changed. Returning the whole state would
    # make LangGraph add the existing messages again (the list uses an `add` reducer).
    return {"curated_ques": response, "messages": [HumanMessage(content=response)]}


def prompt_query_context(state: AgentSchema) -> AgentSchema:

    curated_question = state.curated_ques

    # Always connect with the read-only database user (agent_reader)
    obj = DatabaseUtil(reader_connection_details())

    schema_info = obj.schema_details("public")  # Fetch schema details from the database

    # Constructing the prompt query for the agent to generate the SQL query
    prompt = f"""
    You are an SQL analyst agent. Your task is to convert the user's natural language
    query into Postgres SQL query that can be executed on the database. You are provided
    with the user's original query and the schema details of the database, including
    table names, column names, data types, and sample data for each table so that
    you can understand the structure of the database and generate an accurate SQL query.
    Unless user explicitly asks for specific number of rows, always limit the output to 10 rows.
    Note - Just generate the SQL query without any explanation or additional text because
    this query will be executed directly on the database. So, the output should be SQL
    ready to be executed without any modifications.

    User's Original Query: {curated_question}

    Database Schema Details:
    {schema_info}

    """

    return {"prompt_query_context": prompt}

#Generate SQL Query Node
def generate_sql(state: AgentSchema) -> AgentSchema:

    prompt = state.prompt_query_context

    llm = pick_llm("medium")  # Pick the appropriate LLM based on the specified level
    generated_sql_query = llm.invoke(prompt).content

    return {"generated_sql_query": clean_sql(generated_sql_query)}


# Safe node
def is_safe_sql(state: AgentSchema) -> AgentSchema:

    sql_query = state.generated_sql_query

    # Step 1: rule-based check. Instant, free and always gives the same answer.
    # If it fails, the query is rejected without calling the LLM judge.
    allowed, reason = is_read_only(sql_query)
    if not allowed:
        return {"is_safe": "No", "comments": f"Blocked by the SQL guard: {reason}"}

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
    return {"is_safe": response['answer'], "comments": response['comments']}


#canceled SQL Query Node
def canceled_sql(state: AgentSchema) -> AgentSchema:

    comments = state.comments

    final_answer = f"The generated SQL query was deemed unsafe to execute. Reason: {comments}. Therefore, the SQL query will not be executed."

    return {"final_answer": final_answer, "messages": [AIMessage(content=final_answer)]}

# Execute SQL Query Node
def execute_sql(state: AgentSchema) -> AgentSchema:

    sql_query = state.generated_sql_query

    # Always connect with the read-only database user (agent_reader)
    obj = DatabaseUtil(reader_connection_details())

    execution_result = obj.execute_query(sql_query)
    return {"sql_query_execution_result": execution_result}

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
    understand. If the execution result is empty or does not provide a clear answer to the user's question, explain this in the final answer. \n
    Here is the execution result: {execution_result} \n
    Here is the user's original question: {curated_question}
    """

    llm_response = llm.invoke(prompt).content  # Get the final answer from the LLM

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

sql_agent_graph.add_edge("canceled_sql", END)
sql_agent_graph.add_edge("execute_sql", "represent_final_answer")
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