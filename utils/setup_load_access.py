"""
One-time database setup for loading data (EXP-13). Run once, as the admin user:

    uv run python utils/setup_load_access.py

It creates:
  - a schema called "external", where loaded data lives (separate from the ride-sharing tables)
  - a user etl_writer (DB_WRITER_USER / DB_WRITER_PASSWORD in .env) that can create and fill
    tables ONLY in "external": it has no rights on the ride-sharing tables in "public"
  - read access to "external" for the agent's read-only user (agent_reader), so the SQL agent
    can query loaded data, including tables created later

It's safe to run again: every step checks what already exists.
"""

import getpass
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

SCHEMA = "external"


def main() -> None:
    writer = os.getenv("DB_WRITER_USER", "etl_writer")
    writer_password = os.getenv("DB_WRITER_PASSWORD")
    reader = os.getenv("DB_READER_USER", "agent_reader")
    if not writer_password:
        sys.exit("Set DB_WRITER_USER and DB_WRITER_PASSWORD in .env first (see .env.example).")

    # The admin connection (the same settings utils/feed_db.py uses). The admin password doesn't
    # have to be in .env: if it isn't, the script asks for it and doesn't store it.
    # Admin settings: host/user/password (as in utils/feed_db.py) or DB_USER/DB_PASSWORD.
    admin_user = os.getenv("user") or os.getenv("DB_USER") or "postgres"
    database = os.getenv("database") or os.getenv("DB_NAME")
    if not database:
        sys.exit("Set database= (or DB_NAME=) in .env first: the database with the ride-sharing tables.")
    admin_password = os.getenv("password") or os.getenv("DB_PASSWORD") or getpass.getpass(
        f"Password for the admin user '{admin_user}' (not saved): ")
    print(f"Connecting to '{database}' as '{admin_user}'...")
    try:
        admin = psycopg2.connect(host=os.getenv("host") or os.getenv("DB_HOST", "localhost"),
                                 port=os.getenv("port") or os.getenv("DB_PORT", "5432"),
                                 dbname=database, user=admin_user, password=admin_password)
    except psycopg2.OperationalError as error:
        sys.exit(f"Couldn't connect as the admin user: {str(error).strip()}")
    admin.autocommit = True
    ident = sql.Identifier

    steps = [
        ("Create the schema", sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(ident(SCHEMA))),
        ("Create the writer user", sql.SQL(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {}) THEN "
            "CREATE ROLE {} LOGIN; END IF; END $$").format(sql.Literal(writer), ident(writer))),
        ("Set its password", sql.SQL("ALTER ROLE {} PASSWORD {}").format(ident(writer), sql.Literal(writer_password))),
        ("Let it connect", sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(ident(database), ident(writer))),
        # Older PostgreSQL versions let every user create tables in "public"; nobody but the
        # admin needs that here, so it's switched off (PostgreSQL 15+ already does this).
        ("No new tables in public for ordinary users",
         sql.SQL("REVOKE CREATE ON SCHEMA public FROM PUBLIC")),
        ("Writer: create and fill tables in external only",
         sql.SQL("GRANT USAGE, CREATE ON SCHEMA {} TO {}").format(ident(SCHEMA), ident(writer))),
        ("Writer: time limit and default schema",
         sql.SQL("ALTER ROLE {} SET statement_timeout = '60s'").format(ident(writer))),
        ("", sql.SQL("ALTER ROLE {} SET search_path = {}").format(ident(writer), ident(SCHEMA))),
        ("Reader: can see external",
         sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(ident(SCHEMA), ident(reader))),
        ("Reader: can read tables already there",
         sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(ident(SCHEMA), ident(reader))),
        ("Reader: can read tables the writer creates later",
         sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT SELECT ON TABLES TO {}")
         .format(ident(writer), ident(SCHEMA), ident(reader))),
    ]
    with admin.cursor() as cursor:
        for label, statement in steps:
            try:
                cursor.execute(statement)
            except psycopg2.Error as error:
                # Hosted databases (e.g. Neon) may not let the admin set another user's default
                # privileges. That step is optional: every approved load grants read access itself.
                if "DEFAULT PRIVILEGES" not in statement.as_string(cursor):
                    raise
                print(f"--  {label}: skipped ({str(error).strip().splitlines()[0]}); each load grants it instead")
                continue
            if label:
                print(f"OK  {label}")
    admin.close()
    print(f"\nDone: {writer} can write only to the '{SCHEMA}' schema; {reader} can read it.")


if __name__ == "__main__":
    main()
