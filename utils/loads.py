"""
Loading files into PostgreSQL, with a person's approval (EXP-13).

Two steps, deliberately separate:

1. prepare_load(file, table) - used by the ETL agent's tool. It reads the file, works out
   column names and types, runs data checks and builds a preview. It writes NOTHING to the
   database; it saves the plan as a pending load and returns its ID.

2. approve_load(load_id) - called only by a person (the Approve button in the app, or a
   script). The ETL agent has no tool for it, so no request or prompt can make the model
   load data on its own. It re-checks that the file hasn't changed since the preview, then
   writes it to the "external" schema as the etl_writer user, in one transaction.

The database enforces the boundary too: etl_writer can only create and fill tables in
"external" (see utils/setup_load_access.py), never the ride-sharing tables in "public".

Pending loads are kept in agent_state/pending_loads.db, outside data/, so code generated
by the ETL agent (which can only write inside data/) can't alter a plan after the preview.
"""

import hashlib
import json
import math
import os
import re
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values

from utils.etl_tools import resolve_data_path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DB = Path(os.getenv("LOAD_STATE_DB", PROJECT_ROOT / "agent_state" / "pending_loads.db"))
SCHEMA = "external"
MAX_ROWS = 200_000
TABLE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$")


# ------------------------------------------------------------------ pending loads (local state)

def _state() -> sqlite3.Connection:
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(STATE_DB)
    connection.row_factory = sqlite3.Row
    connection.execute("""CREATE TABLE IF NOT EXISTS loads (
        load_id TEXT PRIMARY KEY, created_at REAL, file TEXT, file_hash TEXT, table_name TEXT,
        mode TEXT, rows INTEGER, plan TEXT, status TEXT, result TEXT)""")
    return connection


def _save(load: dict) -> None:
    with closing(_state()) as connection, connection:
        connection.execute("INSERT OR REPLACE INTO loads VALUES (?,?,?,?,?,?,?,?,?,?)", (
            load["load_id"], load["created_at"], load["file"], load["file_hash"], load["table"], load["mode"],
            load["rows"], json.dumps({k: load[k] for k in ("columns", "checks", "preview")}),
            load["status"], load.get("result", "")))


def _row_to_load(row) -> dict:
    load = {"load_id": row["load_id"], "created_at": row["created_at"], "file": row["file"],
            "file_hash": row["file_hash"], "table": row["table_name"], "mode": row["mode"],
            "rows": row["rows"], "status": row["status"], "result": row["result"]}
    load.update(json.loads(row["plan"]))
    return load


def get_load(load_id: str):
    with closing(_state()) as connection:
        row = connection.execute("SELECT * FROM loads WHERE load_id = ?", (load_id,)).fetchone()
    return _row_to_load(row) if row else None


def list_loads(status: str = None) -> list:
    with closing(_state()) as connection:
        rows = connection.execute("SELECT * FROM loads ORDER BY created_at").fetchall()
    loads = [_row_to_load(row) for row in rows]
    return [load for load in loads if status is None or load["status"] == status]


def cancel_load(load_id: str) -> dict:
    load = get_load(load_id)
    if not load or load["status"] != "pending":
        return {"ok": False, "message": "There's no pending load with that ID."}
    load.update(status="cancelled", result="Cancelled by a person; nothing was loaded.")
    _save(load)
    return {"ok": True, "message": load["result"]}


def discard_loads(file_prefix: str) -> None:
    """Forget pending loads of files under file_prefix (used by the eval to clean up)."""
    with closing(_state()) as connection, connection:
        connection.execute("DELETE FROM loads WHERE file LIKE ?", (file_prefix + "%",))


# ------------------------------------------------------------------ reading and planning

def _read(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in (".json", ".jsonl"):
        try:
            return pd.read_json(path, lines=True)
        except ValueError:
            return pd.read_json(path)
    raise ValueError(f"Unsupported file type {suffix}: use a .csv, .json or .parquet file inside data/")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _column_name(name, used: set) -> str:
    clean = re.sub(r"[^a-z0-9_]+", "_", str(name).strip().lower()).strip("_") or "column"
    if clean[0].isdigit():
        clean = "c_" + clean
    clean = clean[:60]
    base, number = clean, 2
    while clean in used:
        clean, number = f"{base}_{number}", number + 1
    used.add(clean)
    return clean


def _column_type(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(series):
        return "BIGINT"
    if pd.api.types.is_float_dtype(series):
        values = series.dropna()
        if len(values) and (values == values.round()).all() and values.abs().max() < 2 ** 53 \
                and series.isna().any():
            return "BIGINT"            # whole numbers stored as floats because of missing values
        return "DOUBLE PRECISION"
    if isinstance(series.dtype, pd.DatetimeTZDtype):
        return "TIMESTAMPTZ"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "TIMESTAMP"
    values = series.dropna()
    if len(values) and values.map(lambda v: isinstance(v, (list, dict))).all():
        return "JSONB"
    text = values.astype(str)
    if len(text) and text.str.match(DATE).all():
        return "DATE"
    if len(text) and text.str.match(DATETIME).all():
        return "TIMESTAMP"
    return "TEXT"


def _preview_value(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value if isinstance(value, (int, float, bool)) else str(value)


def prepare_load(input_file_path: str, table_name: str, mode: str = "replace") -> dict:
    """Check a file and plan its load. Writes nothing to the database."""
    if not isinstance(table_name, str) or not TABLE_NAME.match(table_name) or table_name.startswith("pg_"):
        return {"ok": False, "message": (
            f"Invalid table name {table_name!r}: use lowercase letters, digits and underscores, starting "
            f"with a letter (for example weather_daily). The table always goes in the '{SCHEMA}' schema.")}
    if mode not in ("replace", "append"):
        return {"ok": False, "message": "mode must be 'replace' (create or replace the table) or 'append'."}
    try:
        path = resolve_data_path(input_file_path)
        frame = _read(path)
    except (ValueError, FileNotFoundError, OSError) as error:
        return {"ok": False, "message": f"Could not read the file: {error}"}
    if frame.empty:
        return {"ok": False, "message": "The file has no rows, so there's nothing to load."}
    if len(frame) > MAX_ROWS:
        return {"ok": False, "message": f"The file has {len(frame):,} rows; loads are limited to {MAX_ROWS:,}."}

    used = set()
    columns = [{"name": _column_name(original, used), "source": str(original), "type": _column_type(frame[original])}
               for original in frame.columns]
    missing = {c["name"]: int(frame[c["source"]].isna().sum()) for c in columns if frame[c["source"]].isna().any()}
    try:
        duplicates = int(frame.duplicated().sum())
    except TypeError:          # lists in cells can't be compared
        duplicates = 0
    checks = {"duplicate_rows": duplicates, "missing_values": missing,
              "renamed_columns": {c["source"]: c["name"] for c in columns if c["source"] != c["name"]}}
    preview = [{c["name"]: _preview_value(value) for c, value in zip(columns, row)}
               for row in frame.head(5).itertuples(index=False)]

    load = {"load_id": uuid.uuid4().hex[:12], "created_at": time.time(),
            "file": path.relative_to(PROJECT_ROOT).as_posix(), "file_hash": _file_hash(path),
            "table": table_name, "mode": mode, "rows": len(frame), "columns": columns, "checks": checks,
            "preview": preview, "status": "pending"}
    _save(load)
    return {"ok": True, "load_id": load["load_id"], "rows": load["rows"], "message": describe(load)}


def describe(load: dict) -> str:
    columns = ", ".join(f"{c['name']} {c['type']}" for c in load["columns"])
    checks = load["checks"]
    notes = [f"{checks['duplicate_rows']} duplicate rows" if checks["duplicate_rows"] else "no duplicate rows"]
    if checks["missing_values"]:
        notes.append("missing values in " + ", ".join(f"{k} ({v})" for k, v in checks["missing_values"].items()))
    else:
        notes.append("no missing values")
    if checks["renamed_columns"]:
        notes.append("renamed " + ", ".join(f"{k} -> {v}" for k, v in checks["renamed_columns"].items()))
    action = "replace (create or replace)" if load["mode"] == "replace" else "append to"
    return (f"Load prepared, NOT done yet. Load ID: {load['load_id']}. It will {action} the table "
            f"{SCHEMA}.{load['table']} with {load['rows']} rows from {load['file']}. Columns: {columns}. "
            f"Checks: {'; '.join(notes)}. Nothing has been written to the database: a person has to review "
            f"and approve this load in the app before anything is loaded.")


# ------------------------------------------------------------------ approving (people only)

def _writer_connection():
    user, password = os.getenv("DB_WRITER_USER"), os.getenv("DB_WRITER_PASSWORD")
    if not user or not password:
        raise ValueError("DB_WRITER_USER and DB_WRITER_PASSWORD must be set in .env (see utils/setup_load_access.py)")
    return psycopg2.connect(host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5432")),
                            dbname=os.getenv("DB_NAME", "postgres"), user=user, password=password)


def _convert(value, kind: str):
    if value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NaT:
        return None
    if kind == "JSONB":
        return json.dumps(value)
    if kind == "BIGINT":
        return int(value)
    if kind in ("DATE", "TIMESTAMP", "TIMESTAMPTZ") and not isinstance(value, str):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if hasattr(value, "item"):          # numpy scalars
        return value.item()
    return value


def approve_load(load_id: str) -> dict:
    """Carry out a prepared load. For people only: the ETL agent has no tool that calls this."""
    load = get_load(load_id) if isinstance(load_id, str) else None
    if not load or load["status"] != "pending":
        return {"ok": False, "message": "There's no pending load with that ID, so nothing was loaded."}
    path = PROJECT_ROOT / load["file"]
    if not path.exists() or _file_hash(path) != load["file_hash"]:
        load.update(status="failed", result="The file changed after the preview, so nothing was loaded. "
                                            "Prepare the load again to review the new data.")
        _save(load)
        return {"ok": False, "message": load["result"]}

    frame = _read(path)
    table = sql.Identifier(SCHEMA, load["table"])
    names = [sql.Identifier(c["name"]) for c in load["columns"]]
    rows = [tuple(_convert(value, c["type"]) for c, value in zip(load["columns"], row))
            for row in frame[[c["source"] for c in load["columns"]]].itertuples(index=False)]
    try:
        connection = _writer_connection()
    except Exception as error:
        return {"ok": False, "message": f"Couldn't connect as the writer user: {error}"}
    try:
        with connection, connection.cursor() as cursor:          # one transaction: all or nothing
            cursor.execute("SET LOCAL lock_timeout = '10s'")    # fail clearly if the table is in use
            if load["mode"] == "replace":
                cursor.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(table))
                definition = sql.SQL(", ").join(sql.SQL("{} {}").format(name, sql.SQL(c["type"]))
                                                for name, c in zip(names, load["columns"]))
                cursor.execute(sql.SQL("CREATE TABLE {} ({})").format(table, definition))
            insert = sql.SQL("INSERT INTO {} ({}) VALUES %s").format(table, sql.SQL(", ").join(names))
            execute_values(cursor, insert.as_string(cursor), rows, page_size=1000)
    except Exception as error:
        load.update(status="failed", result=f"The load failed and nothing was changed: {str(error).strip()[:200]}")
        _save(load)
        return {"ok": False, "message": load["result"]}
    finally:
        connection.close()

    load.update(status="approved",
                result=f"Loaded {len(rows)} rows into {SCHEMA}.{load['table']} ({load['mode']}).")
    _save(load)
    return {"ok": True, "message": load["result"]}


def drop_external_tables(prefix: str) -> None:
    """Drop external tables whose names start with prefix (used by the eval to clean up)."""
    if not TABLE_NAME.match(prefix + "x"):
        raise ValueError("unsafe prefix")
    connection = _writer_connection()
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")   # don't wait long if a table is in use
            cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s AND tablename LIKE %s",
                           (SCHEMA, prefix + "%"))
            for (name,) in cursor.fetchall():
                cursor.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(SCHEMA, name)))
    finally:
        connection.close()


def drop_external_table(table_name: str) -> dict:
    """Drop one loaded table in the external schema. For people only (utils/drop_loaded_table.py):
    the ETL agent has no tool for this, and the ride-sharing tables are out of the writer's reach."""
    if not isinstance(table_name, str) or not TABLE_NAME.match(table_name):
        return {"ok": False, "message": f"{table_name!r} isn't a valid table name."}
    connection = _writer_connection()
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SELECT to_regclass(%s)", (f"{SCHEMA}.{table_name}",))
            if cursor.fetchone()[0] is None:
                return {"ok": False, "message": f"There's no table {SCHEMA}.{table_name}."}
            cursor.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(SCHEMA, table_name)))
    finally:
        connection.close()
    return {"ok": True, "message": f"Dropped {SCHEMA}.{table_name}."}

