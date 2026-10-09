# Troubleshooting

Common problems when setting up or running the AI Data Agent. Setup steps are in the [README](../README.md#4-getting-started).

| Problem | Solution |
|---|---|
| `Error connecting to the database` | Check PostgreSQL is running and the `DB_*` values in `.env` |
| `DB_READER_USER and DB_READER_PASSWORD must be set` | Create the read-only user and add both values to `.env` |
| `permission denied for table ...` | Run the `GRANT SELECT ...` lines while connected to your database |
| `KeyError: 'host'` when loading data | Add the lowercase `host`, `port`, `database`, `user`, `password` entries |
| Anthropic authentication error | Check `ANTHROPIC_API_KEY` |
| 400 error mentioning `temperature` | Claude Sonnet 5 and Opus 5 don't accept a custom temperature; remove it from `utils/llm_pick.py` |
| Duplicate key error when loading data | Data is already loaded; uncomment the `TRUNCATE` block to reload |
| `ModuleNotFoundError` | Run from the project root with `uv run` |
| App shows "The app needs these settings" | Add the listed values to `.env` in the project root, then refresh the page |
| `streamlit: command not found` | Run `uv sync`, then start it with `uv run streamlit run app.py` |
| Query rejected as unsafe | The agent only runs read-only `SELECT` queries |
| `Path must be inside the data/ folder` | Use paths under `data/` |
| `ran longer than 60 seconds and was stopped` | Split the transformation, or raise `CODE_TIMEOUT_SECONDS` in `utils/etl_tools.py` |
