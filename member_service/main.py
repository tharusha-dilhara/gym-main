"""
Member Service - Port 8081
Handles CRUD operations for gym members.
"""

from fastapi import FastAPI, HTTPException, Depends
from auth import verify_token, create_access_token
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, List
import sqlite3
import os
import httpx

# ─── App Setup ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Member Service",
    description="Manages gym member registration and status.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

DB_PATH = os.path.join(os.path.dirname(__file__), "members.db")
ATTENDANCE_SERVICE_URL = os.getenv("ATTENDANCE_SERVICE_URL", "http://localhost:8085")
TRAINER_SERVICE_URL = os.getenv("TRAINER_SERVICE_URL", "http://localhost:8082")

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
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            name     TEXT    NOT NULL,
            email    TEXT    NOT NULL UNIQUE,
            status   TEXT    NOT NULL DEFAULT 'Active'
        )
    """)
    try:
        cursor.execute("ALTER TABLE members ADD COLUMN trainer_id INTEGER")
    except sqlite3.OperationalError:
        pass # Column already exists
    conn.commit()
    conn.close()


init_db()

# ─── Pydantic Models ──────────────────────────────────────────────────────────

class MemberCreate(BaseModel):
    name: str
    email: EmailStr
    status: Optional[str] = "Active"
    trainer_id: Optional[int] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v not in ("Active", "Expired"):
            raise ValueError("status must be 'Active' or 'Expired'")
        return v

    model_config = {"json_schema_extra": {"example": {"name": "John Doe", "email": "john@gym.com", "status": "Active", "trainer_id": 1}}}


class MemberUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    status: Optional[str] = None
    trainer_id: Optional[int] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v is not None and v not in ("Active", "Expired"):
            raise ValueError("status must be 'Active' or 'Expired'")
        return v


class MemberResponse(BaseModel):
    id: int
    name: str
    email: str
    status: str
    trainer_id: Optional[int] = None

# ─── Inter-Service Helpers ────────────────────────────────────────────────────

async def verify_trainer_exists(trainer_id: int) -> dict:
    """Call Trainer Service to confirm the trainer exists."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{TRAINER_SERVICE_URL}/trainers/{trainer_id}", headers={"Authorization": "Bearer " + create_access_token({"sub": "system"})})
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Trainer Service is unavailable. Cannot verify trainer.")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Trainer Service timed out while verifying trainer.")

    if response.status_code == 404:
        raise HTTPException(status_code=422, detail=f"Trainer with ID {trainer_id} does not exist in Trainer Service.")
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Unexpected error from Trainer Service.")
    return response.json()


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
def health_check():
    return {"service": "Member Service", "status": "running", "port": 8081}


@app.post("/members", response_model=MemberResponse, status_code=201, tags=["Members"])
async def create_member(member: MemberCreate, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    if member.trainer_id is not None:
        await verify_trainer_exists(member.trainer_id)

    try:
        cursor = db.execute(
            "INSERT INTO members (name, email, status, trainer_id) VALUES (?, ?, ?, ?)",
            (member.name, member.email, member.status, member.trainer_id),
        )
        db.commit()
        row = db.execute("SELECT * FROM members WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Email '{member.email}' is already registered.")


@app.get("/members", response_model=List[MemberResponse], tags=["Members"])
def list_members(db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    rows = db.execute("SELECT * FROM members").fetchall()
    return [dict(r) for r in rows]


@app.get("/members/{member_id}", response_model=MemberResponse, tags=["Members"])
def get_member(member_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    row = db.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Member with ID {member_id} not found.")
    return dict(row)


@app.get("/members/{member_id}/attendance", tags=["Members"])
async def get_member_attendance(member_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    row = db.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Member with ID {member_id} not found.")
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{ATTENDANCE_SERVICE_URL}/attendance/member/{member_id}", headers={"Authorization": "Bearer " + create_access_token({"sub": "system"})})
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Attendance Service is unavailable.")
    
    if response.status_code == 404:
        return []
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Unexpected error from Attendance Service.")
        
    return response.json()


@app.get("/members/{member_id}/progress", tags=["Members"])
async def get_member_progress(member_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    """Fetch attendance history from Attendance Service and return a progress summary."""
    row = db.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Member with ID {member_id} not found.")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{ATTENDANCE_SERVICE_URL}/attendance/member/{member_id}", headers={"Authorization": "Bearer " + create_access_token({"sub": "system"})})
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Attendance Service is unavailable. Cannot generate progress report.")
    
    attendance_records = []
    if response.status_code == 200:
        attendance_records = response.json()
    elif response.status_code != 404:
        raise HTTPException(status_code=response.status_code, detail="Unexpected error from Attendance Service.")

    total_classes = len(attendance_records)
    return {
        "member_id": member_id,
        "name": row["name"],
        "status": row["status"],
        "total_attendance": total_classes,
        "recent_attendance": attendance_records[:5],
        "message": f"Member has attended {total_classes} sessions."
    }


@app.put("/members/{member_id}", response_model=MemberResponse, tags=["Members"])
async def update_member(member_id: int, member: MemberUpdate, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    existing = db.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Member with ID {member_id} not found.")

    updates = {k: v for k, v in member.model_dump().items() if v is not None}
    if "trainer_id" in updates and updates["trainer_id"] is not None:
        await verify_trainer_exists(updates["trainer_id"])
    if not updates:
        return dict(existing)

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [member_id]

    try:
        db.execute(f"UPDATE members SET {set_clause} WHERE id = ?", values)
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Email already in use by another member.")

    row = db.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    return dict(row)


@app.delete("/members/{member_id}", tags=["Members"])
def delete_member(member_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    existing = db.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Member with ID {member_id} not found.")
    db.execute("DELETE FROM members WHERE id = ?", (member_id,))
    db.commit()
    return {"detail": f"Member {member_id} deleted successfully."}
