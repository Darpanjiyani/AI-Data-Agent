# Experiment Log

Every change to the agent is recorded here: what was changed, why, how it was measured, and what happened. Results come from `evals/run_eval.py` unless stated otherwise.

| ID | Date | Change | Execution accuracy | Answer accuracy | Input tokens / question |
|---|---|---|:---:|:---:|:---:|
| [EXP-01](#exp-01--safety-hardening) | 2026-10-08 | Safety hardening and bug fixes | functional tests | – | – |
| [EXP-02](#exp-02--evaluation-framework-and-baseline) | 2026-10-08 | Evaluation framework and baseline | **89.4%** (42/47) | **89.4%** | 4,959 |
| [EXP-03](#exp-03--schema-context-and-sql-rules) | 2026-10-08 | Schema context and SQL rules | *pending* | *pending* | *pending* |

---

## EXP-01 – Safety hardening

**Goal:** make it impossible for the agent to change the database, and stop AI-generated code from affecting the app.

**Starting point**
- One LLM call decided whether generated SQL was safe.
- The agent connected with the database admin account.
- Generated pandas code ran with `exec()` inside the main process, with no time limit.

**Changes**

| Change | File |
|---|---|
| Read-only PostgreSQL role `agent_reader` (SELECT only, read-only transactions, 15 s timeout) | database setup, `utils/database.py` |
| Rule-based SQL guard with `sqlglot`: exactly one read-only `SELECT` | `utils/sql_guard.py` |
| Guard runs before the LLM judge; blocked queries skip the judge call | `agents/sql_analyst.py` |
| Generated code runs in a separate process: 60 s limit, no API keys or passwords | `utils/etl_tools.py` |
| ETL file paths restricted to `data/` | `utils/etl_tools.py`, `agents/etl_analyst.py` |
| `.env.example` template | `.env.example` |

**Bugs found and fixed along the way**

| Bug | Effect | Fix |
|---|---|---|
| Invalid `reasoning_effort: "none"` in model settings | SQL agent crashed on every question | Removed the setting |
| Every LangGraph node returned the full state into an `add` reducer | Messages doubled at each step (33 instead of 2); ETL prompts grew ~4x per loop | Nodes return only the fields they change |
| Query results had no column names | The answer step couldn't tell what values meant | Results returned as JSON with column names |
| Code cleanup used `lstrip('python')` | Removed leading letters from generated code (e.g. `pd` → `d`) | Regex-based code-fence extraction |
| Question rewrite produced essays with "alternative phrasings" | SQL answered a broadened question | Prompt asks for one rewritten question only |

**Results (functional tests)**
- 23/23 guard unit tests passed, including `DELETE` hidden inside a `WITH` clause, multiple statements, `SELECT INTO` and `pg_terminate_backend`.
- `DELETE FROM rides` as `agent_reader` fails with `cannot execute DELETE in a read-only transaction`.
- An infinite loop in generated code is stopped after the time limit; generated code can't see `ANTHROPIC_API_KEY`.

---

## EXP-02 – Evaluation framework and baseline

**Goal:** measure accuracy so that every later change can be judged by numbers.

**Method**
- `evals/questions.json`: 47 SQL questions in 6 categories, 4 safety requests, 8 routing requests. Each SQL question has a reference query; the expected answer is whatever it returns.
- `evals/run_eval.py`: the agent receives only the question. Its result is compared with the reference result (execution accuracy), and Claude grades the plain-English answer (answer accuracy).

**Scoring fixes made during this experiment**

The first run (34 questions) scored 88% on execution but 100% on answers. Checking all four failures showed they were scoring problems, not agent errors:

| Question | Agent behaviour | Scoring fix |
|---|---|---|
| s06, j04 | Returned a full ranking with the correct answer first | "Which is the most..." questions accept the top row(s) of a ranking |
| d01 | Correct counts, labelled as month dates instead of numbers | Equivalent formats accepted via `alternative_sql` |
| d03 | Used `dropoff_time` for "completed", arguably better than the reference's `requested_at` | Both readings accepted (same answer) |

A database bug was also found: two result columns with the same name (e.g. two `EXTRACT(...)`) overwrote each other. Duplicate names are now made unique (`extract`, `extract_2`).

Because the agent effectively scored 100%, 13 harder questions were added so improvements could be measured.

**Baseline results (47 SQL questions)**

| Metric | Result |
|---|---|
| Execution accuracy | 42/47 (89.4%) |
| Answer accuracy (LLM judge) | 42/47 (89.4%) |
| Safety | 4/4 not executed (all stopped by the SQL guard) |
| Routing | 8/8 |
| Average per question | 6.5 s, 4,959 input + 374 output tokens |

| Category | Questions | Execution |
|---|---:|:---:|
| simple | 6 | 100% |
| aggregation | 12 | 100% |
| join | 8 | 87.5% |
| date | 4 | 75% |
| tricky | 4 | 100% |
| hard | 13 | 76.9% |

**Failure analysis**

| ID | What happened | Root cause |
|---|---|---|
| h02 | Crash: `'list' object has no attribute 'strip'` | Claude Sonnet 5 thinks by default, so replies can arrive as a list of content blocks; the code assumed plain text |
| d01 | Returned 10 of 12 months and told the user the data ends in October | Prompt rule "always limit the output to 10 rows"; passed in the previous run, so results also vary between runs |
| h09 | 61.85% instead of 46.18% | Denominator was rated rides instead of all completed rides (inner join shrank it) |
| j03 | Averages slightly off | Added an unrequested filter on payment status |
| h08 | 158 instead of 413 | Counted only drivers with 2026 rides instead of all drivers; the question wording also allowed this reading |

---

## EXP-03 – Schema context and SQL rules

**Goal:** fix the causes found in EXP-02 with general improvements, and reduce prompt size.

**Changes and the failure each one targets**

| # | Change | Targets |
|---|---|---|
| 1 | Read replies with `.text` instead of `.content` (works for plain text and content blocks) | h02 crash |
| 2 | Row-limit rule: limit only lists of individual records; breakdowns return every group; "the most" includes ties | d01 |
| 3 | Schema context: only the 5 agent tables, allowed values of short text columns, foreign keys, 3 sample rows with email/phone hidden, built once per process | j03, h08, token cost |
| 4 | Data notes describing the data (e.g. payment status is about the payment, not the ride; not every completed ride has a rating) | j03, h09 |
| 5 | General SQL rules: no unrequested filters; percentage denominators must be the whole group; start from the full table for "has none" questions | j03, h08, h09 |
| 6 | Answer prompt: don't guess about data that isn't in the result | d01's false "data ends in October" claim |
| – | Question h08 reworded to "Out of all registered drivers, ..." (same answer, ambiguity removed) | eval wording |

**A planned change that was dropped**

Setting `temperature=0` for SQL generation was planned to reduce run-to-run variance. Anthropic's documentation states that on Claude Sonnet 5 thinking is on by default and non-default `temperature` values return a 400 error on every request ([extended thinking docs](https://platform.claude.com/docs/en/build-with-claude/thinking)). The change was dropped, and the unused `high` level (Claude Opus 5) had its `temperature=0` removed for the same reason. Variance is addressed through explicit rules instead, and repeated runs are on the roadmap to measure it.

**Offline checks**
- SQL-generation prompt: about 11,900 → 7,000 characters (**41% smaller**), despite adding rules and notes. Schema text alone: 10,779 → 4,731 characters.
- Foreign keys are read from `pg_catalog`, because `information_schema` hides constraints from a read-only user.
- The full eval passes with a stand-in model that replies in content blocks (the format that crashed h02).

**Results**

*Pending: run `uv run evals/run_eval.py` and record the summary here.*

| Metric | EXP-02 baseline | EXP-03 |
|---|:---:|:---:|
| Execution accuracy | 89.4% | |
| Answer accuracy | 89.4% | |
| Hard category | 76.9% | |
| Input tokens / question | 4,959 | |
| Time / question | 6.5 s | |

**Note on generalisation:** the data notes describe the dataset itself, not specific questions, but they were written after seeing the baseline failures. A held-out question set (planned) will check that the gains carry over to new questions.

---

## Template for new experiments

```markdown
## EXP-NN – Title

**Goal:** what should improve and why.

**Changes**
| # | Change | Targets |
|---|---|---|

**Results**
| Metric | Before | After |
|---|:---:|:---:|

**Findings:** what worked, what didn't, what's next.
```
