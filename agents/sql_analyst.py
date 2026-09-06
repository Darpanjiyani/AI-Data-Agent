import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.llm_pick import pick_llm
from Models.schema import AgentSchema

# ---------------------------- AI Agent Code -----------------------------------------------

def curate_question(state: AgentSchema) -> AgentSchema:

    user_question = state.user_question #Bcz this is pydantic model object, that is why we wrote state.user_question instead of state['user_question']

    llm = pick_llm("low")  # Pick the appropriate LLM based on the specified level

    response = llm.invoke(f"Curate the following question for better understanding: {user_question}")

    state.curated_ques = response
    return state


def prompt_query_context(state: AgentSchema) -> AgentSchema:

    curated_question = state.curated_ques

    llm = pick_llm("medium")  # Pick the appropriate LLM based on the specified level

    response = llm.invoke(f"Generate a detailed prompt with SQL DB context for the following curated question: {curated_ques}")

    state.prompt_query = response
    return state