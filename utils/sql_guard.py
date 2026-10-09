"""
Rule-based SQL safety check.

This runs BEFORE the LLM safety judge. Unlike an LLM, a parser gives the same
answer every time, costs nothing and is instant. It only allows a single,
read-only SELECT query (including CTEs and UNIONs).

The read-only PostgreSQL user (agent_reader) is still the final line of
defence: even if something slips past this check, the database refuses writes.
"""

import re

import sqlglot
from sqlglot import exp

ANSI_CODES = re.compile(r"\x1b\[[0-9;]*m")

# Statements / clauses that must never appear anywhere in the query,
# including inside CTEs or subqueries.
BLOCKED_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Copy,
    exp.Command,  # anything sqlglot can't parse into a known statement (GRANT, VACUUM, ...)
    exp.Into,     # SELECT ... INTO new_table creates a table
    exp.Lock,     # SELECT ... FOR UPDATE / FOR SHARE locks rows
)

# PostgreSQL functions that can affect the server or read files.
BLOCKED_FUNCTIONS = {
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "lo_import",
    "lo_export",
    "dblink",
    "dblink_exec",
    "set_config",
}


def _function_name(func: exp.Func) -> str:
    if isinstance(func, exp.Anonymous):
        return str(func.name).lower()
    return func.sql_name().lower()


def is_read_only(sql: str) -> tuple[bool, str]:
    """
    Check that `sql` is exactly one read-only SELECT query.

    Returns:
        (True, "OK") if the query is allowed,
        (False, reason) otherwise.
    """
    if not sql or not sql.strip():
        return False, "The query is empty."

    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.SqlglotError as e:  # ParseError, TokenError, ... : fail closed
        # sqlglot adds terminal colour codes and a multi-line excerpt; keep one clean line
        message = ANSI_CODES.sub("", str(e)).splitlines()[0]
        return False, f"It isn't a valid SQL query ({message})."

    if len(statements) != 1:
        return False, f"Only one statement is allowed, found {len(statements)}."

    statement = statements[0]

    if not isinstance(statement, exp.Query):
        return False, f"Only SELECT queries are allowed (found: {statement.key.upper()})."

    for node_type in BLOCKED_NODES:
        node = statement.find(node_type)
        if node is not None:
            return False, f"The query contains a blocked operation ({node.key.upper()})."

    for func in statement.find_all(exp.Func):
        name = _function_name(func)
        if name in BLOCKED_FUNCTIONS:
            return False, f"The query calls a blocked function ({name})."

    return True, "OK"


if __name__ == "__main__":
    examples = [
        "SELECT payment_method, COUNT(*) FROM payments GROUP BY payment_method LIMIT 10;",
        "DELETE FROM rides WHERE status = 'cancelled'",
        "SELECT 1; DROP TABLE rides",
        "WITH d AS (DELETE FROM rides RETURNING *) SELECT * FROM d",
    ]
    for query in examples:
        print(is_read_only(query), "<-", query)
