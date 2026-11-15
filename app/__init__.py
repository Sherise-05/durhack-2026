import os
from dotenv import load_dotenv

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.services.db import init_db

from app.pages import (
    dashboard,
    nap_planner,
    sleep_planner,
    calendar,
    sleep_history,
    gemini,
    sleep_coach
)

# Load environment variables from .env
load_dotenv()


def create_app():
    app = FastAPI(title="Doze")

    # Initialise SQLite database
    init_db()

    # Enable sessions for Google OAuth
    app.add_middleware(
        SessionMiddleware,
        secret_key=os.environ["SESSION_SECRET"],
        same_site="lax",
        https_only=False  # Local development only
    )

    # Serve CSS and JavaScript
    app.mount(
        "/static",
        StaticFiles(directory="app/static"),
        name="static"
    )

    # Register routes
    app.include_router(dashboard.router)
    app.include_router(sleep_coach.router)
    app.include_router(gemini.router)
    app.include_router(nap_planner.router)
    app.include_router(sleep_planner.router)
    app.include_router(calendar.router)
    app.include_router(sleep_history.router)

    return app
