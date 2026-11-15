"""Try a mode with fake data: sign in to that persona's demo account."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import web
from app.services import accounts, demo_modes

router = APIRouter()


@router.post("/demo/{mode}")
def try_demo_mode(request: Request, mode: str):
    if mode not in demo_modes.DEMOS:
        raise HTTPException(status_code=404, detail="Unknown demo mode.")
    account = demo_modes.load_demo(mode, web.local_now())
    accounts.start_session(request, account)
    return RedirectResponse("/dashboard", status_code=303)
