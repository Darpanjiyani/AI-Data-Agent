"""
One-time setup of the read-only database user the agent connects with (agent_reader).
Run as the admin user. Locally the README does this with SQL; for a cloud database
(the public demo) this script does the same steps:

    uv run --env-file .env.cloud python utils/setup_reader.py

The user can read the ride-sharing tables, can't change anything (every transaction is
read-only), and each query stops after 15 seconds. Safe to run again.
"""

import getpass
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")   # doesn't override settings passed with --env-file


def main() -> None:
    reader = os.getenv("DB_READER_USER", "agent_reader")
    reader_password = os.getenv("DB_READER_PASSWORD")
    if not reader_password:
        sys.exit("Set DB_READER_USER and DB_READER_PASSWORD first.")
    admin_user = os.getenv("user") or os.getenv("DB_USER") or "postgres"
    database = os.getenv("database") or os.getenv("DB_NAME")
    admin_password = os.getenv("password") or os.getenv("DB_PASSWORD") or getpass.getpass(
        f"Password for the admin user '{admin_user}' (not saved): ")
    host = os.getenv("host") or os.getenv("DB_HOST", "localhost")
    print(f"Connecting to '{database}' on {host} as '{admin_user}'...")
    try:
        admin = psycopg2.connect(host=host, port=os.getenv("port") or os.getenv("DB_PORT", "5432"),
                                 dbname=database, user=admin_user, password=admin_password)
    except psycopg2.OperationalError as error:
        sys.exit(f"Couldn't connect as the admin user: {str(error).strip()}")
    admin.autocommit = True
    ident = sql.Identifier
    steps = [
        ("Create the read-only user", sql.SQL(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {}) THEN "
            "CREATE ROLE {} LOGIN; END IF; END $$").format(sql.Literal(reader), ident(reader))),
        ("Set its password", sql.SQL("ALTER ROLE {} PASSWORD {}").format(ident(reader), sql.Literal(reader_password))),
        ("Let it connect", sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(ident(database), ident(reader))),
        ("Read the ride-sharing tables", sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident(reader))),
        ("", sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(ident(reader))),
        ("", sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {}").format(ident(reader))),
        ("Every transaction read-only", sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(ident(reader))),
        ("15-second limit per query", sql.SQL("ALTER ROLE {} SET statement_timeout = '15s'").format(ident(reader))),
        ("No new tables in public for ordinary users", sql.SQL("REVOKE CREATE ON SCHEMA public FROM PUBLIC")),
    ]
    with admin.cursor() as cursor:
        for label, statement in steps:
            cursor.execute(statement)
            if label:
                print(f"OK  {label}")
    admin.close()
    print(f"\nDone: {reader} can read the ride-sharing tables and change nothing.")


if __name__ == "__main__":
    main()
