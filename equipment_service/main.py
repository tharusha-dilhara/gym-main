"""
Equipment Service - Port 8084
Manages gym equipment inventory and maintenance schedules.
"""

from fastapi import FastAPI, HTTPException, Depends
from auth import verify_token, create_access_token
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
import os
import httpx

# ─── App Setup ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Equipment Service",
    description="Manages gym equipment inventory and next maintenance dates.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

DB_PATH = os.path.join(os.path.dirname(__file__), "equipment.db")
CLASS_SERVICE_URL = os.getenv("CLASS_SERVICE_URL", "http://localhost:8083")
MEMBER_SERVICE_URL = os.getenv("MEMBER_SERVICE_URL", "http://localhost:8081")

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
        CREATE TABLE IF NOT EXISTS equipment (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT    NOT NULL,
            category            TEXT    NOT NULL,
            quantity            INTEGER NOT NULL DEFAULT 1,
            condition           TEXT    NOT NULL DEFAULT 'Good',
            last_maintenance    TEXT,
            next_maintenance    TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS breakdown_reports (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            equipment_id    INTEGER NOT NULL,
            member_id       INTEGER NOT NULL,
            issue           TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'Reported'
        )
    """)
    conn.commit()
    conn.close()


init_db()

# ─── Pydantic Models ──────────────────────────────────────────────────────────

class EquipmentCreate(BaseModel):
    name: str
    category: str
    quantity: Optional[int] = 1
    condition: Optional[str] = "Good"
    last_maintenance: Optional[str] = None  # ISO date string e.g. "2025-01-15"
    next_maintenance: Optional[str] = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Treadmill Pro 3000",
                "category": "Cardio",
                "quantity": 5,
                "condition": "Good",
                "last_maintenance": "2025-01-10",
                "next_maintenance": "2025-07-10",
            }
        }
    }


class EquipmentUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    quantity: Optional[int] = None
    condition: Optional[str] = None
    last_maintenance: Optional[str] = None
    next_maintenance: Optional[str] = None


class EquipmentResponse(BaseModel):
    id: int
    name: str
    category: str
    quantity: int
    condition: str
    last_maintenance: Optional[str]
    next_maintenance: Optional[str]


class BreakdownReportCreate(BaseModel):
    member_id: int
    issue: str


# ─── Inter-Service Helpers ────────────────────────────────────────────────────

async def verify_member_exists(member_id: int) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{MEMBER_SERVICE_URL}/members/{member_id}", headers={"Authorization": "Bearer " + create_access_token({"sub": "system"})})
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Member Service is unavailable.")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Member Service timed out.")

    if response.status_code == 404:
        raise HTTPException(status_code=422, detail=f"Member with ID {member_id} does not exist in Member Service.")
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Unexpected error from Member Service.")
    return response.json()


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
def health_check():
    return {"service": "Equipment Service", "status": "running", "port": 8084}


@app.post("/equipment", response_model=EquipmentResponse, status_code=201, tags=["Equipment"])
def create_equipment(item: EquipmentCreate, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    cursor = db.execute(
        """INSERT INTO equipment
           (name, category, quantity, condition, last_maintenance, next_maintenance)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (item.name, item.category, item.quantity, item.condition,
         item.last_maintenance, item.next_maintenance),
    )
    db.commit()
    row = db.execute("SELECT * FROM equipment WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


@app.get("/equipment", response_model=List[EquipmentResponse], tags=["Equipment"])
def list_equipment(db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    rows = db.execute("SELECT * FROM equipment").fetchall()
    return [dict(r) for r in rows]


@app.get("/equipment/{equipment_id}", response_model=EquipmentResponse, tags=["Equipment"])
def get_equipment(equipment_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    row = db.execute("SELECT * FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Equipment with ID {equipment_id} not found.")
    return dict(row)

@app.get("/equipment/{equipment_id}/classes", tags=["Equipment"])
async def get_equipment_classes(equipment_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    row = db.execute("SELECT * FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Equipment with ID {equipment_id} not found.")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{CLASS_SERVICE_URL}/classes/equipment/{equipment_id}", headers={"Authorization": "Bearer " + create_access_token({"sub": "system"})})
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Class Service is unavailable.")

    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Error fetching classes for equipment.")
        
    return response.json()


@app.put("/equipment/{equipment_id}", response_model=EquipmentResponse, tags=["Equipment"])
def update_equipment(equipment_id: int, item: EquipmentUpdate, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    existing = db.execute("SELECT * FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Equipment with ID {equipment_id} not found.")

    updates = {k: v for k, v in item.model_dump().items() if v is not None}
    if not updates:
        return dict(existing)

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [equipment_id]
    db.execute(f"UPDATE equipment SET {set_clause} WHERE id = ?", values)
    db.commit()

    row = db.execute("SELECT * FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
    return dict(row)


@app.delete("/equipment/{equipment_id}", tags=["Equipment"])
def delete_equipment(equipment_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    existing = db.execute("SELECT * FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Equipment with ID {equipment_id} not found.")
    db.execute("DELETE FROM equipment WHERE id = ?", (equipment_id,))
    db.commit()
    return {"detail": f"Equipment {equipment_id} deleted successfully."}


@app.get("/equipment/maintenance/due", response_model=List[EquipmentResponse], tags=["Equipment"])
def get_maintenance_due(db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    """Return all equipment whose next_maintenance date is today or in the past."""
    rows = db.execute(
        "SELECT * FROM equipment WHERE next_maintenance IS NOT NULL AND next_maintenance <= date('now')"
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/equipment/{equipment_id}/report-breakdown", tags=["Equipment"])
async def report_breakdown(equipment_id: int, payload: BreakdownReportCreate, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    row = db.execute("SELECT * FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Equipment with ID {equipment_id} not found.")

    await verify_member_exists(payload.member_id)

    db.execute(
        "INSERT INTO breakdown_reports (equipment_id, member_id, issue) VALUES (?, ?, ?)",
        (equipment_id, payload.member_id, payload.issue)
    )
    db.execute("UPDATE equipment SET condition = 'Maintenance' WHERE id = ?", (equipment_id,))
    db.commit()
    return {"message": f"Breakdown reported for Equipment {equipment_id} by Member {payload.member_id}. Condition updated to Maintenance."}


@app.get("/equipment/{equipment_id}/maintenance-schedule", tags=["Equipment"])
async def get_maintenance_schedule(equipment_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    """Fetch classes using this equipment from Class Service to find free time."""
    row = db.execute("SELECT * FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Equipment with ID {equipment_id} not found.")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{CLASS_SERVICE_URL}/classes/equipment/{equipment_id}", headers={"Authorization": "Bearer " + create_access_token({"sub": "system"})})
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Class Service is unavailable.")

    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Error fetching classes for equipment.")
        
    classes = response.json()
    schedules = [c["schedule"] for c in classes if c.get("schedule")]
    
    return {
        "equipment_id": equipment_id,
        "name": row["name"],
        "used_in_classes": schedules,
        "safe_maintenance_slots": "Any time outside the listed schedules."
    }
