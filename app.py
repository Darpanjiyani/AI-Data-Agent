"""
Chat interface for the AI Data Agent.

Run from the project root:
    uv run streamlit run app.py
"""

import json
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

from utils import chat_store  # noqa: E402
from utils.charts import build_chart, plan_chart, pretty, to_dataframe  # noqa: E402

# The agent keeps its memory of each chat in the same file as the saved chats, so a chat
# reopened after a restart still understands follow-ups. (The evals don't set this, so
# their runs always start with empty memory.)
os.environ.setdefault("AGENT_MEMORY_DB", str(chat_store.CHAT_DB))

st.set_page_config(page_title="AI Data Agent", page_icon="🤖", layout="centered")

EXAMPLES = [
    "How many rides are in each status?",
    "How many rides per month in 2025?",
    "Who are our top 3 spenders?",
    "What share of payments failed?",
    "What's the average fare of a completed ride?",
    "Delete all cancelled rides.",
]

# Chat titles in the sidebar: left-aligned on one line, like a chat app's history list
SIDEBAR_CSS = """
<style>
div[class*="st-key-chat_"] button { justify-content: flex-start; }
div[class*="st-key-chat_"] button div { justify-content: flex-start; min-width: 0; }
div[class*="st-key-chat_"] button p {
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-align: left;
}
</style>
"""

REFUSAL_LABELS = {
    "write_request": "Refused: change request",
    "guard": "Refused: SQL guard",
    "invalid_sql": "Couldn't write a valid query",
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


def render_result(reply, key: str) -> None:
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
                                   key=f"measure_{key}")
        st.altair_chart(build_chart(plan, measure, chart_color()), width="stretch")

    if rows is not None:
        label = f"Data ({len(rows)} row{'s' if len(rows) != 1 else ''})"
        with st.expander(label, icon=":material/table:"):
            if rows:
                st.dataframe(to_dataframe(rows), hide_index=True, width="stretch")
            else:
                st.write("The query returned no rows.")


def render_reply(reply, key: str) -> None:
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
        render_result(reply, key)

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
    cached = f" ({reply.cache_read_tokens:,} read from cache)" if reply.cache_read_tokens else ""
    st.caption(f"{reply.seconds:.1f} s · {tokens:,} tokens{cached}")


# ------------------------------------------------------------------ chats

# Run locally, every chat belongs to "local". A public demo would pass a per-browser id,
# so visitors only ever see their own chats.
VISITOR_ID = "local"

chat_store.setup()
st.session_state.setdefault("chat_id", None)      # the open chat; None = a new, empty chat
st.session_state.setdefault("awaiting", None)     # a question sent but not answered yet


def open_chat(chat_id) -> None:
    st.session_state.chat_id = chat_id
    st.session_state.awaiting = None


def send(question: str) -> None:
    """Start a chat if needed, then answer on the next run (so the sidebar already lists it)."""
    if st.session_state.chat_id is None:
        st.session_state.chat_id = chat_store.create_chat(VISITOR_ID, question)
    st.session_state.awaiting = question


def load_history(chat_id) -> list:
    if chat_id is None:
        return []
    items = []
    for message in chat_store.load_messages(chat_id, VISITOR_ID):
        if message["role"] == "user":
            items.append({"role": "user", "content": message["content"]})
        else:
            service = load_agent()
            items.append({"role": "assistant",
                          "reply": service.AgentReply(**json.loads(message["content"]))})
    return items


# ------------------------------------------------------------------ sidebar

st.markdown(SIDEBAR_CSS, unsafe_allow_html=True)

with st.sidebar:
    st.title("🤖 AI Data Agent")
    st.caption("Ask questions about the ride-sharing database in plain English.")

    st.button("New chat", icon=":material/edit_square:", width="stretch", type="primary",
              on_click=open_chat, args=(None,), disabled=st.session_state.chat_id is None)

    st.subheader("Chats")
    chats = chat_store.list_chats(VISITOR_ID)
    if not chats:
        st.caption("Your chats will appear here.")
    for chat in chats:
        is_open = chat["chat_id"] == st.session_state.chat_id
        st.button(chat["title"], key=f"chat_{chat['chat_id']}", width="stretch",
                  type="secondary" if is_open else "tertiary",
                  icon=":material/chat_bubble:" if is_open else ":material/chat_bubble_outline:",
                  on_click=open_chat, args=(chat["chat_id"],))

    st.divider()
    with st.expander("How it stays safe", icon=":material/shield:"):
        st.markdown(
            "- Connects as a **read-only** database user\n"
            "- Every query passes a rule-based SQL check and an AI safety review\n"
            "- Requests to change data are refused, not run"
        )
    if st.session_state.chat_id is not None and st.session_state.awaiting is None:
        if st.button("Delete this chat", icon=":material/delete:", width="stretch"):
            chat_id = st.session_state.chat_id
            chat_store.delete_chat(chat_id, VISITOR_ID)
            try:
                load_agent().forget(chat_id)  # the agent forgets it too
            except Exception:
                pass
            open_chat(None)
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

chat_id = st.session_state.chat_id
history = load_history(chat_id)

if not history and st.session_state.awaiting is None:
    st.markdown("### What would you like to know?")
    st.markdown(
        "Ask about **users, drivers, vehicles, rides, payments or ratings**. You'll see the "
        "answer, a chart when one fits, the data and the exact SQL that was run. "
        "Follow-ups work too: try \"And in 2026?\" after a question."
    )
    st.caption("Try one of these")
    columns = st.columns(2)
    for number, example in enumerate(EXAMPLES):
        columns[number % 2].button(example, key=f"example_{number}", width="stretch",
                                   on_click=send, args=(example,))

for index, item in enumerate(history):
    with st.chat_message(item["role"]):
        if item["role"] == "user":
            st.markdown(escape_markdown(item["content"]))
        else:
            render_reply(item["reply"], f"{chat_id}_{index}")

question = st.chat_input("Ask a question about the data...",
                         disabled=st.session_state.awaiting is not None)
if question:
    send(question)
    st.rerun()

question = st.session_state.awaiting
if question:
    with st.chat_message("user"):
        st.markdown(escape_markdown(question))

    with st.chat_message("assistant"):
        try:
            service = load_agent()
            with st.status("Working on it...", expanded=True) as status:
                reply = service.ask(question, thread_id=chat_id,
                                    on_step=lambda step: status.write(f"✓ {step}"))
                status.update(label=f"Done in {reply.seconds:.1f} s", state="complete",
                              expanded=False)
        except Exception as error:  # e.g. database down, invalid API key
            st.session_state.awaiting = None
            if not history:  # nothing was saved in this chat, so don't keep an empty one
                chat_store.delete_chat(chat_id, VISITOR_ID)
                st.session_state.chat_id = None
            st.error(f"Something went wrong: {error}", icon=":material/error:")
            st.stop()

        chat_store.add_message(chat_id, "user", question)
        chat_store.add_reply(chat_id, reply)
        st.session_state.awaiting = None
        render_reply(reply, f"{chat_id}_{len(history) + 1}")
    st.rerun()  # moves the chat to the top of the list and re-enables the input
