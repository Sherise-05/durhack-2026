from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/sleep-planner", response_class=HTMLResponse)
async def sleep_planner(request: Request):
    return templates.TemplateResponse("sleep_planner.html", {"request": request})