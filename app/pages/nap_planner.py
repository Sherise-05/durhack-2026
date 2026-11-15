from fastapi import APIRouter, Form
from app.services.nap_recc import recommend_nap

router = APIRouter()

@router.post("/nap-planner")
async def nap_planner(tiredness: int = Form(...), goal: str = Form(...)):
    recommendation = recommend_nap(tiredness, goal)
    return recommendation