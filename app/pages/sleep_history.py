
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from app.services.db import get_connection

router = APIRouter()


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
def get_sleep_history():
    with get_connection() as conn:
        records = conn.execute("""
            SELECT id, date, duration, quality
            FROM sleep_history
            ORDER BY date DESC
        """).fetchall()

    return [dict(record) for record in records]


@router.post("/sleep-history", response_model=SleepHistory)
def add_sleep_history(new_record: NewSleepHistory):
    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO sleep_history (date, duration, quality)
            VALUES (?, ?, ?)
        """, (
            new_record.date,
            new_record.duration,
            new_record.quality
        ))

        record_id = cursor.lastrowid

        record = conn.execute("""
            SELECT id, date, duration, quality
            FROM sleep_history
            WHERE id = ?
        """, (record_id,)).fetchone()

    return dict(record)


@router.delete("/sleep-history/{id}")
def delete_sleep_history(id: int):
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM sleep_history WHERE id = ?",
            (id,)
        )

        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Sleep record not found"
            )

    return {"message": f"Sleep record {id} deleted successfully"}
