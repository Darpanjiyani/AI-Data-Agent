"""
Database-layer checks for loading data (EXP-13). No model is involved: each check calls the
database or the load functions directly and returns (passed, reason).

They test the safety of loading itself:
  - the writer user can't touch the ride-sharing tables, and the read-only user can't write
  - preparing a load writes nothing; only approve_load() writes, and only what was previewed
  - bad table names and unknown load IDs are refused
  - the ETL agent has no tool that can approve a load
"""

import os
from pathlib import Path

import psycopg2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT = "data/etl_eval/input"


def _connect(user_var: str, password_var: str):
    return psycopg2.connect(host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5432")),
                            dbname=os.getenv("DB_NAME", "postgres"), user=os.getenv(user_var),
                            password=os.getenv(password_var))


def _try(user: str, statement: str) -> str:
    """'' if the statement ran (and was rolled back), else the database's error."""
    variables = {"writer": ("DB_WRITER_USER", "DB_WRITER_PASSWORD"),
                 "reader": ("DB_READER_USER", "DB_READER_PASSWORD")}[user]
    try:
        connection = _connect(*variables)
    except Exception as error:
        return f"couldn't connect: {error}"
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement)
        return ""
    except Exception as error:
        return str(error).strip().splitlines()[0]
    finally:
        connection.rollback()
        connection.close()


def _external_table_exists(table: str) -> bool:
    connection = _connect("DB_READER_USER", "DB_READER_PASSWORD")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass(%s)", (f"external.{table}",))
            return cursor.fetchone()[0] is not None
    finally:
        connection.close()


def _denied(user: str, statement: str, what: str) -> tuple:
    error = _try(user, statement)
    if error.startswith("couldn't connect"):
        return False, error
    if not error:
        return False, f"{what} was allowed"
    return True, f"denied ({error[:70]})"


# ---------------------------------------------------------------- database permissions

def writer_cannot_insert_public(api):
    return _denied("writer", "INSERT INTO public.rides (ride_id) VALUES (-1)", "writing to public.rides")


def writer_cannot_create_in_public(api):
    return _denied("writer", "CREATE TABLE public.etl_writer_test (a int)", "creating a table in public")


def writer_cannot_drop_public(api):
    return _denied("writer", "DROP TABLE public.ratings", "dropping public.ratings")


def reader_cannot_write_external(api):
    return _denied("reader", "CREATE TABLE external.reader_test (a int)", "the read-only user creating a table")


# ---------------------------------------------------------------- prepare / approve

def prepare_writes_nothing(api):
    from utils.loads import prepare_load
    result = prepare_load(f"{INPUT}/trips.csv", "eval_prepare_only")
    if not result.get("ok"):
        return False, f"prepare failed: {result.get('message', '')[:90]}"
    if _external_table_exists("eval_prepare_only"):
        return False, "the table exists after only preparing the load"
    return True, f"prepared {result['rows']} rows, nothing written"


def unknown_load_refused(api):
    from utils.loads import approve_load
    result = approve_load("000000000000")
    return (False, "an unknown load ID was accepted") if result.get("ok") \
        else (True, f"refused: {result['message'][:70]}")


def changed_file_refused(api):
    from utils.loads import approve_load, prepare_load
    path = PROJECT_ROOT / INPUT / "trips_changed.csv"
    path.write_text((PROJECT_ROOT / INPUT / "trips.csv").read_text(encoding="utf-8"), encoding="utf-8")
    prepared = prepare_load(f"{INPUT}/trips_changed.csv", "eval_changed_file")
    if not prepared.get("ok"):
        return False, f"prepare failed: {prepared.get('message', '')[:90]}"
    with path.open("a", encoding="utf-8") as handle:   # the file changes after the person saw the preview
        handle.write("9999,Toronto,1.0,1.0,cash,2025-05-01 10:00:00,x@example.com\n")
    result = approve_load(prepared["load_id"])
    if result.get("ok") or _external_table_exists("eval_changed_file"):
        return False, "a file changed after the preview was still loaded"
    return True, f"refused: {result['message'][:70]}"


def bad_table_names_refused(api):
    from utils.loads import prepare_load
    for name in ("eval_x; DROP TABLE rides", "public.rides", "Rides Table", "pg_eval", ""):
        if prepare_load(f"{INPUT}/trips.csv", name).get("ok"):
            return False, f"table name {name!r} was accepted"
    return True, "5 unsafe or invalid names refused"


def approve_loads_exact_rows_and_types(api):
    from utils.loads import approve_load, prepare_load
    prepared = prepare_load(f"{INPUT}/weather_daily.csv", "eval_weather_check")
    if not prepared.get("ok"):
        return False, f"prepare failed: {prepared.get('message', '')[:90]}"
    result = approve_load(prepared["load_id"])
    if not result.get("ok"):
        return False, f"approve failed: {result['message'][:90]}"
    connection = _connect("DB_READER_USER", "DB_READER_PASSWORD")   # read back as the SQL agent would
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM external.eval_weather_check")
            rows = cursor.fetchone()[0]
            cursor.execute("SELECT column_name, data_type FROM information_schema.columns "
                           "WHERE table_schema = 'external' AND table_name = 'eval_weather_check'")
            types = dict(cursor.fetchall())
    finally:
        connection.close()
    if rows != 28:
        return False, f"{rows} rows in the table, expected 28"
    if types.get("date") != "date" or types.get("temp_max_c") != "double precision":
        return False, f"unexpected column types: {types}"
    return True, "28 rows; date as DATE, temperatures as numbers; readable by the read-only user"


def agent_cannot_approve(api):
    from agents.etl_analyst import tools
    names = sorted(tool.name for tool in tools)
    if any("approve" in name or name.startswith("load_") or name == "execute_load" for name in names):
        return False, f"the agent has a tool that could load data: {names}"
    if "prepare_load_tool" not in names:
        return False, f"the agent has no prepare_load_tool: {names}"
    return True, f"agent tools: {', '.join(names)}"


CHECKS = {name: function for name, function in globals().items()
          if callable(function) and not name.startswith("_") and name not in {"Path"}}
