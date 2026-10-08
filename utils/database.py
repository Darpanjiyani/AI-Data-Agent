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


class DatabaseUtil:

    def __init__(self, db_config: dict):
        self.db_config = db_config

    def _connect(self):
        """Open a new read-only connection."""
        connection = psycopg2.connect(**self.db_config)
        connection.set_session(readonly=True)  # extra layer on top of the read-only user
        return connection

    def schema_details(self, schema_name: str) -> str:
        """
        Build a text description of every table in the schema: column names,
        data types and a few sample rows. This is given to the LLM as context
        for writing SQL.
        """
        try:
            connection = self._connect()
        except Exception as e:
            print(f"Error connecting to the database: {e}")
            return f"Error connecting to the database: {e}"

        schema_info_context = f"Database Schema: {schema_name}\n"

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

                for table_name in tables_list:
                    schema_info_context += f"\nTable: {table_name}\n"

                    # Column names and data types, in table order
                    cursor.execute(
                        """
                        SELECT column_name, data_type
                        FROM information_schema.columns
                        WHERE table_schema = %s AND table_name = %s
                        ORDER BY ordinal_position;
                        """,
                        (schema_name, table_name),
                    )
                    for column_name, data_type in cursor.fetchall():
                        schema_info_context += f" Column: {column_name}, Data Type: {data_type}\n"

                    # First 5 rows as examples. Identifiers are quoted safely.
                    cursor.execute(
                        sql.SQL("SELECT * FROM {}.{} LIMIT 5;").format(
                            sql.Identifier(schema_name), sql.Identifier(table_name)
                        )
                    )
                    for row in cursor.fetchall():
                        schema_info_context += f" Row: {row}\n"

        except Exception as e:
            print(f"Error retrieving schema details: {e}")
            schema_info_context = f"Error retrieving schema details: {e}"
        finally:
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

                columns = [col[0] for col in cursor.description]
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
