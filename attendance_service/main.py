"""
Attendance Service - Port 8085
Logs daily member attendance. Verifies member exists and is Active
by calling the Member Service before recording attendance.
"""

from fastapi import FastAPI, HTTPException, Depends
from auth import verify_token, create_access_token
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
import httpx
import os
from datetime import date

# ─── App Setup ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Attendance Service",
    description="Logs daily gym attendance after verifying member status with the Member Service.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

DB_PATH = os.path.join(os.path.dirname(__file__), "attendance.db")
MEMBER_SERVICE_URL = os.getenv("MEMBER_SERVICE_URL", "http://localhost:8081")
CLASS_SERVICE_URL = os.getenv("CLASS_SERVICE_URL", "http://localhost:8083")

# ─── Database Helpers ─────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id   INTEGER NOT NULL,
            date        TEXT    NOT NULL,
            check_in    TEXT    NOT NULL,
            notes       TEXT
        )
    """)
    try:
        conn.execute("ALTER TABLE attendance ADD COLUMN class_id INTEGER")
    except sqlite3.OperationalError:
        pass # Column already exists
    conn.commit()
    conn.close()


init_db()

# ─── Pydantic Models ──────────────────────────────────────────────────────────

class AttendanceCreate(BaseModel):
    member_id: int
    class_id: Optional[int] = None
    date: Optional[str] = None        # ISO date "YYYY-MM-DD", defaults to today
    check_in: Optional[str] = None    # HH:MM, defaults to current time
    notes: Optional[str] = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "member_id": 1,
                "class_id": 2,
                "date": "2025-06-15",
                "check_in": "08:30",
                "notes": "Morning session",
            }
        }
    }


class AttendanceResponse(BaseModel):
    id: int
    member_id: int
    class_id: Optional[int] = None
    date: str
    check_in: str
    notes: Optional[str]


# ─── Inter-Service Helpers ────────────────────────────────────────────────────

async def verify_active_member(member_id: int) -> dict:
    """
    Call Member Service to verify:
    1. The member exists.
    2. The member's status is 'Active'.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{MEMBER_SERVICE_URL}/members/{member_id}", headers={"Authorization": "Bearer " + create_access_token({"sub": "system"})})
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail="Member Service is unavailable. Cannot verify member status.",
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Member Service timed out during verification.",
        )

    if response.status_code == 404:
        raise HTTPException(
            status_code=422,
            detail=f"Member with ID {member_id} does not exist.",
        )
    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail="Unexpected error from Member Service.",
        )

    member_data = response.json()
    if member_data.get("status") != "Active":
        raise HTTPException(
            status_code=403,
            detail=f"Member {member_id} has an '{member_data.get('status')}' membership. "
                   "Only Active members can log attendance.",
        )

    return member_data

async def verify_class_exists(class_id: int):
    """Call Class Service to verify the class exists."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{CLASS_SERVICE_URL}/classes/{class_id}", headers={"Authorization": "Bearer " + create_access_token({"sub": "system"})})
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Class Service is unavailable.")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Class Service timed out.")

    if response.status_code == 404:
        raise HTTPException(status_code=422, detail=f"Class with ID {class_id} does not exist.")
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Unexpected error from Class Service.")
    return response.json()

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
def health_check():
    return {"service": "Attendance Service", "status": "running", "port": 8085}


@app.post("/attendance", response_model=AttendanceResponse, status_code=201, tags=["Attendance"])
async def log_attendance(record: AttendanceCreate, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    # Step 1: Verify member is Active via Member Service
    await verify_active_member(record.member_id)

    # Step 1.5: Verify class exists if class_id provided
    if record.class_id is not None:
        await verify_class_exists(record.class_id)

    # Step 2: Apply defaults for date/check_in
    attendance_date = record.date or str(date.today())
    from datetime import datetime
    check_in_time = record.check_in or datetime.now().strftime("%H:%M")

    # Step 3: Persist the record
    cursor = db.execute(
        "INSERT INTO attendance (member_id, class_id, date, check_in, notes) VALUES (?, ?, ?, ?, ?)",
        (record.member_id, record.class_id, attendance_date, check_in_time, record.notes),
    )
    db.commit()

    row = db.execute("SELECT * FROM attendance WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


@app.get("/attendance", response_model=List[AttendanceResponse], tags=["Attendance"])
def list_attendance(db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    rows = db.execute("SELECT * FROM attendance ORDER BY date DESC, check_in DESC").fetchall()
    return [dict(r) for r in rows]


@app.get("/attendance/member/{member_id}", response_model=List[AttendanceResponse], tags=["Attendance"])
def get_attendance_by_member(member_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    rows = db.execute(
        "SELECT * FROM attendance WHERE member_id = ? ORDER BY date DESC",
        (member_id,),
    ).fetchall()
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No attendance records found for member ID {member_id}.",
        )
    return [dict(r) for r in rows]


@app.get("/attendance/{record_id}", response_model=AttendanceResponse, tags=["Attendance"])
def get_attendance_record(record_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    row = db.execute("SELECT * FROM attendance WHERE id = ?", (record_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Attendance record {record_id} not found.")
    return dict(row)


@app.delete("/attendance/{record_id}", tags=["Attendance"])
def delete_attendance_record(record_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    existing = db.execute("SELECT * FROM attendance WHERE id = ?", (record_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Attendance record {record_id} not found.")
    db.execute("DELETE FROM attendance WHERE id = ?", (record_id,))
    db.commit()
    return {"detail": f"Attendance record {record_id} deleted successfully."}
