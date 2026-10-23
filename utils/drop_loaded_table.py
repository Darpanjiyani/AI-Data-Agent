"""
Remove a table that was loaded into the external schema, for example one loaded by mistake:

    uv run python utils/drop_loaded_table.py weather_toronto

It asks you to confirm, then drops external.<name> as the writer user, which can only touch the
external schema. The SQL agent stops seeing the table after the app restarts.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from utils.loads import drop_external_table  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: uv run python utils/drop_loaded_table.py <table name in the external schema>")
    name = sys.argv[1]
    if input(f"Drop external.{name}? This can't be undone. Type yes to confirm: ").strip().lower() != "yes":
        sys.exit("Nothing was dropped.")
    print(drop_external_table(name)["message"])
