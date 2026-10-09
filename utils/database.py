import os
import json
import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv

load_dotenv()


def reader_connection_details() -> dict:
    """
    Connection details for the read-only database user (agent_reader).

    The AI agent always connects with this user, so PostgreSQL itself blocks
    any write, even if an unsafe query gets past the other safety checks.
    """
    user = os.getenv("DB_READER_USER")
    password = os.getenv("DB_READER_PASSWORD")
    if not user or not password:
        raise ValueError(
            "DB_READER_USER and DB_READER_PASSWORD must be set in .env. "
            "The agent only connects with the read-only database user."
        )

    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "dbname": os.getenv("DB_NAME", "postgres"),
        "user": user,
        "password": password,
    }


def _unique_column_names(columns: list) -> list:
    """
    Make column names unique, e.g. ["extract", "extract"] -> ["extract", "extract_2"].
    Without this, two columns with the same name (common with unnamed
    expressions like EXTRACT(...) or COUNT(*)) would overwrite each other.
    """
    seen = {}
    unique = []
    for name in columns:
        if name in seen:
            seen[name] += 1
            unique.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 1
            unique.append(name)
    return unique


class DatabaseUtil:

    def __init__(self, db_config: dict):
        self.db_config = db_config

    def _connect(self):
        """Open a new read-only connection."""
        connection = psycopg2.connect(**self.db_config)
        connection.set_session(readonly=True)  # extra layer on top of the read-only user
        return connection

    def schema_details(
        self,
        schema_name: str,
        tables: list | None = None,
        sample_rows: int = 3,
        masked_columns: tuple = ("email", "phone"),
        max_listed_values: int = 10,
    ) -> str:
        """
        Build a compact text description of the database for the LLM:

        - each table's columns and data types,
        - the allowed values of short text columns (e.g. status: cancelled, completed, ...),
        - a few sample rows, with personal columns (email, phone) hidden,
        - the relationships between tables (foreign keys).

        Args:
            schema_name: database schema to describe, usually "public".
            tables: only describe these tables (None = every table in the schema).
            sample_rows: number of example rows per table.
            masked_columns: columns whose sample values are replaced with <hidden>.
            max_listed_values: list a text column's values if it has at most this many.
        """
        try:
            connection = self._connect()
        except Exception as e:
            print(f"Error connecting to the database: {e}")
            return f"Error connecting to the database: {e}"

        lines = [f"Database schema: {schema_name}"]

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = %s
                    ORDER BY table_name;
                    """,
                    (schema_name,),
                )
                tables_list = [row[0] for row in cursor.fetchall()]
                if tables is not None:
                    tables_list = [t for t in tables_list if t in tables]

                for table_name in tables_list:
                    table_id = sql.SQL("{}.{}").format(sql.Identifier(schema_name), sql.Identifier(table_name))
                    lines.append(f"\nTable: {table_name}")

                    # Columns and data types, in table order
                    cursor.execute(
                        """
                        SELECT column_name, data_type
                        FROM information_schema.columns
                        WHERE table_schema = %s AND table_name = %s
                        ORDER BY ordinal_position;
                        """,
                        (schema_name, table_name),
                    )
                    columns = cursor.fetchall()

                    for column_name, data_type in columns:
                        line = f"  - {column_name} ({data_type})"

                        # List the values of short text columns so the LLM uses exact spellings
                        if data_type in ("character varying", "text") and column_name not in masked_columns:
                            cursor.execute(
                                sql.SQL("SELECT DISTINCT {} FROM {} LIMIT %s").format(
                                    sql.Identifier(column_name), table_id
                                ),
                                (max_listed_values + 1,),
                            )
                            values = [row[0] for row in cursor.fetchall()]
                            if len(values) <= max_listed_values:
                                shown = sorted(repr(v) if v is not None else "NULL" for v in values)
                                line += f" values: {', '.join(shown)}"
                        lines.append(line)

                    # A few example rows, with personal data hidden
                    cursor.execute(
                        sql.SQL("SELECT * FROM {} LIMIT %s").format(table_id), (sample_rows,)
                    )
                    column_names = [desc[0] for desc in cursor.description]
                    lines.append(f"  Sample rows ({' | '.join(column_names)}):")
                    for row in cursor.fetchall():
                        cells = []
                        for name, value in zip(column_names, row):
                            if name in masked_columns:
                                cells.append("<hidden>")
                            elif value is None:
                                cells.append("NULL")
                            else:
                                cells.append(str(value))
                        lines.append("    " + " | ".join(cells))

                # Relationships between the described tables (read from pg_catalog,
                # which, unlike information_schema, is visible to a read-only user)
                cursor.execute(
                    """
                    SELECT src.relname, a.attname, dst.relname, af.attname
                    FROM pg_constraint c
                    JOIN pg_class src ON src.oid = c.conrelid
                    JOIN pg_class dst ON dst.oid = c.confrelid
                    JOIN pg_namespace n ON n.oid = c.connamespace
                    CROSS JOIN LATERAL unnest(c.conkey, c.confkey) AS k(attnum, fattnum)
                    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                    JOIN pg_attribute af ON af.attrelid = c.confrelid AND af.attnum = k.fattnum
                    WHERE c.contype = 'f' AND n.nspname = %s
                    ORDER BY 1, 2;
                    """,
                    (schema_name,),
                )
                relationships = [
                    f"  - {src}.{col} -> {dst}.{dst_col}"
                    for src, col, dst, dst_col in cursor.fetchall()
                    if src in tables_list and dst in tables_list
                ]
                if relationships:
                    lines.append("\nRelationships (foreign keys):")
                    lines.extend(relationships)

            schema_info_context = "\n".join(lines)

        except Exception as e:
            print(f"Error retrieving schema details: {e}")
            schema_info_context = f"Error retrieving schema details: {e}"
        finally:
            connection.rollback()
            connection.close()

        return schema_info_context

    def execute_query(self, query: str) -> str:
        """
        Run a read-only query and return the result as JSON text, with column
        names, e.g. [{"payment_method": "paypal", "count": 3215}, ...].
        """
        try:
            connection = self._connect()
        except Exception as e:
            print(f"Error connecting to the database: {e}")
            return f"Error connecting to the database: {e}"

        try:
            with connection.cursor() as cursor:
                cursor.execute(query)

                if cursor.description is None:
                    return "The query did not return any rows."

                columns = _unique_column_names([col[0] for col in cursor.description])
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

            # default=str converts dates, timestamps and decimals to text
            return json.dumps(rows, default=str)

        except Exception as e:
            print(f"Error executing query: {e}")
            return f"Error executing query: {e}"
        finally:
            # No commit: the agent only reads. Rolling back ends the transaction cleanly.
            connection.rollback()
            connection.close()


if __name__ == "__main__":
    # Writes an example of the schema context the LLM receives.
    db = DatabaseUtil(reader_connection_details())
    result = db.schema_details("public")

    with open("test_schema_details.txt", "w") as f:
        f.write(result)

    print(result)
