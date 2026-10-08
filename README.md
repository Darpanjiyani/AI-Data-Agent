# 🤖 AI Data Agent

Ask questions about your data in plain English, and let a team of AI agents answer them.

AI Data Agent is a multi-agent system built with **LangGraph** and **Claude**. A router agent reads your request and sends it to one of two specialists:

- **SQL Analyst**: turns a question like *"Which payment methods do riders use most?"* into a PostgreSQL query, runs it, and explains the result in plain language.
- **ETL Analyst**: extracts data from REST APIs and transforms CSV/JSON files using pandas, saving the output as CSV, JSON, or Parquet.

The project ships with a sample ride-sharing dataset (users, vehicles, rides, payments, ratings) so you can try it end to end.

> **Status:** v1, a working learning/portfolio project. See [Known limitations](#-known-limitations) and [Roadmap](#-roadmap) before using it with real or sensitive data.

---

## 🏗️ How it works

```
                     ┌──────────────────────────────┐
   Your question ──► │     Data Agent (router)      │
                     │   Classifies: "sql" or "etl" │
                     └──────────────┬───────────────┘
                                    │
                ┌───────────────────┴───────────────────┐
                ▼                                       ▼
     ┌─────────────────────┐                 ┌─────────────────────┐
     │     SQL Analyst     │                 │     ETL Analyst     │
     │ (fixed pipeline)    │                 │ (tool-using agent)  │
     └─────────────────────┘                 └─────────────────────┘
       1. Rewrite question                     Claude picks a tool and
       2. Read DB schema + samples             loops until the task is done:
       3. Generate SQL                         • extract_load_tool
       4. SQL guard + LLM safety judge         • transform_load_tool
       5. Execute as read-only user
       6. Explain the answer
```

### Data Agent (router): `agents/data_agent.py`
Claude reads your latest message and returns a structured decision, either `sql` or `etl` (enforced by the `RouterSchema` Pydantic model). The request is then passed to the matching sub-agent.

### SQL Analyst: `agents/sql_analyst.py`
A fixed LangGraph pipeline:

1. **Curate question**: a fast model rewrites the question more clearly.
2. **Build context**: reads every table's columns, data types, and 5 sample rows from PostgreSQL.
3. **Generate SQL**: Claude writes a PostgreSQL query (limited to 10 rows unless you ask for more).
4. **Safety checks**: a rule-based SQL guard (`sqlglot`) allows only a single read-only `SELECT`; queries that pass are then reviewed by a Claude "judge" as a second opinion.
5. **Execute or cancel**: approved queries run through a read-only PostgreSQL user; rejected ones stop with an explanation.
6. **Final answer**: the result (with column names) is turned into a short, plain-English answer.

![SQL Analyst workflow](sql_analyst_graph.png)

### ETL Analyst: `agents/etl_analyst.py`
A tool-calling agent. Claude decides which tool to call, sees the result, and continues until the task is complete.

- **`extract_load_tool`**: calls an API URL, flattens the JSON (the `results` list if there is one) with `pandas.json_normalize`, and saves it as `extracted_data.<format>` in a folder inside `data/`.
- **`transform_load_tool`**: shows Claude the first 3 rows of a CSV/JSON/Parquet file, asks it to write pandas code for your request, then runs that code in a separate process with a time limit to save the transformed output.

Both tools only accept file paths inside the project's `data/` folder.

![ETL Analyst workflow](etl_analyst_graph.png)

---

## 📊 Sample dataset

The `data/` folder contains a synthetic ride-sharing dataset for a company operating in Canadian cities (Halifax, Vancouver, Winnipeg, Montreal, and others).

| Table | Rows | What it contains |
|---|---:|---|
| `users` | 10,000 | 7,000 riders and 3,000 drivers: name, email, phone, city, province, signup date |
| `vehicles` | 3,000 | Each driver's car: make, model, year, colour, licence plate |
| `rides` | 20,000 | Pickup/drop-off times and coordinates, distance, fare, surge multiplier, status, cancellation reason |
| `payments` | 16,073 | One per completed ride: amount, method (card, PayPal, Apple Pay, Google Pay), status |
| `ratings` | 12,000 | 1–5 star rating and comment for a ride |

Tables are linked by IDs (for example, `rides.driver_id → users.user_id`, `payments.ride_id → rides.ride_id`). `utils/feed_db.py` creates the tables with primary keys, foreign keys, a 1–5 rating check, and indexes, then bulk-loads the CSVs with PostgreSQL `COPY`.

All names, emails, and phone numbers are fake (emails use `@example.com`).

---

## 📦 Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** (recommended) or pip
- **PostgreSQL** running locally or remotely
- An **Anthropic API key** ([console.anthropic.com](https://console.anthropic.com/))
- An internet connection (for the Claude API, API extraction, and graph image rendering)

---

## 🚀 Setup

### 1. Clone the repository

```bash
git clone https://github.com/Darpanjiyani/AI-Data-Agent.git
cd AI-Data-Agent
```

### 2. Install dependencies

With uv (uses `pyproject.toml` and `uv.lock`):

```bash
uv sync
```

Or with pip:

```bash
python -m venv .venv
# Windows
.\.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install dotenv ipython langchain langchain-anthropic langgraph pandas psycopg2-binary pydantic requests sqlglot pyarrow
```

### 3. Create the database

In psql or pgAdmin:

```sql
CREATE DATABASE ride_share;
```

### 4. Create a read-only database user

The agent never uses your admin account. Connected as your admin user (for example `postgres`), run this in psql or pgAdmin, choosing your own password:

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

Run this while connected to the `ride_share` database (the `GRANT ... SCHEMA public` lines apply to the database you're connected to). It can be run before or after loading the data in step 6.

### 5. Create a `.env` file

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env        # macOS / Linux
copy .env.example .env      # Windows
```

| Variables | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | All agents |
| `DB_HOST`, `DB_PORT`, `DB_NAME` | Database connection |
| `DB_READER_USER`, `DB_READER_PASSWORD` | The agent (read-only user from step 4) |
| Lowercase `host`, `port`, `database`, `user`, `password` | Only `utils/feed_db.py` (admin user, to create tables and load data) |

`.env` is listed in `.gitignore`. Never commit it or share it.

### 6. Load the sample data

Run this **once** from the project root:

```bash
uv run utils/feed_db.py
# or: python utils/feed_db.py
```

It prints the record count for each table when finished. Running it a second time will fail on duplicate primary keys; to reload from scratch, uncomment the `TRUNCATE` block in `utils/feed_db.py`.

---

## 💻 Usage

Run all commands from the project root.

### Run the full system

```bash
uv run main.py
```

`main.py` sends one example request (extracting Pokémon data from the PokéAPI) to the router. Edit the message in `main.py` to ask your own question.

### Use it from Python

```python
from agents.data_agent import data_agent
from langchain_core.messages import HumanMessage

response = data_agent.invoke({
    "messages": [HumanMessage(content="Which 5 drivers have the highest average rating?")],
    "route_response": "",
})

print(response["messages"][-1])
```

### Run a single agent

```bash
uv run agents/sql_analyst.py   # asks about payment methods, prints SQL + result
uv run agents/etl_analyst.py   # extracts PokéAPI data to data/extract/
```

Running an agent file directly also regenerates its workflow diagram (`*_graph.png`).

---

## 📚 Example requests

**SQL (answered from the database)**
- "What are the different payment methods in our database?"
- "How many rides were cancelled, grouped by cancellation reason?"
- "Which 5 drivers have the highest average rating?"
- "What is the total revenue from completed payments by payment method?"

**ETL: extract from an API**
- "Extract the data from https://pokeapi.co/api/v2/pokemon and save it to data/extract as CSV."

**ETL: transform a file**
- "Read data/extract/extracted_data.csv, keep only Pokémon whose name starts with 'c', and save the result to data/transform as CSV."

---

## 📁 Project structure

```
AI-Data-Agent/
├── agents/
│   ├── data_agent.py        # Router: sends requests to the SQL or ETL agent
│   ├── sql_analyst.py       # Natural language → SQL → answer pipeline
│   └── etl_analyst.py       # Tool-calling agent for extract/transform tasks
├── Models/
│   └── schema.py            # Pydantic state models (AgentSchema, ETLAgentSchema, RouterSchema, ...)
├── utils/
│   ├── database.py          # PostgreSQL connection, schema reader, query runner
│   ├── etl_tools.py         # API extraction, file reading, isolated code execution
│   ├── sql_guard.py         # Rule-based check: only one read-only SELECT allowed
│   ├── feed_db.py           # Creates tables and loads the CSV dataset
│   └── llm_pick.py          # Chooses the Claude model for each step
├── data/
│   ├── extract/             # Output of API extractions
│   ├── users.csv
│   ├── vehicles.csv
│   ├── rides.csv
│   ├── payments.csv
│   └── ratings.csv
├── main.py                  # Entry point with an example request
├── .env.example             # Template for your .env file
├── data_agent_graph.png     # Router workflow diagram
├── sql_analyst_graph.png    # SQL agent workflow diagram
├── etl_analyst_graph.png    # ETL agent workflow diagram
├── test_schema_details.txt  # Example of the schema context sent to the model
├── pyproject.toml
└── uv.lock
```

---

## ⚙️ Model selection

`utils/llm_pick.py` maps each step to a Claude model, using a smaller, faster model for simple steps and a stronger model for harder ones:

| Level | Model configured | Used for |
|---|---|---|
| `low` | Claude Haiku | Rewriting the question, writing the final answer |
| `medium` | Claude Sonnet | Generating SQL, safety judge |
| `claude` | Claude Sonnet | Router, ETL agent, pandas code generation |
| `high` | Claude Opus | Defined but not used yet |

Model names are set in `utils/llm_pick.py`. Change them there to use different models.

---

## 🔐 Safety

**SQL Analyst: three layers of protection**
1. **SQL guard** (`utils/sql_guard.py`): a `sqlglot` parser allows only a single read-only `SELECT`. It blocks writes, DDL, multiple statements, `SELECT ... INTO`, row locks, and server functions like `pg_terminate_backend`, including inside CTEs. Blocked queries never reach the LLM judge or the database.
2. **LLM judge**: queries that pass the guard are reviewed by Claude as a second opinion.
3. **Read-only database user**: the agent connects as `agent_reader`, which only has `SELECT` permission, read-only transactions, and a 15-second query timeout. Even if a query got past both checks, PostgreSQL would refuse to change anything.

Results are limited to 10 rows by default.

**ETL Analyst**
- AI-generated pandas code runs in a **separate Python process** with a 60-second time limit, so it can't crash or freeze the app.
- That process does not receive your API key or database passwords.
- Both ETL tools only accept file paths inside the project's `data/` folder.
- ⚠️ This is isolation, not a full sandbox: the generated code can still read and write files your user account can access. Use it on your own machine with trusted requests.

**Credentials**
- API keys and database passwords are read from `.env`, which is excluded from Git. `.env.example` shows the variables without values.

**Data privacy**
- Sample rows from each table are sent to the Claude API as context. That's fine for this synthetic dataset; mask sensitive columns before using real customer data.

---

## 🚧 Known limitations

- Generated ETL code is isolated in a separate process but not fully sandboxed (see Safety).
- The router only sees the latest message, so follow-up questions ("now group that by city") don't have context yet.
- The extract tool fetches only the first page of paginated APIs and doesn't support authentication yet.
- A typical SQL question uses about five Claude calls (router, rewrite, generate, judge, answer).

---

## 🗺️ Roadmap

**Safety**
- [x] Read-only database user with a query timeout
- [x] Rule-based SQL validation (single `SELECT` statement only) before the LLM judge
- [x] Run generated ETL code in an isolated process with a time limit
- [ ] Run generated ETL code in a container for full sandboxing

**Accuracy**
- [x] Return column names with query results
- [ ] Evaluation set of questions with known answers
- [ ] Self-correcting SQL: retry with the error message when a query fails
- [ ] Add foreign keys and a business glossary to the schema context
- [ ] Conversation memory with a LangGraph checkpointer

**Features**
- [ ] Streamlit chat interface showing the answer, generated SQL, and result table
- [ ] Automatic charts for query results
- [ ] Load transformed data into PostgreSQL (with human approval)
- [ ] Pagination and authentication support for API extraction

---

## 🛠️ Tech stack

| Area | Tools |
|---|---|
| Agent orchestration | LangGraph |
| LLM framework | LangChain, `langchain-anthropic` |
| Models | Claude (Haiku, Sonnet) |
| Database | PostgreSQL, `psycopg2` |
| Data processing | pandas |
| Validation | Pydantic |
| Config | python-dotenv |
| Packaging | uv |

---

## 🧩 Extending the system

**Add a new agent**
1. Create a file in `agents/` and build its LangGraph workflow.
2. Add its state model to `Models/schema.py`.
3. Add a new option to `RouterSchema.answer` and a matching node and route in `agents/data_agent.py`.

**Add an ETL tool**
Define a function with the `@tool` decorator in `agents/etl_analyst.py` and add it to the `tools` list.

---

## 🚨 Troubleshooting

| Problem | Solution |
|---|---|
| `Error connecting to the database` | Check PostgreSQL is running and the `DB_*` values in `.env` are correct |
| `DB_READER_USER and DB_READER_PASSWORD must be set` | Create the read-only user (Setup step 4) and add both values to `.env` |
| `permission denied for table ...` | Run the `GRANT SELECT ...` lines from Setup step 4 while connected to your database |
| `KeyError: 'host'` when loading data | Add the lowercase `host`, `port`, `database`, `user`, `password` entries to `.env` |
| Authentication error from Anthropic | Check `ANTHROPIC_API_KEY` in `.env` |
| Duplicate key error when loading data | Data is already loaded; uncomment the `TRUNCATE` block to reload |
| `ModuleNotFoundError` | Run commands from the project root, using `uv run` or an activated virtual environment |
| Query rejected as unsafe | Rephrase as a read-only question; the agent only runs `SELECT` queries |
| `Path must be inside the data/ folder` | Use input and output paths under `data/`, for example `data/extract/` |
| `ran longer than 60 seconds and was stopped` | Split the transformation into smaller steps, or raise `CODE_TIMEOUT_SECONDS` in `utils/etl_tools.py` |

---

## 📚 Learn more

- [LangGraph documentation](https://langchain-ai.github.io/langgraph/)
- [LangChain documentation](https://python.langchain.com/)
- [Claude API documentation](https://docs.anthropic.com/)
- [PostgreSQL documentation](https://www.postgresql.org/docs/)

---

Built by [Darpanjiyani](https://github.com/Darpanjiyani)
