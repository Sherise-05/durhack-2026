import os
import asyncpg
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


async def connect_to_db():
    """
    Establish a connection to the TimescaleDB database.
    """
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        print("Connected to the database successfully!")
        return conn
    except Exception as e:
        print(f"Failed to connect to the database: {e}")
        raise


async def close_db_connection(conn):
    """
    Close the database connection.
    """
    try:
        await conn.close()
        print("Database connection closed.")
    except Exception as e:
        print(f"Failed to close the database connection: {e}")
        raise


async def execute_query(query, *args):
    """
    Execute a query on the database.
    """
    conn = await connect_to_db()
    try:
        result = await conn.fetch(query, *args)
        return result
    finally:
        await close_db_connection(conn)
