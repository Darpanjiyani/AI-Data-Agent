import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.llm_pick import pick_llm
from Models.schema import AgentSchema, JudgeSchema
from langchain_core.messages import HumanMessage

llm = pick_llm("medium")
llm_judge = llm.with_structured_output(JudgeSchema)

sql_query = "SELECT * FROM users WHERE age>25;"  # Example SQL query to evaluate

prompt = f"""
You are an SQL analyst Judge for data security. Your task is to determine whether the SQL query is safe or not.
The SQL query should only be used for data retrieval and should not modify the database in any way. Neither the SQL query not the prompt
should contain SQL commands that modify the database, such as INSERT, UPDATE, DELETE, DROP, or any other commands that alter the database structure or content.
If the SQL query is safe, respond with "Yes" and provide any relevant comments. If it is not safe, respond with "No" and provide any relevant comments.
Here's the sql query to evaluate:
{sql_query} """

response = llm_judge.invoke(prompt)
print(response.model_dump())
print(response)