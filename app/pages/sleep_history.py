from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from app.services.db import get_connection
router = APIRouter()

# Pydantic model for sleep history


class SleepHistory(BaseModel):
    id: int
    date: str
    duration: float
    quality: str


class NewSleepHistory(BaseModel):
    date: str
    duration: float
    quality: str


@router.get("/sleep-history", response_model=List[SleepHistory])
async def get_sleep_history():
    """
    Fetch all sleep history records from the database.
    """
    query = "SELECT id, date, duration, quality FROM sleep_history ORDER BY date DESC;"
    try:
        records = await get_connection(query)
        return [dict(record) for record in records]
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch sleep history: {e}")


@router.post("/sleep-history", response_model=SleepHistory)
async def add_sleep_history(new_record: NewSleepHistory):
    """
    Add a new sleep history record to the database.
    """
    query = """
    INSERT INTO sleep_history (date, duration, quality)
    VALUES ($1, $2, $3)
    RETURNING id, date, duration, quality;
    """
    try:
        record = await get_connection(query, new_record.date, new_record.duration, new_record.quality)
        return dict(record[0])  # Return the first (and only) record
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to add sleep history: {e}")


@router.delete("/sleep-history/{id}")
async def delete_sleep_history(id: int):
    """
    Delete a sleep history record by ID.
    """
    query = "DELETE FROM sleep_history WHERE id = $1;"
    try:
        await get_connection(query, id)
        return {"message": f"Sleep history record with ID {id} deleted successfully."}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to delete sleep history: {e}")
