"""
Save a table to a CSV file, read as the read-only user. Used to copy the loaded weather data
to the public demo's database:

    uv run python utils/export_table.py external.weather_daily data/demo/weather_daily.csv
"""

import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import psycopg2  # noqa: E402
from psycopg2 import sql  # noqa: E402

from utils.database import reader_connection_details  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1].count(".") != 1:
        sys.exit("Usage: uv run python utils/export_table.py <schema.table> <output.csv>")
    schema, table = sys.argv[1].split(".")
    output = PROJECT_ROOT / sys.argv[2]
    connection = psycopg2.connect(**reader_connection_details())
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT * FROM {} ORDER BY 1, 2").format(sql.Identifier(schema, table)))
            frame = pd.DataFrame(cursor.fetchall(), columns=[d[0] for d in cursor.description])
    finally:
        connection.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    print(f"Saved {len(frame):,} rows of {schema}.{table} to {output.relative_to(PROJECT_ROOT).as_posix()}")
