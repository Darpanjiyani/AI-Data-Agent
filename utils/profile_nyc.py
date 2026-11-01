"""
Profile the loaded NYC trips (EXP-16), as the read-only agent user:

    uv run python utils/profile_nyc.py

Prints what the data looks like: nulls, value ranges, odd values (negative fares, zero miles),
flag values, companies, zones, and how long a full scan takes. The output is used to write the
data notes and the NYC question set; it contains only counts and summaries, no trip rows.
It takes a few minutes (several full scans of about 20M rows).
"""

import sys
import time
from pathlib import Path

import psycopg2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
from utils.database import reader_connection_details

NUMERIC = ["trip_miles", "trip_time", "base_passenger_fare", "tolls", "bcf", "sales_tax",
           "congestion_surcharge", "airport_fee", "tips", "driver_pay", "cbd_congestion_fee"]
FLAGS = ["shared_request_flag", "shared_match_flag", "access_a_ride_flag", "wav_request_flag", "wav_match_flag"]

QUERIES = [
    ("Columns and types",
     "SELECT column_name, data_type FROM information_schema.columns "
     "WHERE table_schema = 'nyc' AND table_name = 'trips' ORDER BY ordinal_position"),
    ("Full scan: row count (timed)", "SELECT COUNT(*) AS trips FROM nyc.trips"),
    ("Nulls per column",
     "SELECT " + ", ".join(f"COUNT(*) - COUNT({c}) AS {c}" for c in
                          ["originating_base_num", "on_scene_datetime", "request_datetime", "pickup_datetime",
                           "dropoff_datetime", "pickup_zone_id", "dropoff_zone_id", "airport_fee",
                           "cbd_congestion_fee", "congestion_surcharge", "tips", "driver_pay"]) +
     " FROM nyc.trips"),
    ("Timestamps",
     "SELECT MIN(request_datetime) AS min_request, MAX(request_datetime) AS max_request, "
     "MIN(pickup_datetime) AS min_pickup, MAX(pickup_datetime) AS max_pickup, "
     "MIN(dropoff_datetime) AS min_dropoff, MAX(dropoff_datetime) AS max_dropoff, "
     "COUNT(*) FILTER (WHERE request_datetime > pickup_datetime) AS request_after_pickup, "
     "COUNT(*) FILTER (WHERE dropoff_datetime < pickup_datetime) AS dropoff_before_pickup, "
     "COUNT(*) FILTER (WHERE pickup_datetime < '2026-06-01' OR pickup_datetime >= '2026-07-01') AS pickup_outside_june "
     "FROM nyc.trips"),
    ("Numbers: min / max / avg / how many below 0 and equal to 0",
     "SELECT " + ", ".join(
         f"MIN({c}) AS {c}_min, MAX({c}) AS {c}_max, ROUND(AVG({c})::numeric, 2) AS {c}_avg, "
         f"COUNT(*) FILTER (WHERE {c} < 0) AS {c}_neg, COUNT(*) FILTER (WHERE {c} = 0) AS {c}_zero"
         for c in NUMERIC) + " FROM nyc.trips"),
    ("Companies", "SELECT t.hvfhs_license_num, c.company, COUNT(*) AS trips FROM nyc.trips t "
                  "LEFT JOIN nyc.companies c USING (hvfhs_license_num) GROUP BY 1, 2 ORDER BY 3 DESC"),
    ("Dispatching bases", "SELECT COUNT(DISTINCT dispatching_base_num) AS bases, "
                          "COUNT(DISTINCT originating_base_num) AS originating_bases FROM nyc.trips"),
] + [
    (f"Flag {f}", f"SELECT {f} AS value, COUNT(*) AS trips FROM nyc.trips GROUP BY 1 ORDER BY 2 DESC") for f in FLAGS
] + [
    ("Airport fee values", "SELECT airport_fee, COUNT(*) AS trips FROM nyc.trips GROUP BY 1 ORDER BY 2 DESC LIMIT 8"),
    ("CBD congestion fee values", "SELECT cbd_congestion_fee, COUNT(*) AS trips FROM nyc.trips GROUP BY 1 ORDER BY 2 DESC LIMIT 8"),
    ("Zones: per borough and service zone",
     "SELECT borough, service_zone, COUNT(*) AS zones FROM nyc.zones GROUP BY 1, 2 ORDER BY 1, 2"),
    ("Airport zones", "SELECT zone_id, borough, zone, service_zone FROM nyc.zones "
                      "WHERE zone ILIKE '%airport%' OR service_zone IN ('Airports', 'EWR') ORDER BY 1"),
    ("Pickup zone ids not in nyc.zones",
     "SELECT COUNT(*) AS trips FROM nyc.trips t WHERE NOT EXISTS "
     "(SELECT 1 FROM nyc.zones z WHERE z.zone_id = t.pickup_zone_id)"),
    ("Pickups per borough (join, timed)",
     "SELECT z.borough, COUNT(*) AS trips FROM nyc.trips t JOIN nyc.zones z ON z.zone_id = t.pickup_zone_id "
     "GROUP BY 1 ORDER BY 2 DESC"),
    ("Trips per day: fewest and most",
     "(SELECT pickup_datetime::date AS day, COUNT(*) AS trips FROM nyc.trips GROUP BY 1 ORDER BY 2 LIMIT 3) "
     "UNION ALL (SELECT pickup_datetime::date, COUNT(*) FROM nyc.trips GROUP BY 1 ORDER BY 2 DESC LIMIT 3)"),
    ("Indexes on nyc tables", "SELECT tablename, indexname FROM pg_indexes WHERE schemaname = 'nyc' ORDER BY 1, 2"),
]


def main() -> None:
    connection = psycopg2.connect(**reader_connection_details())
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("SET statement_timeout = '600s'")  # profiling only; the agent keeps its 15 s limit
        cursor.execute("SHOW server_version")
        print(f"PostgreSQL {cursor.fetchone()[0]}\n")
        for title, query in QUERIES:
            start = time.perf_counter()
            cursor.execute(query)
            seconds = time.perf_counter() - start
            names = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
            print(f"== {title}  ({seconds:.1f} s)")
            if len(rows) == 1 and len(names) > 6:   # one wide row: print one value per line
                for name, value in zip(names, rows[0]):
                    print(f"  {name}: {value}")
            else:
                print("  " + " | ".join(names))
                for row in rows:
                    print("  " + " | ".join(str(v) for v in row))
            print()
    connection.close()


if __name__ == "__main__":
    main()
