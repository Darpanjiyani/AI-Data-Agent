import os
import psycopg2
from dotenv import load_dotenv
load_dotenv()

class DatabaseUtil:

    def __init__(self, db_config):
         self.db_config = db_config

         try:
            self.connection = psycopg2.connect(**db_config)

         except Exception as e:
            print(f"Error connecting to the database: {e}")
            self.connection = None


    def schema_details(self, schema_name):
         
         schema_info_context = ""       
    
         connection = self.connection
         cursor = connection.cursor()   #Cursor is the object that we get with the postgres connection and with the help of which we can run the queries.
                                                #This is the same cursor that created while pushing the data to the database as well.
         schema_info_context = f"Database Schema: {schema_name}\n" 

         try:
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s;", (schema_name,)) #
            tables_list = cursor.fetchall()  #this is covert our result in the form of list (fetchall)

            for table in tables_list:
                table_name = table[0]
                schema_info_context += f"{schema_info_context}\nTable: {table_name}\n"

                # Adding column details for each table & Data Types as well
                cursor.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = %s;", (table_name,))   #whenever you write cursor.execute, you just need to first of all write your query, wherever you have any variable simply write %s that's it.
                columns_list = cursor.fetchall()

                for column in columns_list:
                    column_name = column[0]
                    data_type = column[1]
                    schema_info_context = f"{schema_info_context} Column: {column_name}, Data Type: {data_type}\n"

                cursor.execute(f"SELECT * FROM {schema_name}.{table_name} LIMIT 5;")  # Fetching first 5 rows of the table
                sample_data = cursor.fetchall()
                schema_info_context = f"{schema_info_context} Sample Data: {sample_data}\n"

                for row in sample_data:
                    schema_info_context = f"{schema_info_context} Row: {row}\n" 

         except Exception as e:
            print(f"Error retrieving schema details: {e}")
            schema_info_context = f"Error retrieving schema details: {e}"
         finally:
            if cursor:
                 cursor.close()
            if connection:
                connection.close()

         return schema_info_context 

    def execute_query(self, query: str):
        connection = self.connection
        cursor = connection.cursor()
        try:
            cursor.execute(query)
            result = cursor.fetchall()  # Fetch the result of the query if needed
            connection.commit()
            return str(result)
        except Exception as e:
            print(f"Error executing query: {e}")
            return f"Error executing query: {e}"
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

obj = DatabaseUtil({
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT")),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
})

result = obj.schema_details("public")

with open("test_schema_details.txt", "w") as f:
    f.write(result)