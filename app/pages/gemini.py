
from fastapi import APIRouter, HTTPException, Query

from app.services.gemini import get_nap_advice

router = APIRouter()


@router.get("/api/nap-advice")
def nap_advice(
    hours_slept: float = Query(default=6.0, ge=0, le=24),
    energy_level: int = Query(default=2, ge=1, le=5)
):
    try:
        return get_nap_advice(hours_slept, energy_level)
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Gemini recommendation failed. Check server logs."
        )
