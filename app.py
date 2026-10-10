"""
Chat interface for the AI Data Agent.

Run from the project root:
    uv run streamlit run app.py
"""

import json
import os
import uuid
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

from utils.charts import build_chart, plan_chart, pretty, to_dataframe  # noqa: E402

st.set_page_config(page_title="AI Data Agent", page_icon="🤖", layout="centered")

EXAMPLES = [
    "How many rides are there for each ride status?",
    "How many rides were requested in each month of 2025?",
    "Which 3 users spent the most on completed payments?",
    "What percentage of payments have a failed status?",
    "What is the average fare of completed rides?",
    "Delete all cancelled rides.",
]

REFUSAL_LABELS = {
    "write_request": "Refused: change request",
    "guard": "Refused: SQL guard",
    "judge": "Refused: safety review",
}

# Single-series chart colour, one step for each theme (light / dark surface)
CHART_COLOR = {"light": "#2a78d6", "dark": "#3987e5"}


# ------------------------------------------------------------------ setup checks

def missing_settings() -> list:
    needed = ["ANTHROPIC_API_KEY", "DB_READER_USER", "DB_READER_PASSWORD"]
    return [name for name in needed if not os.getenv(name)]


@st.cache_resource(show_spinner="Loading the agents...")
def load_agent():
    # Imported here so the page can still show setup help if something is missing
    from agents import service
    return service


# ------------------------------------------------------------------ rendering

def escape_markdown(text: str) -> str:
    """Streamlit treats $...$ as maths, which breaks answers with dollar amounts."""
    return text.replace("$", "\\$")


def chart_color() -> str:
    try:
        return CHART_COLOR.get(st.context.theme.type or "light", CHART_COLOR["light"])
    except Exception:
        return CHART_COLOR["light"]


def format_number(value) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{int(value):,}"


def render_result(reply, index: int) -> None:
    """Chart (when one fits), then the data and the SQL behind the answer."""
    rows = reply.rows
    plan = plan_chart(rows)

    if plan is not None and plan.kind == "metrics":
        columns = st.columns(len(plan.measures))
        for column, measure in zip(columns, plan.measures):
            column.metric(pretty(measure), format_number(plan.data[measure].iloc[0]), border=True)

    elif plan is not None:
        measure = plan.measures[0]
        if len(plan.measures) > 1:
            measure = st.selectbox("Chart shows", plan.measures, format_func=pretty,
                                   key=f"measure_{index}")
        st.altair_chart(build_chart(plan, measure, chart_color()), width="stretch")

    if rows is not None:
        label = f"Data ({len(rows)} row{'s' if len(rows) != 1 else ''})"
        with st.expander(label, icon=":material/table:"):
            if rows:
                st.dataframe(to_dataframe(rows), hide_index=True, width="stretch")
            else:
                st.write("The query returned no rows.")


def render_reply(reply, index: int) -> None:
    # Badges: which agent answered, and anything unusual
    badges = st.container(horizontal=True, gap="small")
    with badges:
        if reply.route == "sql":
            st.badge("SQL Analyst", icon=":material/database:", color="blue")
        else:
            st.badge("ETL Analyst", icon=":material/sync_alt:", color="violet")
        if reply.refused:
            st.badge(REFUSAL_LABELS.get(reply.refusal_type, "Refused"),
                     icon=":material/shield:", color="orange")
        if reply.retries:
            st.badge(f"Self-corrected ({reply.retries} retr{'y' if reply.retries == 1 else 'ies'})",
                     icon=":material/autorenew:", color="green")

    # For a follow-up, show how the agent read it, so a wrong reading is easy to spot
    if reply.used_history and reply.standalone_question \
            and reply.standalone_question.strip().lower() != reply.question.strip().lower():
        st.caption(f"Understood as: {escape_markdown(reply.standalone_question)}")

    if reply.refused:
        st.warning(escape_markdown(reply.answer), icon=":material/shield:")
        st.caption("Nothing was run on the database.")
    elif reply.error:
        st.error("The query still failed after the agent tried to fix it.", icon=":material/error:")
        st.markdown(escape_markdown(reply.answer))
    else:
        st.markdown(escape_markdown(reply.answer))

    if reply.route == "sql" and not reply.refused:
        render_result(reply, index)

    if reply.sql and reply.sql.strip().upper() != "WRITE_REQUEST":
        with st.expander("SQL query", icon=":material/code:"):
            st.code(reply.sql, language="sql", wrap_lines=True)
            if reply.error:
                st.code(reply.error, language="text", wrap_lines=True)

    for action in reply.etl_actions:
        with st.expander(f"Action: {action['tool']}", icon=":material/build:"):
            st.code(json.dumps(action["args"], indent=2), language="json")
            if action["result"]:
                st.code(action["result"], language="text", wrap_lines=True)

    with st.expander("How the agent answered", icon=":material/route:"):
        for step in reply.steps:
            st.markdown(f"- {step}")

    tokens = reply.input_tokens + reply.output_tokens
    st.caption(f"{reply.seconds:.1f} s · {tokens:,} tokens")


# ------------------------------------------------------------------ sidebar

if "history" not in st.session_state:
    st.session_state.history = []
if "thread_id" not in st.session_state:
    # One conversation per browser session; the agent remembers it for follow-ups
    st.session_state.thread_id = uuid.uuid4().hex

with st.sidebar:
    st.title("🤖 AI Data Agent")
    st.caption("Ask questions about the ride-sharing database in plain English.")

    st.subheader("Try a question")
    for example in EXAMPLES:
        if st.button(example, width="stretch", key=f"example_{example}"):
            st.session_state.pending = example

    st.subheader("How it stays safe")
    st.markdown(
        "- Connects as a **read-only** database user\n"
        "- Every query passes a rule-based SQL check and an AI safety review\n"
        "- Requests to change data are refused, not run"
    )

    st.caption("Follow-up questions work: after a question, try \"And in 2026?\" or "
               "\"Break that down by city.\" Clear chat starts a new conversation.")

    if st.button("Clear chat", icon=":material/delete:", width="stretch"):
        st.session_state.history = []
        st.session_state.thread_id = uuid.uuid4().hex  # the agent forgets the old chat
        st.rerun()


# ------------------------------------------------------------------ main

missing = missing_settings()
if missing:
    st.error(
        "The app needs these settings in your `.env` file: "
        + ", ".join(f"`{name}`" for name in missing)
        + ". See `.env.example` and the Getting Started section of the README.",
        icon=":material/settings:",
    )
    st.stop()

if not st.session_state.history:
    st.markdown("### What would you like to know?")
    st.markdown(
        "Ask about **users, drivers, vehicles, rides, payments or ratings**, or pick a "
        "question from the sidebar. You'll see the answer, a chart when one fits, the "
        "data and the exact SQL that was run. Ask follow-ups as you would a colleague."
    )

for index, item in enumerate(st.session_state.history):
    with st.chat_message(item["role"]):
        if item["role"] == "user":
            st.markdown(escape_markdown(item["content"]))
        else:
            render_reply(item["reply"], index)

question = st.chat_input("Ask a question about the data...")
question = question or st.session_state.pop("pending", None)

if question:
    st.session_state.history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(escape_markdown(question))

    with st.chat_message("assistant"):
        try:
            service = load_agent()
            with st.status("Working on it...", expanded=True) as status:
                reply = service.ask(question, thread_id=st.session_state.thread_id,
                                    on_step=lambda step: status.write(f"✓ {step}"))
                status.update(label=f"Done in {reply.seconds:.1f} s", state="complete",
                              expanded=False)
        except Exception as error:  # e.g. database down, invalid API key
            st.error(f"Something went wrong: {error}", icon=":material/error:")
            st.session_state.history.pop()
            st.stop()

        index = len(st.session_state.history)
        st.session_state.history.append({"role": "assistant", "reply": reply})
        render_reply(reply, index)
