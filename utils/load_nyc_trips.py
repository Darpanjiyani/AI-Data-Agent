"""
Load one month of real NYC ride-hailing trips (Uber, Lyft, Via) into PostgreSQL. Run as the admin:

    uv run python utils/load_nyc_trips.py --month 2026-06

The data is the NYC Taxi & Limousine Commission's "High Volume For-Hire Vehicle" trip records:
every Uber, Lyft and Via trip in New York City, about 20 million per month, published as Parquet:
https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page

It creates a schema "nyc", separate from the sample ride data in "public":
  - nyc.trips      one row per trip (the Parquet file, column names in lower case;
                   PULocationID/DOLocationID become pickup_zone_id/dropoff_zone_id)
  - nyc.zones      the TLC taxi zones: id, borough, zone name, service zone (airports included)
  - nyc.companies  hvfhs_license_num -> company (from the TLC data dictionary)
and lets the read-only agent user (agent_reader) read them.

The files are downloaded to data/nyc/ (git-ignored). No indexes are created on purpose:
the first evaluation measures the agent on the raw table, and indexes are added as a change.
Run again with --replace to reload the trips.
"""

import argparse
import getpass
import io
import os
import sys
import time
from pathlib import Path

import psycopg2
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq
import requests
from dotenv import load_dotenv
from psycopg2 import sql

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

SCHEMA = "nyc"
DATA_DIR = PROJECT_ROOT / "data" / "nyc"
TRIPS_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data/fhvhv_tripdata_{month}.parquet"
ZONES_URL = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv"
# From the TLC data dictionary for high volume FHV trip records
COMPANIES = [("HV0002", "Juno"), ("HV0003", "Uber"), ("HV0004", "Via"), ("HV0005", "Lyft")]
RENAMES = {"PULocationID": "pickup_zone_id", "DOLocationID": "dropoff_zone_id"}
BATCH_ROWS = 250_000


def download(url: str, target: Path) -> None:
    if target.exists() and target.stat().st_size > 0:
        print(f"Already downloaded: {target.relative_to(PROJECT_ROOT)} ({target.stat().st_size / 1e6:,.0f} MB)")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url} ...")
    partial = target.with_suffix(target.suffix + ".part")
    with requests.get(url, stream=True, timeout=60) as response:
        if response.status_code == 403 or response.status_code == 404:
            sys.exit(f"Not found ({response.status_code}): {url}\nCheck the month on the TLC page; "
                     "files are published about two months late.")
        response.raise_for_status()
        done, next_report = 0, 50e6
        with open(partial, "wb") as f:
            for chunk in response.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                done += len(chunk)
                if done >= next_report:
                    print(f"  {done / 1e6:,.0f} MB")
                    next_report += 50e6
    partial.replace(target)
    print(f"Saved {target.relative_to(PROJECT_ROOT)} ({target.stat().st_size / 1e6:,.0f} MB)")


def admin_connection():
    # Same admin settings as utils/setup_load_access.py: host/user/password or DB_* names.
    admin_user = os.getenv("user") or os.getenv("DB_USER") or "postgres"
    database = os.getenv("database") or os.getenv("DB_NAME")
    if not database:
        sys.exit("Set database= (or DB_NAME=) in .env first.")
    password = os.getenv("password") or os.getenv("DB_PASSWORD") or getpass.getpass(
        f"Password for the admin user '{admin_user}' (not saved): ")
    try:
        return psycopg2.connect(host=os.getenv("host") or os.getenv("DB_HOST", "localhost"),
                                port=os.getenv("port") or os.getenv("DB_PORT", "5432"),
                                dbname=database, user=admin_user, password=password)
    except psycopg2.OperationalError as error:
        sys.exit(f"Couldn't connect as the admin user: {str(error).strip()}")


def postgres_type(arrow_type: pa.DataType) -> str:
    if pa.types.is_int8(arrow_type) or pa.types.is_int16(arrow_type):
        return "smallint"
    if pa.types.is_int32(arrow_type) or pa.types.is_uint16(arrow_type):
        return "integer"
    if pa.types.is_integer(arrow_type):
        return "bigint"
    if pa.types.is_floating(arrow_type):
        return "double precision"
    if pa.types.is_decimal(arrow_type):
        return "numeric"
    if pa.types.is_boolean(arrow_type):
        return "boolean"
    if pa.types.is_timestamp(arrow_type):
        return "timestamp"
    if pa.types.is_date(arrow_type):
        return "date"
    return "text"


def column_name(name: str) -> str:
    return RENAMES.get(name, name.lower())


def for_csv(batch: pa.RecordBatch) -> pa.Table:
    """Whole seconds for timestamps (PostgreSQL takes microseconds at most), names as in the table."""
    columns = []
    for array in batch.columns:
        if pa.types.is_timestamp(array.type):
            array = pc.cast(array, pa.timestamp("s"), safe=False)
        columns.append(array)
    return pa.Table.from_arrays(columns, names=[column_name(n) for n in batch.schema.names])


def load_trips(connection, parquet_path: Path) -> int:
    parquet = pq.ParquetFile(parquet_path)
    schema = parquet.schema_arrow
    total = parquet.metadata.num_rows
    print(f"Trips file: {total:,} rows, {len(schema.names)} columns")
    columns = sql.SQL(", ").join(
        sql.SQL("{} {}").format(sql.Identifier(column_name(field.name)), sql.SQL(postgres_type(field.type)))
        for field in schema)
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("DROP TABLE IF EXISTS {}.trips").format(sql.Identifier(SCHEMA)))
        cursor.execute(sql.SQL("CREATE TABLE {}.trips ({})").format(sql.Identifier(SCHEMA), columns))
        copy = sql.SQL("COPY {}.trips FROM STDIN WITH (FORMAT csv)").format(sql.Identifier(SCHEMA)).as_string(cursor)
        start, loaded = time.perf_counter(), 0
        for batch in parquet.iter_batches(batch_size=BATCH_ROWS):
            buffer = io.BytesIO()
            pa_csv.write_csv(for_csv(batch), buffer, pa_csv.WriteOptions(include_header=False))
            buffer.seek(0)
            cursor.copy_expert(copy, buffer)
            loaded += batch.num_rows
            rate = loaded / (time.perf_counter() - start)
            print(f"  {loaded:,} / {total:,} rows ({rate:,.0f} rows/s)", end="\r")
        print()
    return loaded


def main() -> None:
    parser = argparse.ArgumentParser(description="Load one month of NYC Uber/Lyft/Via trips into PostgreSQL.")
    parser.add_argument("--month", default="2026-06", help="Month to load, YYYY-MM (default 2026-06).")
    parser.add_argument("--replace", action="store_true", help="Reload the trips even if nyc.trips has rows.")
    args = parser.parse_args()

    trips_file = DATA_DIR / f"fhvhv_tripdata_{args.month}.parquet"
    zones_file = DATA_DIR / "taxi_zone_lookup.csv"
    download(TRIPS_URL.format(month=args.month), trips_file)
    download(ZONES_URL, zones_file)

    reader = os.getenv("DB_READER_USER", "agent_reader")
    connection = admin_connection()
    ident = sql.Identifier
    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(ident(SCHEMA)))
            cursor.execute("SELECT to_regclass(%s)", (f"{SCHEMA}.trips",))
            exists = cursor.fetchone()[0] is not None
            existing = 0
            if exists:
                cursor.execute(sql.SQL("SELECT COUNT(*) FROM {}.trips").format(ident(SCHEMA)))
                existing = cursor.fetchone()[0]

            # Zones and companies: small lookup tables, always reloaded
            cursor.execute(sql.SQL("DROP TABLE IF EXISTS {}.zones").format(ident(SCHEMA)))
            cursor.execute(sql.SQL("CREATE TABLE {}.zones (zone_id integer PRIMARY KEY, borough text, "
                                   "zone text, service_zone text)").format(ident(SCHEMA)))
            with open(zones_file, "rb") as f:
                cursor.copy_expert(sql.SQL("COPY {}.zones FROM STDIN WITH (FORMAT csv, HEADER true)")
                                   .format(ident(SCHEMA)).as_string(cursor), f)
            cursor.execute(sql.SQL("DROP TABLE IF EXISTS {}.companies").format(ident(SCHEMA)))
            cursor.execute(sql.SQL("CREATE TABLE {}.companies (hvfhs_license_num text PRIMARY KEY, company text)")
                           .format(ident(SCHEMA)))
            cursor.executemany(sql.SQL("INSERT INTO {}.companies VALUES (%s, %s)").format(ident(SCHEMA)), COMPANIES)
        print("OK  nyc.zones and nyc.companies")

        if existing and not args.replace:
            print(f"nyc.trips already has {existing:,} rows; kept (use --replace to reload).")
        else:
            loaded = load_trips(connection, trips_file)
            print(f"OK  nyc.trips: {loaded:,} rows")

        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(ident(SCHEMA), ident(reader)))
            cursor.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(ident(SCHEMA), ident(reader)))
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    # Statistics for the query planner (outside the transaction)
    connection.autocommit = True
    with connection.cursor() as cursor:
        print("Updating table statistics (ANALYZE)...")
        cursor.execute(sql.SQL("ANALYZE {}.trips").format(ident(SCHEMA)))
        cursor.execute(sql.SQL("SELECT COUNT(*), MIN(pickup_datetime), MAX(pickup_datetime) FROM {}.trips")
                       .format(ident(SCHEMA)))
        rows, first, last = cursor.fetchone()
        cursor.execute("SELECT pg_size_pretty(pg_total_relation_size(%s))", (f"{SCHEMA}.trips",))
        size = cursor.fetchone()[0]
    connection.close()
    print(f"\nDone in {(time.perf_counter() - started) / 60:.1f} min: nyc.trips has {rows:,} rows "
          f"({size}), pickups from {first} to {last}. {reader} can read the nyc schema.")


if __name__ == "__main__":
    main()
