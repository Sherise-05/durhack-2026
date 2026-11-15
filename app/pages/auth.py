"""Welcome, signup, login and logout entry points for the real user flow."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import web
from app.services import accounts

router = APIRouter()


def _account(request: Request):
    return getattr(request.state, "account", None)


def _auth_page(request: Request, mode: str, *, error=None, values=None, next_path=None, status_code=200):
    return web.templates.TemplateResponse(request=request, name="auth.html", context={
        "mode": mode, "error": error, "values": values or {}, "next_path": accounts.safe_next(next_path),
    }, status_code=status_code)


@router.get("/", response_class=HTMLResponse)
def welcome(request: Request):
    if account := _account(request):
        return RedirectResponse(accounts.destination(account), status_code=303)
    return web.templates.TemplateResponse(request=request, name="welcome.html", context={})


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    if account := _account(request):
        return RedirectResponse(accounts.destination(account), status_code=303)
    return _auth_page(request, "signup")


@router.post("/signup", response_class=HTMLResponse)
def signup(request: Request, name: Annotated[str, Form(max_length=80)],
           email: Annotated[str, Form(max_length=254)], password: Annotated[str, Form()]):
    try:
        account = accounts.register(email, password, name)
    except ValueError as error:
        return _auth_page(request, "signup", error=str(error), values={"name": name, "email": email}, status_code=422)
    accounts.start_session(request, account)
    return RedirectResponse("/onboarding", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str | None = None):
    if account := _account(request):
        return RedirectResponse(accounts.destination(account), status_code=303)
    return _auth_page(request, "login", next_path=next)


@router.post("/login", response_class=HTMLResponse)
def login(request: Request, email: Annotated[str, Form(max_length=254)],
          password: Annotated[str, Form()], next: Annotated[str, Form(max_length=2048)] = "/dashboard"):
    ip = request.client.host if request.client else "unknown"
    if not accounts.login_allowed(ip, email):
        return _auth_page(request, "login", error="Too many unsuccessful attempts. Try again in 15 minutes.",
                          values={"email": email}, next_path=next, status_code=429)
    account = accounts.authenticate(email, password)
    accounts.record_login(ip, email, successful=account is not None)
    if account is None:
        return _auth_page(request, "login", error="The email or password is incorrect.",
                          values={"email": email}, next_path=next, status_code=401)
    accounts.start_session(request, account)
    target = accounts.safe_next(next) if account["onboarding_complete"] else "/onboarding"
    return RedirectResponse(target, status_code=303)


@router.post("/logout")
def logout(request: Request):
    accounts.end_session(request)
    return RedirectResponse("/", status_code=303)
