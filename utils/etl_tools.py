import hashlib
import os
import re
import sys
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
import pandas as pd

from utils.code_guard import check_code, runtime_prelude

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


# The extract tool follows "next" links, but never more than this many pages per request
MAX_PAGES = 50

# Common places APIs put records, and the link to the next page
RECORD_KEYS = ("results", "data", "items", "records", "rows")
NEXT_KEYS = ("next", "next_url", "nextPage", "next_page")


def next_link(data, current_url: str):
    """The next page's URL, if the response has one ("next", or "links"/"paging" -> "next")."""
    if not isinstance(data, dict):
        return None
    candidates = [data.get(key) for key in NEXT_KEYS]
    for container in ("links", "paging"):
        if isinstance(data.get(container), dict):
            candidates.append(data[container].get("next"))
    for link in candidates:
        if isinstance(link, str) and link.strip():
            return urljoin(current_url, link.strip())
    return None


def _column_block(data: dict):
    """The key holding "columns of values" (a dict of equal-length lists), e.g. Open-Meteo's daily."""
    blocks = [key for key, value in data.items()
              if isinstance(value, dict) and value
              and all(isinstance(v, list) for v in value.values())
              and len({len(v) for v in value.values()}) == 1]
    if not blocks:
        return None
    return "daily" if "daily" in blocks else blocks[0]


# Fields an API adds about the request itself, not the data (Open-Meteo: how long it took)
RESPONSE_ONLY_FIELDS = ("generationtime_ms",)


def requested_points(url: str, count: int):
    """The (latitude, longitude) points a URL asked for, in order, if there are `count` of them.

    Weather APIs such as Open-Meteo answer each point with the coordinates of the grid cell
    they used, a few km away, so the response alone can't say which requested point
    (and so which city) each location is. The URL can: answers come back in its order."""
    query = parse_qs(urlparse(url).query)
    try:
        latitudes = [float(v) for v in query["latitude"][0].split(",")]
        longitudes = [float(v) for v in query["longitude"][0].split(",")]
    except (KeyError, ValueError):
        return None
    if len(latitudes) != count or len(longitudes) != count:
        return None
    return list(zip(latitudes, longitudes))


def response_to_table(data, url: str = "") -> tuple:
    """(DataFrame, note) for one API response."""
    if isinstance(data, dict) and _column_block(data):
        key = _column_block(data)
        frame = pd.DataFrame(data[key])
        points = requested_points(url, 1)
        if points:
            frame["requested_latitude"], frame["requested_longitude"] = points[0]
        for name, value in data.items():   # coordinates, timezone... repeated on every row
            if not isinstance(value, (dict, list)) and name not in frame.columns and name not in RESPONSE_ONLY_FIELDS:
                frame[name] = value
        return frame, f"the '{key}' values were stored as columns and were turned into one row per entry"
    if isinstance(data, list) and data and all(isinstance(item, dict) and _column_block(item) for item in data):
        points = requested_points(url, len(data))
        frames = []
        for index, item in enumerate(data):
            frame = response_to_table(item)[0]
            if points:   # which requested point this location answers, next to the data
                frame.insert(len(item[_column_block(item)]), "requested_latitude", points[index][0])
                frame.insert(len(item[_column_block(item)]) + 1, "requested_longitude", points[index][1])
            frames.append(frame)
        note = (f"the response held {len(data)} locations; they were combined into one table "
                "with each location's coordinates on its rows")
        if points:
            note += ("; requested_latitude and requested_longitude are the point each row answers, as written "
                     "in the URL, while latitude and longitude are the weather grid cell the API used, which can "
                     "be a few km away, so match locations on the requested columns")
        return pd.concat(frames, ignore_index=True), note
    if isinstance(data, dict):
        for key in RECORD_KEYS:
            if isinstance(data.get(key), list):
                return pd.json_normalize(data[key]), ""
    return pd.json_normalize(data), ""


def source_name(url: str) -> str:
    """A file name from the URL, e.g. rides_page1 or archive_3f9c1a (query string hashed)."""
    parts = urlparse(url)
    stem = Path(parts.path).stem or parts.netloc.split(":")[0] or "extracted_data"
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "extracted_data"
    if parts.query:
        stem += "_" + hashlib.sha1(parts.query.encode("utf-8")).hexdigest()[:6]
    return stem


def folder_snapshot(folder: Path) -> dict:
    """{file: modification time} for the files under a folder (empty if it doesn't exist)."""
    if not folder.exists():
        return {}
    return {f: f.stat().st_mtime for f in folder.rglob("*") if f.is_file()}


def describe_written_files(folder: Path, before: dict) -> list:
    """One line per file created or changed in `folder` since `before`: its path, rows and columns,
    so the agent reports what was really written instead of what it meant to write (EXP-14)."""
    lines = []
    for path, mtime in sorted(folder_snapshot(folder).items()):
        if before.get(path) == mtime:
            continue
        shown = path.relative_to(PROJECT_ROOT).as_posix() if path.is_relative_to(PROJECT_ROOT) else str(path)
        try:
            suffix = path.suffix.lower()
            frame = (pd.read_csv(path) if suffix == ".csv" else pd.read_parquet(path) if suffix == ".parquet"
                     else pd.read_json(path, lines=True) if suffix in (".json", ".jsonl") else None)
        except ValueError:
            frame = pd.read_json(path) if path.suffix.lower() == ".json" else None
        except Exception:
            frame = None
        if frame is None:
            lines.append(shown)
        else:
            columns = ", ".join(map(str, frame.columns[:12])) + (", ..." if len(frame.columns) > 12 else "")
            lines.append(f"{shown}: {len(frame)} rows; columns: {columns}")
    return lines


class ETLTools:

    def extract_load(self, url: str, output_folder: str, format: str) -> str:
        """
        Extract data from an API (url) and save it to output_folder (inside data/)
        as csv, json or parquet. Since EXP-12 part 2 the tool itself:
          - follows "next" links to read every page (same host only, at most MAX_PAGES),
          - turns "columns of values" responses (Open-Meteo's daily/hourly) into one row per
            time step, and a list of such responses into one table,
          - names the file after its source, so downloads don't overwrite each other,
          - reports pages, rows and columns, so the agent can't misreport what it got.
        """
        format = format.lower().strip(". ")
        if format not in ("csv", "json", "parquet"):
            return f"Unsupported format: {format}. Use csv, json or parquet."

        try:
            folder = resolve_data_path(output_folder)
        except ValueError as e:
            return str(e)

        frames, notes, pages, page_url, seen = [], [], 0, url, set()
        while page_url and pages < MAX_PAGES:
            seen.add(page_url)
            try:
                response = requests.get(page_url, timeout=30)
                response.raise_for_status()
                data = response.json()
            except requests.exceptions.RequestException as e:
                if pages == 0:
                    return f"Failed to extract data: {e}"
                notes.append(f"stopped at page {pages + 1} because it failed ({e}); the file has the first {pages} pages only")
                break
            except ValueError:
                if pages == 0:
                    return "Failed to extract data: the API did not return JSON."
                notes.append(f"stopped at page {pages + 1}: it wasn't JSON; the file has the first {pages} pages only")
                break
            pages += 1
            try:
                frame, note = response_to_table(data, page_url)
            except Exception as e:
                return f"Failed to convert the API response to a table: {e}"
            frames.append(frame)
            if note and note not in notes:
                notes.append(note)

            link = next_link(data, page_url)
            if link and urlparse(link).netloc != urlparse(url).netloc:
                notes.append(f"a next link to another host ({urlparse(link).netloc}) was not followed")
                link = None
            if link in seen:
                link = None
            page_url = link
        if page_url:
            notes.append(f"stopped after {MAX_PAGES} pages; more pages remain")

        df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
        folder.mkdir(parents=True, exist_ok=True)
        filename = folder / f"{source_name(url)}.{format}"

        try:
            if format == "csv":
                df.to_csv(filename, index=False)
            elif format == "json":
                df.to_json(filename, orient="records", lines=True)
            else:
                df.to_parquet(filename, index=False)
        except Exception as e:
            return f"Failed to save the data: {e}"

        columns = ", ".join(map(str, df.columns[:12])) + (", ..." if len(df.columns) > 12 else "")
        report = (f"Extracted {len(df)} rows from {pages} page{'s' if pages != 1 else ''} and saved them to "
                  f"{filename}. Columns: {columns}.")
        if notes:
            report += " Notes: " + "; ".join(notes) + "."
        return report

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

        Since EXP-12 the code guard runs first (utils/code_guard.py): code with a
        forbidden import or call is rejected without running, and the code that does run
        can only write inside data/, read data/ and Python's own files, and can't use the
        network or start other programs. That's a guard, not a container; a container
        (Docker) is still the stronger, later step.
        """
        problems = check_code(code)
        if problems:
            return ("Failed: the code guard rejected the code before running it: "
                    + "; ".join(problems) + ". Rewrite it using only pandas and the standard "
                    "data modules, reading and writing files inside the data/ folder only.")

        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", delete=False, encoding="utf-8"
        ) as script:
            script.write(runtime_prelude(DATA_DIR) + "\n# --- generated code ---\n" + code)
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
    print(tools.transform_load_context("data/extract/pokemon.csv"))
