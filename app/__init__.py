"""Application factory; importing the brain does not start the web application."""

from pathlib import Path
import os
import secrets
from urllib.parse import urlsplit
from urllib.parse import quote


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _origin(value: str):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return None
        port = parsed.port if parsed.port is not None else (
            443 if parsed.scheme == "https" else 80)
        return parsed.scheme, parsed.hostname, port
    except ValueError:
        return None


def _session_secret() -> str:
    configured = os.getenv("SESSION_SECRET", "").strip()
    if configured:
        return configured

    # Keep local sessions stable across reloads without putting secrets in Git.
    secret_path = PROJECT_ROOT / "instance" / "session_secret"
    secret_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(secret_path, os.O_WRONLY |
                             os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        secret = secret_path.read_text().strip()
        if not secret:
            raise RuntimeError(
                "The local session secret file is empty; set SESSION_SECRET.")
        return secret
    with os.fdopen(descriptor, "w") as secret_file:
        secret = secrets.token_urlsafe(48)
        secret_file.write(secret)
    return secret


def create_app(*, load_environment: bool = True):
    from dotenv import load_dotenv
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.middleware.sessions import SessionMiddleware

    if load_environment:
        load_dotenv(PROJECT_ROOT / ".env")

    from app.pages import auth, onboarding, calendar, dashboard, demo, nap_planner, sleep_history, sleep_planner, dream_journal
    from app.services import accounts, db

    db.init_db()
    app = FastAPI(title="Doze")

    @app.middleware("http")
    async def account_context(request, call_next):
        path = request.url.path
        request.state.account = None
        if path.startswith("/static/"):
            return await call_next(request)
        account = accounts.resolve_session(
            request.session.get(accounts.SESSION_KEY))
        request.state.account = account
        if account is None and request.method == "GET" and path in ("/", "/signup", "/login"):
            if canonical := accounts.canonical_entry_url(request):
                return RedirectResponse(canonical, status_code=303)
        public = path in ("/", "/signup", "/login",
                          "/logout", "/health", "/favicon.ico")
        setup = path == "/onboarding" or path.startswith("/onboarding/")
        wants_json = path.startswith("/api/") or path == "/onboarding/message"
        if account is None:
            if request.session.get(accounts.SESSION_KEY):
                request.session.clear()
            if not public:
                if wants_json:
                    return JSONResponse(status_code=401, content={"detail": "Sign in to continue.", "next": "/login"})
                next_path = path + \
                    ("?" + request.url.query if request.url.query else "")
                return RedirectResponse("/login?next=" + quote(next_path, safe=""), status_code=303)
        elif not account["onboarding_complete"] and not public and not setup:
            if wants_json:
                return JSONResponse(status_code=403, content={"detail": "Review and confirm your profile first.", "next": "/onboarding"})
            return RedirectResponse("/onboarding", status_code=303)
        elif account["onboarding_complete"] and setup:
            if wants_json:
                return JSONResponse(status_code=409, content={"detail": "Your initial setup is already complete.", "next": "/dashboard"})
            return RedirectResponse("/dashboard", status_code=303)
        with db.account_scope(account["id"] if account else None):
            if account and not request.session.get("google_connection_ref"):
                reference = db.latest_google_connection_reference()
                if reference:
                    request.session["google_connection_ref"] = reference
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.middleware("http")
    async def protect_local_writes(request, call_next):
        # Scripts without browser Origin headers still use normal session auth.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            cross_site = request.headers.get("sec-fetch-site") == "cross-site"
            if cross_site or (origin is not None and _origin(origin) != _origin(str(request.url))):
                return JSONResponse(status_code=403, content={"detail": "Open Doze directly before changing its data."})
        return await call_next(request)

    # Session parsing must wrap the account context middleware.
    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret(),
        same_site="lax",
        max_age=accounts.SESSION_DAYS * 24 * 60 * 60,
        https_only=os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true",
    )

    app.mount("/static", StaticFiles(directory=PROJECT_ROOT /
              "app" / "static"), name="static")
    for router in (auth.router, onboarding.router, dashboard.router, nap_planner.router, sleep_planner.router, calendar.router, sleep_history.router, dream_journal.router, demo.router):
        app.include_router(router)

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok"}

    return app
