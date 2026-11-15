
import os
import uvicorn
from dotenv import load_dotenv
from app import create_app

load_dotenv()

# Create the FastAPI application
app = create_app()

if __name__ == "__main__":
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", 8000))
    reload = os.getenv("DEBUG", "True").lower() == "true"

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload
    )
