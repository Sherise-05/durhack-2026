from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.pages import (
    dashboard,
    nap_planner,
    sleep_planner,
    calendar,
    sleep_history
)


def create_app():
    app = FastAPI(title="Doze")

    # Serve CSS and JavaScript
    app.mount(
        "/static",
        StaticFiles(directory="app/static"),
        name="static"
    )

    # Include existing routes
    app.include_router(dashboard.router)
    app.include_router(nap_planner.router)
    app.include_router(sleep_planner.router)
    app.include_router(calendar.router)
    app.include_router(sleep_history.router)

    return app
