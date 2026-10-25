# Live demo: deploying the app

How the public demo is set up: the Streamlit app on **Streamlit Community Cloud**, a copy of the database on **Neon** (free Postgres), and **demo mode** switched on so strangers can ask questions safely and the API bill stays bounded.

## What demo mode changes

Turn it on with `DEMO_MODE=1` (in the app's Secrets). Running locally without it, nothing changes.

| Without demo mode (local) | With demo mode (public) |
|---|---|
| SQL Analyst and ETL Analyst | SQL Analyst only; ETL requests get a short "switched off in the demo" answer, and no tool runs |
| One shared chat history | Each browser session has its own chats; visitors never see each other's |
| No limits | 15 questions per visit, 200 per day for everyone together, 500 characters per question (all adjustable) |
| Ride questions as examples | Examples include the weather data, plus a note that the ride data is synthetic |

The app connects only as the read-only `agent_reader`. The demo's Secrets hold no writer or admin password, so it couldn't write to the database even if every other check failed.

**Cost protection, in layers:**

1. **Per visit:** 15 questions per browser session.
2. **Per day:** 200 questions for all visitors together. The count lives in a small file on the app's server, which resets if Streamlit restarts the app.
3. **Hard stop:** set this one up in the Anthropic Console.
   - Use a **separate API key** for the demo, so you can revoke it without touching your own.
   - Keep a **small prepaid credit balance with auto-reload off**, so spending can't go past what's loaded.
   - If your Console shows workspace spend limits, set a low monthly limit on the demo's workspace too.

## One-time setup

### 1. Create the cloud database (Neon)

1. Sign up at [neon.com](https://neon.com) (GitHub login works) and create a project, e.g. `ai-data-agent`, in a region near you.
2. Open **Connect** on the project dashboard and note the **host** (`ep-...neon.tech`), the **database** (`neondb`), the **role** (e.g. `neondb_owner`) and its **password**.

The free plan has 1 GB per project and pauses the database after 5 minutes without queries. The first question after a pause wakes it, which adds a second or so.

### 2. Create `.env.cloud` in the project folder

This file is ignored by git. Never commit it. Use new passwords, not your local ones:

```
# Admin (Neon's owner role): only used by the setup scripts below
host=ep-xxxx.region.aws.neon.tech
port=5432
database=neondb
user=neondb_owner
password=<Neon password>

# The users the scripts create
DB_HOST=ep-xxxx.region.aws.neon.tech
DB_PORT=5432
DB_NAME=neondb
DB_READER_USER=agent_reader
DB_READER_PASSWORD=<new strong password>
DB_WRITER_USER=agent_writer
DB_WRITER_PASSWORD=<another new strong password>

PGSSLMODE=require
DEMO_MODE=1
```

### 3. Fill the cloud database

Run these from the project folder. `--env-file .env.cloud` points each script at Neon instead of your local database:

```
uv run --env-file .env.cloud utils/feed_db.py
uv run --env-file .env.cloud python utils/setup_reader.py
uv run --env-file .env.cloud python utils/setup_load_access.py
uv run python utils/export_table.py external.weather_daily data/demo/weather_daily.csv
uv run --env-file .env.cloud python utils/load_file.py data/demo/weather_daily.csv weather_daily
```

1. Creates the ride-sharing tables and loads the dataset.
2. Creates the read-only `agent_reader`.
3. Creates the `external` schema and the writer user.
4. Saves your local weather table (4,608 rows) to a CSV. This one runs against your **local** database.
5. Loads that CSV into Neon. It shows a summary first; type `yes` to approve.

### 4. Rehearse locally against the cloud database

```
uv run --env-file .env.cloud streamlit run app.py
```

The app runs in demo mode on your machine, using Neon and your normal API key. Try a weather example, a delete request and an ETL request, which should get the "switched off" answer.

### 5. Deploy on Streamlit Community Cloud

1. Merge the `live-demo` branch into `main` (pull request), or deploy the branch directly.
2. At [share.streamlit.io](https://share.streamlit.io), sign in with GitHub and choose **Create app**. Set:
   - repository `Darpanjiyani/AI-Data-Agent`
   - branch `main`
   - main file `app.py`
   - a custom subdomain, if you like
3. Under **Advanced settings**, pick Python 3.12 and paste the Secrets:

```toml
ANTHROPIC_API_KEY = "<the demo's own API key>"
DB_HOST = "ep-xxxx.region.aws.neon.tech"
DB_PORT = "5432"
DB_NAME = "neondb"
DB_READER_USER = "agent_reader"
DB_READER_PASSWORD = "<the reader password from .env.cloud>"
PGSSLMODE = "require"
DEMO_MODE = "1"
DEMO_MAX_QUESTIONS_PER_SESSION = "15"
DEMO_MAX_QUESTIONS_PER_DAY = "200"
```

Only the reader's login goes here, never the writer's or the admin's. Community Cloud installs the dependencies from `uv.lock`.

4. Deploy, then test from your phone:
   - an example question
   - a follow-up ("And in 2026?")
   - "Delete all cancelled rides." (refused)
   - an ETL request (switched off)

## After it's live

- Add the link to the README, your resume and LinkedIn.
- Streamlit puts apps to sleep after a period without visitors. The first visitor sees a "wake up" button and waits a short while.
- To change the limits, edit the Secrets; the app restarts with the new values.
