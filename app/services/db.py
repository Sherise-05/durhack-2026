import os
import asyncpg
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Database configuration
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_SSLMODE = os.getenv("DB_SSLMODE", "require")

# Connection string
DATABASE_URL = f"postgres://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?sslmode={DB_SSLMODE}"


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
