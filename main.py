from agents.data_agent import data_agent
from langchain_core.messages import HumanMessage

if __name__ == "__main__":
    # Messages with the same thread_id are one conversation (needed by the checkpointer)
    config = {"configurable": {"thread_id": "main"}}
    response = data_agent.invoke(
        {"messages": [HumanMessage(content="I want to extract the data from the API endpoint 'https://pokeapi.co/api/v2/pokemon' and save it to data/extract folder in the csv folder")]},
        config,
    )

    print(response["messages"][-1].text)
