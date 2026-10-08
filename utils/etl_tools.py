import os
import sys
import subprocess
import tempfile
from pathlib import Path

import requests
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

# Generated code gets at most this many seconds to run.
CODE_TIMEOUT_SECONDS = 60

# Environment variables that are NOT passed to generated code, so it can't
# read your API key or database passwords.
_SECRET_MARKERS = ("KEY", "PASSWORD", "PASSWD", "SECRET", "TOKEN", "CREDENTIAL")
_SECRET_NAMES = {"HOST", "PORT", "USER", "DATABASE"}  # lowercase DB vars used by feed_db.py


def resolve_data_path(path: str) -> Path:
    """
    Turn a user/LLM-provided path into an absolute path and make sure it is
    inside the project's data/ folder. Raises ValueError otherwise.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    candidate = candidate.resolve()

    data_dir = DATA_DIR.resolve()
    if candidate != data_dir and data_dir not in candidate.parents:
        raise ValueError(f"Path must be inside the data/ folder: {path}")

    return candidate


def _safe_environment() -> dict:
    """Copy of the environment without API keys, passwords or DB settings."""
    safe_env = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if upper.startswith(("DB_", "ANTHROPIC")):
            continue
        if upper in _SECRET_NAMES:
            continue
        if any(marker in upper for marker in _SECRET_MARKERS):
            continue
        safe_env[name] = value
    return safe_env


class ETLTools:

    def extract_load(self, url: str, output_folder: str, format: str) -> str:
        """
        Extract data from an API (url) and save it to output_folder
        (inside data/) as csv, json or parquet.
        """
        format = format.lower().strip(". ")
        if format not in ("csv", "json", "parquet"):
            return f"Unsupported format: {format}. Use csv, json or parquet."

        try:
            folder = resolve_data_path(output_folder)
        except ValueError as e:
            return str(e)

        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            return f"Failed to extract data: {e}"
        except ValueError:
            return "Failed to extract data: the API did not return JSON."

        # Many APIs wrap records in a "results" list; otherwise use the JSON as is.
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            records = data["results"]
        else:
            records = data

        try:
            df = pd.json_normalize(records)
        except Exception as e:
            return f"Failed to convert the API response to a table: {e}"

        folder.mkdir(parents=True, exist_ok=True)
        filename = folder / f"extracted_data.{format}"

        try:
            if format == "csv":
                df.to_csv(filename, index=False)
            elif format == "json":
                df.to_json(filename, orient="records", lines=True)
            else:
                df.to_parquet(filename, index=False)
        except Exception as e:
            return f"Failed to save the data: {e}"

        return f"Data successfully extracted ({len(df)} rows) and saved to {filename}"

    def transform_load_context(self, file_path: str) -> str:
        """
        Read a csv/json/parquet file inside data/ and return its first 3 rows,
        so the LLM can see the columns before writing pandas code.
        """
        path = resolve_data_path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        extension = path.suffix.lower()
        if extension == ".csv":
            df = pd.read_csv(path)
        elif extension == ".json":
            df = pd.read_json(path, lines=True)
        elif extension == ".parquet":
            df = pd.read_parquet(path)
        else:
            raise ValueError(f"Unsupported file format: {extension}")

        return str(df.head(3))

    def execute_code(self, code: str, timeout: int = CODE_TIMEOUT_SECONDS) -> str:
        """
        Run generated Python code in a SEPARATE process instead of exec():
          - it can't change or crash the main program,
          - it is stopped after `timeout` seconds,
          - it doesn't receive API keys or database passwords.

        Note: this is isolation, not a full sandbox. The code can still read and
        write files your user account can access. A container (Docker) is the
        next step for full sandboxing.
        """
        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", delete=False, encoding="utf-8"
        ) as script:
            script.write(code)
            script_path = script.name

        try:
            result = subprocess.run(
                [sys.executable, "-I", script_path],  # -I: isolated mode
                cwd=PROJECT_ROOT,
                env=_safe_environment(),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"Failed to execute code: it ran longer than {timeout} seconds and was stopped."
        finally:
            os.remove(script_path)

        output = (result.stdout or "")[-2000:]
        if result.returncode != 0:
            error = (result.stderr or "")[-2000:]
            return f"Failed to execute code (exit code {result.returncode}):\n{error}"

        return "Code executed successfully." + (f"\nOutput:\n{output}" if output.strip() else "")


if __name__ == "__main__":
    tools = ETLTools()
    print(tools.extract_load("https://pokeapi.co/api/v2/pokemon", "data/extract", "csv"))
    print(tools.transform_load_context("data/extract/extracted_data.csv"))
