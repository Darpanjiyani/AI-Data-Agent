"""
Load a file into the external schema from the command line, with the same safeguards as the
app's Approve button: the load is prepared first, you review the summary, and nothing is
written until you type yes. Used to put the weather data into the public demo's database:

    uv run --env-file .env.cloud python utils/load_file.py data/demo/weather_daily.csv weather_daily
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")   # doesn't override settings passed with --env-file

from utils.loads import approve_load, cancel_load, prepare_load  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("Usage: uv run python utils/load_file.py <file inside data/> <table name>")
    prepared = prepare_load(sys.argv[1], sys.argv[2])
    print(prepared["message"])
    if not prepared.get("ok"):
        sys.exit(1)
    if input("\nLoad it now? Type yes to approve: ").strip().lower() != "yes":
        cancel_load(prepared["load_id"])
        sys.exit("Cancelled: nothing was loaded.")
    print(approve_load(prepared["load_id"])["message"])
