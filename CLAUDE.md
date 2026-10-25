# CLAUDE.md

Context for Claude Code working in this repo. Keep it short and current; details live in the README and `docs/`.

## What this project is

**AI Data Agent**: a LangGraph + Claude multi-agent system that answers plain-English questions about a PostgreSQL database, safely. Portfolio project by Darpan (GitHub: Darpanjiyani/AI-Data-Agent). Every change is measured with evals and written up as a numbered experiment.

- **Router** (`agents/data_agent.py`) classifies each message as `sql` or `etl`, with conversation memory (LangGraph checkpointer, keyed by `thread_id`).
- **SQL Analyst** (`agents/sql_analyst.py`): fixed LangGraph pipeline
  `curate_ques → prompt_query_context → generate_sql → is_safe_sql → execute_sql → check_result → represent_final_answer`, with `fix_sql` (max 2 retries, back through `is_safe_sql`) and `canceled_sql` for refusals.
- **ETL Analyst** (`agents/etl_analyst.py`): ReAct tool loop with `extract_load_tool`, `transform_load_tool`, `prepare_load_tool`.
- **Service** (`agents/service.py`): `ask(question, thread_id)` → `AgentReply` (answer, SQL, rows, refusal, retries, tokens, pending loads). Every interface goes through this.
- **App**: Streamlit chat (`app.py`) with charts, SQL view, saved chats, and Approve/Cancel cards for loads.

Dataset: synthetic ride-sharing company, 8 Canadian cities. Tables `users`, `vehicles`, `rides`, `payments`, `ratings` in `public`; approved loads (e.g. `external.weather_daily`, 4,608 rows) in the `external` schema.

## Environment and commands

Dev machine is **Windows** (PowerShell, VS Code). Python 3.11, managed with **uv**. Always run from the project root with `uv run`.

```bash
uv sync                                   # install
uv run streamlit run app.py               # chat app (localhost:8501)
uv run main.py                            # one request through the router
uv run agents/sql_analyst.py              # SQL agent alone
uv run agents/etl_analyst.py              # ETL agent alone
uv run utils/feed_db.py                   # load sample data (once)
uv run python utils/setup_load_access.py  # external schema + etl_writer (once)
uv run python utils/drop_loaded_table.py  # remove a loaded table (asks to confirm)
```

Evals (each writes a Markdown + JSON report to `evals/results/`):

```bash
uv run evals/run_eval.py                       # main SQL set (47 + 4 safety + 8 routing)
uv run evals/run_eval.py --set holdout2        # current independent held-out set
uv run evals/run_eval.py --set weather         # weather + rides (needs external.weather_daily)
uv run evals/run_eval.py --ids h06 --no-judge  # quick, cheap targeted check
uv run evals/run_eval.py --category join       # one category: simple, aggregation, join, date, tricky, hard, safety, routing
uv run evals/run_eval.py --skip-safety --skip-routing  # SQL questions only (run_followup_eval.py has --skip-safety too)
uv run evals/run_followup_eval.py --set check  # follow-up conversations (also: followup, fresh)
uv run evals/run_etl_eval.py                   # ETL tasks + no-model tests
uv run evals/run_etl_eval.py --group layer     # groups: basic, hard, layer, tool, db, load, weather
uv run evals/run_etl_eval.py --reference       # no model: checks the scorer
uv run evals/run_etl_eval.py --rescore         # re-score last run's files after a scorer fix
uv run evals/run_etl_eval.py --live            # adds real Open-Meteo tasks
```

Evals cost API tokens. Prefer `--ids`, `--limit`, `--category`, `--group` and `--no-judge` while iterating; run full sets only to measure an experiment.

`.env` holds secrets (see `.env.example`). Never read it into output, print it, or commit it. `DB_HOST/DB_PORT/DB_NAME` are shared by the agent's two users; `DB_READER_USER/PASSWORD` = `agent_reader` (read-only), `DB_WRITER_USER/PASSWORD` = `etl_writer` (approved loads only), lowercase `host/user/password...` = admin, used only by setup scripts.

## Safety model: do not weaken

These are the core of the project. Any change touching them needs a safety eval run before and after.

- **SQL, 3 layers:** `utils/sql_guard.py` (sqlglot, exactly one read-only `SELECT`, fails closed) → LLM safety judge → connection as `agent_reader` (read-only transactions, 15 s timeout).
- **Change requests** get the `WRITE_REQUEST` marker (SQL rule 8) and a clear refusal, including vague follow-ups ("delete them"). Mixed read+write requests are refused as a whole (deliberate trade-off).
- **ETL code:** `utils/code_guard.py` static AST check (allow-listed imports, blocked calls) + runtime audit hook in a separate process: write/read only inside `data/`, no network, no subprocesses, no new native libraries, 60 s limit (`CODE_TIMEOUT_SECONDS`). pandas/numpy/pyarrow are pre-imported before the hook (needed on Windows).
- **Loads:** prepare (agent, writes nothing) → approve (people only, app button; re-checks file SHA-256). The agent must never get an approve tool. Pending loads live in `agent_state/`, outside `data/`. `etl_writer` can write only to `external`.
- PII: emails and phone numbers are hidden from sample rows sent to the LLM.

## Conventions

- **LangGraph nodes return only the fields they change.** Returning full state doubles messages (the list uses an `add` reducer). This bug happened once.
- **Read model replies with `.text`**, not `.content`: Sonnet 5 thinks by default and may return content blocks.
- **No `temperature` on Sonnet 5 / Opus 5**: it returns a 400. Only Haiku sets it. Reduce variance with explicit rules instead.
- Model tiers in `utils/llm_pick.py`: `low` = Haiku 4.5 (standalone rewrite, final answer), `medium` = Sonnet 5 (follow-up rewrite, SQL, judges), `claude` = Sonnet 5 (router, ETL), `high` = Opus 5 (defined, unused).
- SQL context (rules + data notes + schema) is one **prompt-cached system block**; keep it byte-identical across questions or caching breaks. `fix_sql` reuses it.
- `utils/schema_notes.py` `DATA_NOTES` describe the data itself, never specific eval questions.
- Rewrite and answer steps reply in the language of the user's message.
- Schema is built once per process; `service.py` rebuilds it after a load is approved.
- Plain, simple English in docs, comments and user-facing messages. Short sentences, no jargon where a plain word works.

## How work is done here (the experiment discipline)

1. **Measure before changing.** Write tests/questions first, run them on the current code for a baseline.
2. **Fixes must be general** (better context, rules, tools), never special cases for an eval question.
3. **Evals are only changed to fix a wrong answer key or scorer bug**, never to raise a score. Say so explicitly when it happens.
4. **Held-out sets are never used to design fixes.** `holdout2_questions.json` is the current independent set. Once a set shapes a fix, it loses independence; write a new one.
5. Pair every "bad thing is blocked" test with a "normal thing still works" control, and test safety layers on Windows (the platform they run on).
6. Re-run the main set as a regression check after any change that affects single questions; report variance honestly (multiple runs when it matters).
7. Write up each change in `docs/experiments.md` (SQL, EXP-01 to EXP-10) or `docs/experiments-etl.md` (ETL, EXP-11+), using the template at the end of `experiments.md`, and add a row to the summary table there. Update the README tables/roadmap if numbers change. **Next experiment is EXP-15.**

## Current state (as of EXP-14, 2026-10-23)

- SQL main set 47/47 (100%), held-out 2 20/20, follow-up sets 100%, weather set 11/12; all change requests refused.
- ETL full run 60/61 with `--live`; safety layers 10/10, tool tests 6/6, database checks 10/10.
- Billed input ~1,878 tokens per SQL question (prompt caching, about −59%).

**Open issues**
- **w01:** ETL agent sometimes saves to the parent folder (`output/w01.csv` instead of `output/w01/…`). The instruction rule didn't fix it. Candidate general fix: let the transform tool take an exact output file path.
- **wq12:** "total fare" read as revenue from payments, matched on drop-off date. Question left as written.
- **h06:** still writes a wrong-shape `COUNT ... GROUP BY` sometimes; the result sanity check (`utils/result_checks.py`) repairs it. The rule is phrase-based and misses some single-value questions.
- Questions the data can't answer get prose that the guard blocks, instead of a clear "the data doesn't have that". Candidate experiment, with its own questions written first.
- Roadmap: run generated ETL code in a container; continuous eval in CI; LangSmith tracing; pagination auth for APIs.

## Project layout

```
agents/     data_agent.py (router), sql_analyst.py, etl_analyst.py, service.py
Models/     schema.py (Pydantic state + router/judge schemas)
utils/      database.py, schema_notes.py, sql_guard.py, code_guard.py, etl_tools.py,
            loads.py, result_checks.py, usage.py, charts.py, chat_store.py, llm_pick.py,
            feed_db.py, setup_load_access.py, drop_loaded_table.py
evals/      questions.json, holdout*.json, followup*.json, weather_questions.json,
            run_eval.py, run_followup_eval.py, run_etl_eval.py, etl_tasks.py,
            load_checks.py, etl_fixtures/, results/
docs/       experiments.md, experiments-etl.md, troubleshooting.md, demo.gif
data/       dataset CSVs and ETL outputs (ETL tools may only touch this folder)
```

Git-ignored and never committed: `.env`, `.venv`, `chat_history/`, `agent_state/`, `data/etl_eval/`.
