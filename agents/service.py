"""
One entry point for any interface (the Streamlit app, scripts, tests).

ask(question, thread_id) sends a message to the data agent and returns everything an
interface needs to show the answer: the generated SQL, the result rows, whether the
request was refused, retries, time and tokens.

Messages with the same thread_id form one conversation, so follow-ups like
"and in 2026?" are understood. Use a new thread_id to start a fresh chat.
"""

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.messages import HumanMessage

from agents.data_agent import data_agent

StepCallback = Optional[Callable[[str], None]]


@dataclass
class AgentReply:
    question: str
    route: str = ""                     # "sql" or "etl"
    answer: str = ""
    standalone_question: str = ""       # how the agent understood the message
    used_history: bool = False          # True when earlier messages were used to understand it
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


def ask(question: str, thread_id: str = "default", on_step: StepCallback = None) -> AgentReply:
    """Send one message in a conversation and return the full reply."""
    config = {"configurable": {"thread_id": thread_id}}
    start = time.perf_counter()
    final = {}

    with get_usage_metadata_callback() as usage:
        # "custom" carries the progress steps the agents report; "values" the state after each step
        for mode, chunk in data_agent.stream({"messages": [HumanMessage(content=question)]}, config,
                                             stream_mode=["custom", "values"]):
            if mode == "custom" and on_step and "step" in chunk:
                on_step(chunk["step"])
            elif mode == "values":
                final = chunk if isinstance(chunk, dict) else chunk.model_dump()

    turn = final.get("turn", {})
    reply = AgentReply(question=question)
    for name in ("route", "answer", "standalone_question", "used_history", "sql", "rows", "refused",
                 "refusal_type", "retries", "error", "steps", "etl_actions"):
        if name in turn:
            setattr(reply, name, turn[name])

    reply.seconds = time.perf_counter() - start
    reply.input_tokens = sum(u.get("input_tokens", 0) for u in usage.usage_metadata.values())
    reply.output_tokens = sum(u.get("output_tokens", 0) for u in usage.usage_metadata.values())
    return reply


if __name__ == "__main__":
    for message in ["How many rides were requested in each month of 2025?", "And in 2026?"]:
        reply = ask(message, thread_id="demo", on_step=print)
        print(f"Understood as: {reply.standalone_question}")
        print(reply.answer)
        print(reply.sql)
        print()
