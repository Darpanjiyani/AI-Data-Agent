import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.llm_pick import pick_llm
from utils.etl_tools import ETLTools, resolve_data_path
from Models.schema import ETLAgentSchema
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import StateGraph, START, END
from langchain.tools import tool
from langchain_anthropic import ChatAnthropic
import re


def clean_code(text: str) -> str:
    """Extract the code from a ```python ... ``` block, if there is one."""
    text = text.strip()
    match = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        text = match.group(1)
    return text.strip()


#------------------------------------ AGENT TOOLS ------------------------------------#


@tool
def extract_load_tool(url:str, output_folder:str, format:str) -> str:
    """
    This tool extracts the data from the API (url) and loads it into the
    the desired location (output_folder).

    Args:
        url (str): The API endpoint from which to extract data.
        output_folder (str): The folder where the extracted data will be saved.
        format (str): The format in which to save the extracted data (csv, json, parquet).
    
    Returns:
        str: A message indicating the success or failure of the operation.

    """
    etl_tools = ETLTools()
    return etl_tools.extract_load(url, output_folder, format)


@tool
def transform_load_tool(input_file_path:str,output_folder:str,output_format:str, user_question:str) -> str:
    """
    This tool transforms the data from the specified file and loads it into the
    desired location (output_folder).

    Args:
        input_file_path (str): The path to the file containing the data to be transformed (inside the data/ folder).
        output_folder (str): The folder where the transformed data will be saved (inside the data/ folder).
        output_format (str): The format in which to save the transformed data (csv, json, parquet).
        user_question (str): The user's request describing the transformation.

    Returns:
        str: A message indicating the success or failure of the operation.

    """
    etl_tools = ETLTools()

    # Both paths must stay inside the project's data/ folder
    try:
        input_path = resolve_data_path(input_file_path)
        output_path = resolve_data_path(output_folder)
        top_3_rows = etl_tools.transform_load_context(input_file_path)
    except (ValueError, FileNotFoundError) as e:
        return f"Could not run the transformation: {e}"

    llm = pick_llm("claude")

    # Forward slashes avoid Windows backslash escape problems in generated code
    input_posix = input_path.as_posix()
    output_posix = output_path.as_posix()

    prompt = f"""
            You are a Python Data Analyst who uses Pandas to analyze data.
            You need to provide only the Pandas Code that will help to perform the right ETL operations on the data stored in the file : {input_posix}
            as per the user's question. Do not provide any explanation or comments, only
            the code should be provided. The code should be in a format that can be executed
            in a Python environment with Pandas installed.
            Don't write anything else than Pandas Code. \n

            Create the Pandas Dataframe from the data stored in the file : {input_posix} and then
            write the code to transform the data and save it as a single {output_format} file
            inside the folder {output_posix}. Create the folder with os.makedirs(..., exist_ok=True)
            if it does not exist. Only read and write files inside these paths.
            Here's the user's question: {user_question}\n
            Here's the context of the data you will be analyzing: {top_3_rows}\n

        """

    # .text works whether Claude replies with plain text or a list of content blocks
    response = llm.invoke(prompt).text

    # Remove a ```python ... ``` code fence if the model added one
    pandas_code = clean_code(response)

    # Execute the Pandas code in a separate process with a time limit
    results = etl_tools.execute_code(pandas_code)

    if results.startswith("Failed"):
        return f"The transformation failed. \n\n Pandas Code: \n {pandas_code} \n\n Error: \n {results}"

    return f"The data is transformed and saved at {output_folder} in {output_format} format. \n\n Pandas Code Executed: \n {pandas_code} \n\n Execution Result: \n {results}"


# Toolkit 
tools = [extract_load_tool, transform_load_tool]

llm = pick_llm("claude")
llm_bind = llm.bind_tools(tools)


# ---------------------------------------- AGENT GRAPH ---------------------------------------- #

def llm_node(state:ETLAgentSchema):

    messages = state.messages

    prompt = f"""
            You are a Python Data Analyst who has access to tools that can extract and load, 
            transform and load data. You will be provided with a user's question 
            and you would need to perform the right ETL operations as per the user's question. 
            If the operation is performed then inform the user and end the coversation.
            You cannot change the database. If the user asks to add, change or delete database
            records, don't call a tool: reply that you can only read data and can't make changes.
            Here's the chat history: {messages}\n
    """

    final_answer = llm_bind.invoke(prompt)

    # Return only the fields this step changed. Returning the whole state would
    # make LangGraph add the existing messages again (the list uses an `add` reducer).
    return {"messages": [final_answer]}


def tool_node(state:ETLAgentSchema):
    """
    This node is responsible for invoking the appropriate tool based on the user's question and the context provided by the LLM.
    """

    tools_results = []

    tools_by_name = {tool.name: tool for tool in tools}

    tool_calls = state.messages[-1].tool_calls

    for tool_call in tool_calls:

        tool = tools_by_name[tool_call['name']]
        observation = tool.invoke(tool_call['args'])

        tools_results.append(ToolMessage(content=observation, tool_call_id = tool_call['id']))

    return {"messages": tools_results}   


# Nodes & Edges
etl_analyst_graph = StateGraph(ETLAgentSchema)
etl_analyst_graph.add_node("llm_node", llm_node)
etl_analyst_graph.add_node("tool_node", tool_node)

etl_analyst_graph.add_edge(START, "llm_node")

def is_tool_call(state:ETLAgentSchema):
    tool_calls = state.messages[-1].tool_calls

    if tool_calls:
        return "tool_node"
    else:
        return "end"

etl_analyst_graph.add_conditional_edges(
    "llm_node",is_tool_call,
    {
        "tool_node": "tool_node",
        "end": END
    }
)

etl_analyst_graph.add_edge("tool_node", "llm_node")

etl_analyst = etl_analyst_graph.compile()

if __name__ == "__main__":
    # Compile the Graph
    

    # Optional
    from IPython.display import display, Image
    img = Image(etl_analyst.get_graph().draw_mermaid_png())
    with open("etl_analyst_graph.png", "wb") as f:
        f.write(img.data)

    response = etl_analyst.invoke(
        {"messages":[HumanMessage(content="I want to extract the data from the API endpoint 'https://pokeapi.co/api/v2/pokemon' and save it to data/extract folder in the csv folder")]}
    ) 

    print(response)