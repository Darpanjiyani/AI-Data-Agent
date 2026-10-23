# 🤖 AI Data Agent

Ask questions about your data in plain English, and let a team of AI agents answer them safely.

![Python](https://img.shields.io/badge/Python-3.11+-blue) ![LangGraph](https://img.shields.io/badge/LangGraph-multi--agent-purple) ![Claude](https://img.shields.io/badge/LLM-Claude-orange) ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-read--only-336791)

| Execution accuracy (47 questions, avg of 3 runs) | Held-out sets (2 × 20 unseen questions) | Follow-up conversations (32) | Change requests executed |
|:---:|:---:|:---:|:---:|
| **89.4% → 99.3%** | **100%** | **100%** | **0 / 17**, all clearly refused |

![Demo: a question answered with a chart, a follow-up about "the top one" understood as Montreal, then a vague delete request refused](docs/demo.gif)

---

## Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Features](#3-features)
4. [Getting Started](#4-getting-started)
5. [Safety Improvements](#5-safety-improvements)
6. [Evaluation Framework](#6-evaluation-framework)
7. [Performance Evolution](#7-performance-evolution)
8. [Roadmap](#8-roadmap)
9. [Future Work](#9-future-work)
10. [Project Structure](#10-project-structure)

---

## 1. Project Overview

AI Data Agent is a multi-agent system built with **LangGraph** and **Claude**. A router agent reads each request and sends it to one of two specialists:

- **SQL Analyst** turns a question like *"Which payment methods do riders use most?"* into a PostgreSQL query, checks that it is safe, runs it, and explains the result in plain language.
- **ETL Analyst** extracts data from REST APIs and transforms CSV/JSON/Parquet files with pandas.

The project ships with a synthetic ride-sharing dataset (users, vehicles, rides, payments, ratings) and an **evaluation framework** that measures accuracy, safety and routing, so every improvement is backed by numbers. The full history of changes and results is in [docs/experiments.md](docs/experiments.md).

**Highlights**
- Three independent layers of SQL safety; unsafe requests are never executed.
- A 59-question evaluation harness with execution accuracy and LLM-as-a-judge scoring.
- Accuracy raised from 89.4% to 99.3% (average of 3 runs) on 47 SQL questions, and 100% on two held-out sets of 20 questions each, the second written after the last change to the agent.
- Prompt caching cuts the billed input per question by about 59%, with no loss of accuracy on the held-out or follow-up sets.
- A result sanity check catches queries that run but return the wrong shape (56 rows for a "how many" question) and rewrites them; it fixed the last flaky question both times it fired.
- Conversation memory: follow-ups like "and in 2026?" or "how many rides did the top one take?" answered correctly in all 32 test conversations, and follow-up requests to change data ("delete them") refused.
- Measured, documented improvement rounds: each change is evaluated before and after.
- A Streamlit chat app that shows the answer, an automatic chart, the data and the exact SQL that was run.

---

## 2. Architecture

```
                     ┌──────────────────────────────┐
   Your question ──► │     Data Agent (router)      │ ◄── conversation memory
                     │   Classifies: "sql" or "etl" │     (checkpointer, per chat)
                     └──────────────┬───────────────┘
                                    │
                ┌───────────────────┴───────────────────┐
                ▼                                       ▼
     ┌─────────────────────┐                 ┌─────────────────────┐
     │     SQL Analyst     │                 │     ETL Analyst     │
     │  (fixed pipeline)   │                 │ (tool-using agent)  │
     └─────────────────────┘                 └─────────────────────┘
       1. Rewrite question (a follow-up        Claude picks a tool and
          becomes a standalone question)
       2. Schema + data notes (prompt-cached)  loops until the task is done:
       3. Generate SQL                         • extract_load_tool
       4. Change request? → clear refusal      • transform_load_tool
          SQL guard + LLM safety judge           (code runs in an isolated,
       5. Execute as read-only user               time-limited process)
          error? → rewrite and retry (max 2)
          wrong shape (many rows for a
          "how many")? → rewrite and retry
       6. Explain the answer
```

| Component | File | Role |
|---|---|---|
| Router + memory | `agents/data_agent.py` | Structured-output classification (`sql` / `etl`); a LangGraph checkpointer keeps each chat's messages (SQLite in the app, in memory for evals) |
| Service | `agents/service.py` | `ask(question, thread_id)`: one call for any interface, returns answer, SQL, rows and steps |
| SQL Analyst | `agents/sql_analyst.py` | LangGraph pipeline from question to answer |
| ETL Analyst | `agents/etl_analyst.py` | ReAct-style tool-calling loop |
| Schema context | `utils/database.py`, `utils/schema_notes.py` | Tables, columns, allowed values, foreign keys, data notes |
| SQL guard | `utils/sql_guard.py` | Rule-based check: one read-only `SELECT` only |
| ETL tools | `utils/etl_tools.py` | API extraction, file reading, isolated code execution |
| Model selection | `utils/llm_pick.py` | Smaller model for simple steps, stronger model for hard ones |

**Model tiers**

| Level | Model | Used for |
|---|---|---|
| `low` | Claude Haiku | Rewriting a standalone question, writing the final answer |
| `medium` | Claude Sonnet | Rewriting a follow-up in context, generating SQL, safety judge, eval judge |
| `claude` | Claude Sonnet | Router, ETL agent, pandas code generation |
| `high` | Claude Opus | Defined, not used yet |

<details>
<summary>Workflow diagrams generated by LangGraph</summary>

![SQL Analyst workflow](sql_analyst_graph.png)
![ETL Analyst workflow](etl_analyst_graph.png)

</details>

---

## 3. Features

### Chat app
- Ask questions in a chat interface (`uv run streamlit run app.py`).
- **Follow-up questions:** ask "And in 2026?" or "Break that down by city" and the agent uses the conversation to understand it. A line under the badges shows how a follow-up was understood.
- **Chat history:** past chats are listed in the sidebar, titled by their first question, and saved in a local SQLite file (`chat_history/`, ignored by git). Reopen one and keep asking follow-ups, even after a restart: the agent's memory of each chat is saved in the same file. "New chat" starts a fresh conversation, and a chat can be deleted.
- Each answer shows which agent handled it, plus a badge when a request was refused or a query was self-corrected.
- Live progress while the agent works: understanding the question, writing SQL, safety check, running the query.
- **Automatic charts:** a single number becomes a headline metric, a breakdown becomes a bar chart (in the query's order), and a time series becomes a line chart. Lists of records stay as a table. When a result has several numbers (e.g. total rides and cancellation rate), you can choose which one to chart.
- The result table and the exact SQL behind every answer, so answers can be checked.
- Time and token usage for each answer, and example questions on the start screen of a new chat.

### SQL Analyst
- Converts natural-language questions into PostgreSQL.
- Understands follow-ups: the last 3 exchanges are used to rewrite a message like "and in 2026?" into a standalone question, so the SQL rules and safety checks work exactly as for a single question. A follow-up that asks to change data ("delete them", "set their fares to zero") is rewritten to name the exact records and refused (16 of 16 in the latest runs).
- Gives the model a compact description of the data: columns, the exact allowed values of short text columns, foreign keys, sample rows (with emails and phone numbers hidden) and data notes.
- Follows explicit rules for row limits, filters, percentages and "has none" questions.
- Uses prompt caching: the instructions, data notes and schema are identical for every question, so they are sent as a cached block that Claude reads at a tenth of the normal input price after the first question.
- Blocks anything that isn't a single read-only query, then runs it as a read-only database user.
- Refuses requests to change data with a clear explanation instead of attempting them.
- Recovers from its own mistakes: if a query fails, or the guard can't parse it (a typo such as `SELECE`), the error goes back to Claude, which rewrites the query (up to 2 retries, each re-checked for safety).
- Checks that a successful result fits the question: if a question asks for one number ("How many users...?") but the query returns many rows, for example a `COUNT` inside a `GROUP BY`, the query is rewritten within the same retry limit. The check is rule-based, so it adds no model call when the result is fine.
- Returns results with column names and explains them in plain English. The answer step also sees the SQL, so it knows when a result is already sorted and cut to the top row and can state it directly.

### ETL Analyst
- Extracts JSON from REST APIs and saves it as CSV, JSON or Parquet.
- Transforms existing files: Claude writes the pandas code, which runs in a separate, time-limited process without access to your secrets.
- Only reads and writes inside the project's `data/` folder.
- **Loads files into PostgreSQL, with your approval:** the agent prepares a load (column types, duplicate and missing-value checks, a preview) and the app shows it with **Approve** and **Cancel** buttons. Nothing is written until you click Approve, and the agent has no way to approve on its own. Loaded tables go to a separate `external` schema; the SQL agent sees every table there as soon as it's approved (EXP-14), and `utils/drop_loaded_table.py` removes one.
- **Weather for the 8 cities (EXP-14):** one request fetches daily historical weather from Open-Meteo for every city in the ride data, turns it into one row per city per day, and prepares it as `external.weather_daily` for your approval (see [Load the weather data](#6-load-the-weather-data-optional)).

### Sample dataset

A synthetic ride-sharing company operating in eight Canadian cities:

| Table | Rows | Contents |
|---|---:|---|
| `users` | 10,000 | 7,000 riders and 3,000 drivers: city, province, signup date |
| `vehicles` | 3,000 | One vehicle per driver: make, model, year, colour |
| `rides` | 20,000 | Times, coordinates, distance, fare, surge multiplier, status, cancellation reason |
| `payments` | 16,073 | One per completed ride: amount, method, status |
| `ratings` | 12,000 | 1 to 5 stars and a comment |

All names, emails and phone numbers are fake.

---

## 4. Getting Started

### Prerequisites
- Python 3.11+ and [uv](https://docs.astral.sh/uv/) (or pip)
- PostgreSQL
- An Anthropic API key

### 1. Clone and install

```bash
git clone https://github.com/Darpanjiyani/AI-Data-Agent.git
cd AI-Data-Agent
uv sync
```

<details>
<summary>Using pip instead</summary>

```bash
python -m venv .venv
# Windows
.\.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install dotenv ipython langchain langchain-anthropic langgraph langgraph-checkpoint-sqlite pandas psycopg2-binary pydantic requests sqlglot pyarrow streamlit
```

</details>

### 2. Create the database and a read-only user

```sql
CREATE DATABASE ride_share;
```

Then, connected to `ride_share` as your admin user, create the user the agent connects with (choose your own password):

```sql
CREATE ROLE agent_reader LOGIN PASSWORD 'choose_a_strong_password';

GRANT CONNECT ON DATABASE ride_share TO agent_reader;
GRANT USAGE ON SCHEMA public TO agent_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agent_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO agent_reader;

ALTER ROLE agent_reader SET default_transaction_read_only = on;
ALTER ROLE agent_reader SET statement_timeout = '15s';

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
```

### 3. Configure `.env`

```bash
cp .env.example .env        # macOS / Linux
copy .env.example .env      # Windows
```

| Variables | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | All agents |
| `DB_HOST`, `DB_PORT`, `DB_NAME` | Database connection |
| `DB_READER_USER`, `DB_READER_PASSWORD` | The agent (read-only user) |
| `DB_WRITER_USER`, `DB_WRITER_PASSWORD` | Approved loads only (`etl_writer`, can write only to the `external` schema) |
| Lowercase `host`, `port`, `database`, `user`, `password` | Only `utils/feed_db.py` and `utils/setup_load_access.py` (admin user, setup) |

`.env` is in `.gitignore`. Never commit it.

### 4. Load the sample data (once)

```bash
uv run utils/feed_db.py
```

To let the ETL agent load files into the database (EXP-13), also run this once. It creates the `external` schema and the `etl_writer` user, which can write only there:

```bash
uv run python utils/setup_load_access.py
```

### 5. Ask questions

**Chat app (recommended):**

```bash
uv run streamlit run app.py
```

It opens in your browser at `http://localhost:8501`.

**From the command line:**

```bash
uv run main.py                     # full system (router → agent)
uv run agents/sql_analyst.py       # SQL agent only
uv run agents/etl_analyst.py       # ETL agent only
```

From Python:

```python
from agents.service import ask

reply = ask("Which 5 drivers have the highest average rating?", thread_id="chat-1")
print(reply.answer)   # plain-English answer
print(reply.sql)      # the SQL that was run
print(reply.rows)     # the result rows

# Same thread_id = same conversation, so follow-ups work
reply = ask("Which city is each of them based in?", thread_id="chat-1")
print(reply.standalone_question)  # how the follow-up was understood
```

**Example requests**
- "How many cancelled rides were there for each cancellation reason?"
- "What is the total revenue from completed payments by payment method?"
- "Extract the data from https://pokeapi.co/api/v2/pokemon and save it to data/extract as CSV."
- "Read data/extract/pokemon.csv, keep only names starting with 'c', and save it to data/transform as CSV." (The extract tool names each file after its source and follows the API's "next" links, up to 50 pages.)

### 6. Load the weather data (optional)

The weather questions (`--set weather`) and weather-and-rides answers need daily weather for the 8 cities in `external.weather_daily`. In the app, send this request (one line; it's also `LIVE_WEATHER_URL` in `evals/etl_tasks.py`):

```text
https://archive-api.open-meteo.com/v1/archive?latitude=43.65,45.50,45.42,53.55,44.65,49.28,49.90,51.05&longitude=-79.38,-73.57,-75.70,-113.49,-63.57,-123.12,-97.14,-114.07&start_date=2025-01-01&end_date=2026-07-30&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum,wind_speed_10m_max&timezone=auto has daily weather for the 8 cities in our ride data: 43.65,-79.38 is Toronto, 45.50,-73.57 is Montreal, 45.42,-75.70 is Ottawa, 53.55,-113.49 is Edmonton, 44.65,-63.57 is Halifax, 49.28,-123.12 is Vancouver, 49.90,-97.14 is Winnipeg, 51.05,-114.07 is Calgary. Keep one row per city per day with exactly these columns: city (the city name), date, temp_max_c, temp_min_c, precipitation_mm, snowfall_cm, wind_max_kmh. Then load it into the database as a table called weather_daily.
```

The ETL agent downloads 576 days for each city from [Open-Meteo](https://open-meteo.com/) (free, no API key, CC BY 4.0), shapes it into one row per city per day (4,608 rows) and prepares the load. Check the card (8 cities, `date` as a date) and click **Approve load**.

Something not working? See [docs/troubleshooting.md](docs/troubleshooting.md).

---

## 5. Safety Improvements

The original version relied on one LLM check and ran AI-written code directly. Each layer below was added and tested in [Round 1](docs/experiments.md#exp-01--safety-hardening).

| Risk | Original version | Now |
|---|---|---|
| Harmful SQL (DELETE, DROP, ...) | One LLM "judge" decided | **3 layers:** rule-based guard → LLM judge → read-only database user |
| Requests to change data | Sometimes attempted, sometimes turned into a read query | Clear refusal: "I can only read data..." |
| Runaway queries | No limit | 15-second statement timeout |
| AI-generated pandas code | `exec()` inside the app | **Code guard** ([EXP-12](docs/experiments-etl.md#exp-12-part-1--code-guard-for-generated-pandas-code)): checked before running, then run in a separate process that can only write inside `data/`, can't read `.env`, use the network or start programs, with a 60-second limit |
| File access by ETL tools | Any path | Only inside `data/` |
| Personal data sent to the LLM | Sample rows with emails and phones | Emails and phone numbers hidden |
| Secrets | `.env` | `.env` (git-ignored) plus `.env.example` template |

### SQL: three layers
1. **SQL guard** (`utils/sql_guard.py`): a `sqlglot` parser allows exactly one read-only `SELECT`. It blocks writes, DDL, multiple statements, `SELECT ... INTO`, row locks and server functions like `pg_terminate_backend`, including inside CTEs. Blocked queries never reach the LLM judge or the database.
2. **LLM judge:** a second opinion on queries that pass the guard.
3. **Read-only role:** the agent connects as `agent_reader` (SELECT only, read-only transactions, 15-second timeout). Even a query that got past both checks couldn't change anything.

In the evaluations, none of the 7 unsafe requests (delete, update, drop, create table, and "show then delete") was executed, and table row counts never changed. The eval also checks that each one gets a **clear refusal**, not just that nothing ran.

The guard fails closed: anything it can't parse, including a prose reply instead of SQL, is blocked.

### ETL: loading only with a person's approval
Loading data is split into two steps that can't be merged (`utils/loads.py`):
1. **Prepare** (the agent's `prepare_load_tool`): checks the file and plans the load, writes nothing.
2. **Approve** (people only: the app's Approve button): re-checks that the file is unchanged since the preview, then writes it in one transaction as `etl_writer`.

The ETL agent has no tool that approves, so no request ("approve it yourself") can make it load data. The database enforces the rest: `etl_writer` can create and fill tables only in the `external` schema and can't touch the ride-sharing tables, and the agent's read-only user can read `external` but not write to it. Pending loads are stored outside `data/`, so generated code can't change a plan after the preview.

### ETL: a code guard, not yet a container
The pandas code Claude writes goes through two checks (`utils/code_guard.py`):
1. **Before running:** its syntax tree is checked. Imports outside a short list (pandas, numpy, json, re, datetime, ...) and calls like `eval`, `exec`, `os.system` or `os.environ` are rejected with a message the agent can use to rewrite the code.
2. **While running:** the code runs in a separate, time-limited process with a Python audit hook that sees every file open, network connection and program start. It can write only inside `data/`, read only `data/` and Python's own libraries (so not `.env`), and can't use the network or start other programs. Each layer was tested on its own: the runtime check alone stops all 9 attempts to get around the static one.

Until EXP-12 the code could write anywhere, use the network, start programs and read the `.env` file; the safety-layer tests in `evals/etl_tasks.py` found this without involving the model. It's a guard, not a container: running generated code in a container is still on the [roadmap](#8-roadmap).

---

## 6. Evaluation Framework

`evals/` measures the agent with **59 questions** about the ride-sharing data, plus a held-out set and a set of follow-up conversations (below).

| Set | Questions | What is checked |
|---|---:|---|
| SQL | 47 | 6 simple, 12 aggregation, 8 join, 4 date, 4 tricky (ties, zero answers, easy-to-forget filters), 13 hard (medians, window functions, rates, growth) |
| Safety | 4 | Requests to change data are never executed |
| Routing | 8 | Each request goes to the right agent |

### How scoring works
- **The agent only sees the question.** Each question has a reference SQL query that acts as an answer key: the runner uses it to calculate the correct answer from the live data.
- **Answers are compared, not queries.** Any query that returns the right data passes. Column names, column order and row order don't matter; numbers are compared to 2 decimals; extra columns are allowed. "Which is the most..." questions accept a full ranking with the right answer first, and some questions accept equivalent formats (month numbers or month dates).

Every answer gets two scores:
- **Execution accuracy (strict):** the agent's result matches the correct data.
- **Answer accuracy (LLM-as-a-judge):** Claude agrees that the plain-English answer is correct.

The runner also records time and tokens per question (including how much input was read from the prompt cache, and the input cost in normal-price tokens), checks safety and routing, and confirms that no table changed.

### Running it

```bash
uv run evals/run_eval.py                     # full run
uv run evals/run_eval.py --limit 5           # quick check
uv run evals/run_eval.py --category hard     # one category
uv run evals/run_eval.py --ids s01 t01       # specific questions
uv run evals/run_eval.py --no-judge          # skip the LLM judge (cheaper)
uv run evals/run_eval.py --set holdout       # held-out questions (see below)
uv run evals/run_eval.py --set holdout2      # second held-out set (see below)
uv run evals/run_eval.py --set weather       # weather + rides questions (needs external.weather_daily)
uv run evals/run_followup_eval.py            # follow-up conversations (see below)
uv run evals/run_followup_eval.py --set fresh   # fresh follow-up conversations (EXP-07)
uv run evals/run_followup_eval.py --set check   # final check conversations (EXP-07)
uv run evals/run_etl_eval.py                 # ETL Analyst: 33 tasks + 26 tests without the model
uv run evals/run_etl_eval.py --group hard    # one group: basic, hard, layer, tool, db, load or weather
uv run evals/run_etl_eval.py --live          # ... plus two tasks against the real Open-Meteo API
```

Each run writes a Markdown report (summary, per-question results, and every failure with its generated SQL) and a JSON file to `evals/results/`.

### Held-out set
`evals/holdout_questions.json` holds 27 more questions (20 SQL, 3 safety, 4 routing) in different wording and on different topics. They were written before Round 3 and are **never used to design fixes**: they're run only to check that improvements carry over to questions the agent wasn't tuned on.

In EXP-07 one held-out question (o14) showed the same weakness as a follow-up conversation, and the general fix for it also changed o14's answer. From then on the set had helped shape the agent, so `evals/holdout2_questions.json` (20 SQL, 3 safety, 4 routing, written on 2026-10-14) replaces it as the independent check, under the same rule.

### Follow-up conversations
`evals/followup_questions.json` holds 14 short conversations (2 or 3 turns) and 3 safety conversations. They are sent turn by turn in one chat, through the router, memory and SQL agent, and only the last turn is scored, with the same execution and judge checks. They cover changing a time period or filter, narrowing a result, using a value from an earlier answer ("the top one", "there"), a topic switch where nothing should carry over, and follow-ups that ask to change data, which must be refused. The report shows how each follow-up was understood. `evals/followup_fresh_questions.json` adds 12 more conversations and 4 safety conversations, written before the EXP-07 fixes, to measure those fixes on conversations they weren't designed around. `evals/followup_check_questions.json` (6 conversations, 3 safety) was written before the second part of EXP-07 for the same reason.

### ETL tasks
`evals/etl_tasks.py` holds 27 tasks for the ETL Analyst (5 extract, 9 transform and 3 safety requests, plus 10 harder pipeline tasks such as pagination, joins, messy values and upserts), each with a short pandas reference that produces the correct output, and 10 safety-layer tests that push harmful code straight through the tools without the model (plus one control test that normal code still runs), and 6 tool tests that call the extract tool directly (pagination, Open-Meteo's layout, several locations, which requested point each location answers, file names). Since EXP-14 the output is also compared row by row, so a table with the right values on the wrong rows (one city's weather under another city's name) fails. The runner serves the test data from a local web server, sends each request to the ETL agent, and compares the file it writes with the reference: row count, expected columns and their values. See [docs/experiments-etl.md](docs/experiments-etl.md).

### Weather questions
`evals/weather_questions.json` (12 SQL, 2 safety, 3 routing) asks about the loaded weather on its own ("On how many days in 2025 did it snow in Winnipeg?") and joined with rides ("How many rides did Toronto riders request on days when it snowed in Toronto?"). Rides have no city column, so a ride's city is its rider's city, and every question says so. The set was written before the SQL agent could see any weather data, under the held-out rule.

### Keeping it honest
- The eval is only changed when an answer key or a question's wording is wrong, never to raise the score.
- Agent improvements must be general (better context, clearer rules), not special cases for these questions.
- The main set was used to find and fix failures, so the held-out score is the better estimate of accuracy on new questions.
- The LLM judge can be wrong too (1 wrong verdict in 228 so far, and none in 161 since it was changed to reason before deciding), so deterministic execution accuracy is the primary metric.

---

## 7. Performance Evolution

| Version | Changes | Execution accuracy | Answer accuracy | Input tokens / question | Time / question |
|---|---|:---:|:---:|:---:|:---:|
| v1.0 | Original multi-agent system | not measured (crashed) | – | – | – |
| v1.1 | Round 1: safety hardening and bug fixes | not measured | – | – | – |
| v1.2 | Evaluation baseline (47 SQL questions) | **89.4%** | **89.4%** | 4,959 | 6.5 s |
| v2.0 | Round 2: schema context and SQL rules | **100%** | **100%** | 4,353 (−12%) | 6.3 s |
| v2.0 | Same version on the **held-out set** (20 unseen questions) | **100%** | 95% | 4,335 | 5.5 s |
| v2.1 | Round 3: clear refusals and self-correcting SQL | **99.3%** (avg of 3 runs) | **99.3%** | 4,432 (+2%) | 5.6 s |
| v2.1 | Same version on the **held-out set** | **100%** | **100%** | 4,413 | 5.6 s |
| v2.2 | Round 5: conversation memory, **follow-up conversations** (14, 3 runs) | **100%** | 92.9% | 5,509 per follow-up turn | 8.1 s |
| v2.2 | Same version, main set (regression check, 1 run) | 97.9% | 97.9% | 4,403 | 6.3 s |
| v2.3 | Round 6: clear refusals for vague follow-ups, confident top-1 answers, retry on invalid SQL (**check set**, 6 new conversations, 3 runs) | **100%** | **100%** | 5,792 per follow-up turn | 6.7 s |
| v2.3 | Same version, main set | **100%** | **100%** | 4,568 | 5.6 s |
| v2.3 | Same version, held-out set | **100%** | **100%** | 4,546 | 5.5 s |
| v2.4 | Round 7: prompt caching for the SQL context (main set) | 97.9%¹ | 97.9% | 4,582 (**1,861 billed, −59%**) | 6.2 s |
| v2.4 | Same version, held-out set | **100%** | **100%** | 4,554 (**1,936 billed, −57%**) | 5.5 s |
| v2.5 | Round 8: result sanity check (main set) | **100%²** | **100%** | 4,671 (**1,815 billed**) | 6.2 s |
| v2.5 | Same version, held-out set | **100%** | **100%** | 4,562 (**1,765 billed**) | 5.8 s |
| v2.5 | Same version, **second held-out set** (20 questions written after the last change) | **100%** | **100%** | 4,544 (**1,925 billed**) | 6.0 s |
| v2.6 | EXP-14: reads loaded tables; **weather + rides set** (12 questions written before the agent saw the data; 0/12 before) | **91.7%** (11/12)³ | 91.7% | 5,122 (2,299 billed) | 6.6 s |
| v2.6 | Same version, main set (regression check) | **100%** | **100%** | 5,104 (**1,878 billed**) | 5.9 s |

¹ The one miss is h06, the same occasional mistake seen before caching; see [EXP-08](docs/experiments.md#exp-08--prompt-caching). Billed input = uncached input + 0.1 × cache reads + 1.25 × cache writes.
² h06 still writes the wrong-shape query sometimes (2 of 6 runs in this round), but the sanity check now catches it and the rewritten query is correct; h06 passed 5/5 when run alone. See [EXP-09](docs/experiments.md#exp-09--result-sanity-check).
³ 10/12 as first scored: wq06 was right but returned a midnight timestamp with a time zone, which the scorer now accepts. The miss, wq12, totalled completed payments by drop-off date instead of ride fares by request date.

Results by category, notes on each number, and the caveats are in [docs/experiments.md](docs/experiments.md#results-by-version); every round has its own write-up there.

**ETL Analyst** (`uv run evals/run_etl_eval.py`; details in [docs/experiments-etl.md](docs/experiments-etl.md))

| Version | Changes | Tasks passed (27 + live) | Safety layers | Tool tests | Pipeline tool calls / time |
|---|---|:---:|:---:|:---:|:---:|
| EXP-11 | Evaluation and baseline, agent unchanged | 28/28¹ | 4/9 | – | 2.4 calls, 17.1 s |
| EXP-12 part 1 | Code guard for generated pandas code | 26/27 | **10/10** | 0/5 | – |
| EXP-12 part 2 | Smarter extract tool: pagination, Open-Meteo layout, file names | 27/28 | **10/10** | **5/5** | h03 1 call (was failing), weather 1 call (was 3) |
| EXP-13 | Loading into PostgreSQL with a person's approval | 31/31² | 10/10 | 5/5 | database checks **10/10** (was 4/10); load tasks **4/4** |
| EXP-14 | Weather for the 8 cities; output compared row by row; tools report the files really written | 34/35⁴ | 10/10 | **6/6** (was 5/6) | real load approved, 4,608 rows; live 8-city task 4,608/4,608 rows |

¹ The 18 basic and live tasks and the 10 pipeline tasks, run separately; one pipeline result was a scorer bug (−0.0 vs 0.0), fixed and re-scored.
² The 27 earlier tasks plus the 4 new load tasks, in one run without the live task (all 56 tasks, including the tests without a model: 56/56).
⁴ The 27 earlier tasks, 4 load tasks, 2 weather tasks and 2 live tasks in one run (60/61 with the 26 tests without a model). The miss, w01, had the right content but saved it one folder up (`output/w01.csv`).

---

## 8. Roadmap

**Safety**
- [x] Read-only database user with a query timeout
- [x] Rule-based SQL validation before the LLM judge
- [x] Isolated, time-limited execution for generated ETL code
- [x] Code guard for generated ETL code: static check plus runtime limits (EXP-12)
- [x] Load data into PostgreSQL only after a person approves it, as a user that can write only to its own schema (EXP-13)
- [x] Daily weather for the 8 cities in one request, each location tied to the point it was asked for (EXP-14 part 1)
- [x] SQL agent answers questions that join loaded weather with rides: 11/12, was 0/12 (EXP-14 part 2)
- [x] Personal columns hidden from the LLM
- [ ] Run generated ETL code in a container
- [x] Clear refusals for vague follow-up change requests ("remove those drivers")

**Accuracy**
- [x] Query results with column names
- [x] Evaluation set with reference SQL and LLM-as-a-judge
- [x] Schema context: agent tables only, allowed values, foreign keys, data notes
- [x] SQL rules: row limits, filters, denominators, "has none" questions
- [x] Clear, consistent refusals for requests to change data
- [x] Self-correcting SQL: retry with the database error
- [x] Conversation memory with a LangGraph checkpointer
- [x] Held-out evaluation questions
- [x] A fresh held-out set for single questions (`holdout2_questions.json`: 20/20, EXP-10)
- [x] Repeated eval runs to measure run-to-run variance
- [x] Result sanity checks (e.g. a "how many" question should return one row)
- [x] Retry when the guard finds invalid SQL, not only on database errors
- [x] Show the SQL to the answer step, so a top-1 result is stated confidently

**Cost and speed**
- [x] Schema built once per process; SQL prompt 41% smaller
- [x] Prompt caching for the schema context
- [ ] Compare a decision model (e.g. Jev) with Sonnet for routing

**Features**
- [x] Streamlit chat interface with the answer, generated SQL and result table
- [x] Saved chat history in the sidebar, with New chat and delete
- [x] Automatic charts for query results
- [ ] Pagination and authentication for API extraction

---

## 9. Future Work

- **Continuous evaluation:** run the eval in GitHub Actions against a test database on every pull request, and fail the build if accuracy drops.
- **Observability:** trace every step, prompt and token count with LangSmith to debug wrong answers quickly.
- **Full sandboxing:** run generated code in a container with no network and a read-only filesystem.
- **More approval-gated actions:** loads already need a person's approval (EXP-13); the same prepare-then-approve pattern could cover other actions that change data.
- **More data sources:** support MySQL, SQLite and Snowflake through SQLAlchemy, plus user-uploaded files.

**Current limitations**
- Generated ETL code is isolated but not fully sandboxed.
- Follow-ups use the last 3 exchanges; something mentioned earlier than that is forgotten.
- Answers can vary between runs: Claude Sonnet 5 thinks by default and doesn't accept a custom `temperature`, so variance is reduced with explicit rules rather than sampling settings.
- The result check only covers questions phrased as asking for one number; a query that runs but answers a different question in some other way isn't caught.
- A request that mixes reading and changing data ("show the cancelled rides, then delete them") is refused as a whole, rather than answering only the read part.

---

## 10. Project Structure

```
AI-Data-Agent/
├── agents/
│   ├── data_agent.py        # Router
│   ├── sql_analyst.py       # Question → SQL → answer pipeline
│   ├── etl_analyst.py       # Tool-calling agent for extract/transform
│   └── service.py           # ask(question, thread_id): answer, SQL, rows, steps
├── Models/
│   └── schema.py            # Pydantic state models
├── utils/
│   ├── database.py          # Read-only connection, schema context, query runner
│   ├── schema_notes.py      # Agent tables and data notes for the LLM
│   ├── sql_guard.py         # Rule-based read-only check
│   ├── charts.py            # Picks a chart (metric, bar, line or none) for a result
│   ├── usage.py             # Token usage, cache reads/writes and billed input tokens
│   ├── result_checks.py     # Sanity check: does a successful result fit the question?
│   ├── code_guard.py        # Checks and limits for the pandas code the ETL agent writes
│   ├── loads.py             # Prepare a load (agent) and approve it (people only)
│   ├── setup_load_access.py # One-time setup: external schema and the etl_writer user
│   ├── drop_loaded_table.py # Remove a loaded table from the external schema (asks to confirm)
│   ├── chat_store.py        # Saved chats for the app (SQLite)
│   ├── etl_tools.py         # API extraction, file reading, isolated code execution
│   ├── feed_db.py           # Creates tables and loads the dataset
│   └── llm_pick.py          # Model per step
├── evals/
│   ├── questions.json       # Evaluation questions with reference SQL
│   ├── holdout_questions.json  # Held-out questions, never used to design fixes
│   ├── holdout2_questions.json # Second held-out set, written 2026-10-14
│   ├── weather_questions.json  # Weather + rides questions, written before the agent saw the data (EXP-14)
│   ├── followup_questions.json # Multi-turn conversations for conversation memory
│   ├── followup_fresh_questions.json # Fresh conversations, written before the EXP-07 fixes
│   ├── followup_check_questions.json # Final check conversations for EXP-07 part 2
│   ├── run_followup_eval.py # Scores follow-up conversations
│   ├── run_eval.py          # Scores the agent and writes reports
│   ├── etl_tasks.py         # ETL tasks with pandas reference outputs
│   ├── run_etl_eval.py      # Scores the ETL agent's output files
│   ├── etl_fixtures/        # Small test files and API responses for the ETL tasks
│   ├── load_checks.py       # Database-layer checks for loading (no model)
│   └── results/             # Reports from each run
├── docs/
│   ├── experiments.md       # Experiment log: every experiment, SQL details
│   ├── experiments-etl.md   # ETL Analyst experiments (EXP-11 onwards)
│   ├── troubleshooting.md   # Common errors and fixes
│   └── demo.gif             # Demo of the chat app
├── data/                    # Dataset and ETL outputs
├── app.py                   # Streamlit chat app
├── main.py                  # Example request through the router
├── .env.example             # Template for .env
├── pyproject.toml
└── uv.lock
```

**Tech stack:** LangGraph, LangChain, Claude (Haiku, Sonnet), PostgreSQL (`psycopg2`), `sqlglot`, pandas, Pydantic, Streamlit, Altair, uv.

---

Built by [Darpanjiyani](https://github.com/Darpanjiyani)
