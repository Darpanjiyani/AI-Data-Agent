# LLM_Pick is basically the class that we will build, low end LLM, High end LLM, so we need to make sure the exact and quick switch between these LLMs. So that 
# we don't need to hardcode anything.

# LLM_Pick is basically the class that we will build, low end LLM, High end LLM, so we need to make sure the exact and quick switch between these LLMs. So that 
# we don't need to hardcode anything.

from langchain_anthropic import ChatAnthropic
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file


def pick_llm(level: str):
    """
    Pick the appropriate LLM based on the specified level.
    
    Args:
        level (str): The level of the LLM to pick: "low", "medium", "high" or "claude".
    
    Returns:
        ChatAnthropic: The model for the specified level.
    """

    if level.lower() == "low":
        llm = ChatAnthropic(model="claude-haiku-4-5-20251001", temperature = 0)
    elif level.lower() == "medium":
        llm = ChatAnthropic(model="claude-sonnet-5")
    elif level.lower() == "high":
        llm = ChatAnthropic(model="claude-opus-5", temperature = 0)
    elif level.lower() == "claude":
        llm = ChatAnthropic(model_name="claude-sonnet-5")
    else:
        raise ValueError(f"Unsupported level: {level}")
    
    return llm

if __name__ == "__main__":
    llm_obj = pick_llm("low")
    print(llm_obj.invoke("What is the capital of France?"))