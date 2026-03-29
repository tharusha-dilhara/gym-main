"""
Class Service - Port 8083
Handles CRUD for gym classes, validates trainer ID from Trainer Service.
"""

from fastapi import FastAPI, HTTPException, Depends
from auth import verify_token, create_access_token
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
import httpx
import os

# ─── App Setup ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Class Service",
    description="Manages gym class schedules and links them to verified trainers.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

DB_PATH = os.path.join(os.path.dirname(__file__), "classes.db")
TRAINER_SERVICE_URL = os.getenv("TRAINER_SERVICE_URL", "http://localhost:8082")
MEMBER_SERVICE_URL = os.getenv("MEMBER_SERVICE_URL", "http://localhost:8081")
EQUIPMENT_SERVICE_URL = os.getenv("EQUIPMENT_SERVICE_URL", "http://localhost:8084")

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
        CREATE TABLE IF NOT EXISTS classes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            trainer_id  INTEGER NOT NULL,
            schedule    TEXT    NOT NULL,
            capacity    INTEGER NOT NULL DEFAULT 20
        )
    """)
    try:
        conn.execute("ALTER TABLE classes ADD COLUMN equipment_id INTEGER")
    except sqlite3.OperationalError:
        pass # Column might already exist
    conn.execute("""
        CREATE TABLE IF NOT EXISTS class_registrations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id    INTEGER NOT NULL,
            member_id   INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()


init_db()

# ─── Pydantic Models ──────────────────────────────────────────────────────────

class ClassCreate(BaseModel):
    name: str
    trainer_id: int
    schedule: str
    equipment_id: int
    capacity: Optional[int] = 20

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Morning Yoga",
                "trainer_id": 1,
                "schedule": "Mon/Wed/Fri 07:00",
                "equipment_id": 2,
                "capacity": 15,
            }
        }
    }


class ClassUpdate(BaseModel):
    name: Optional[str] = None
    trainer_id: Optional[int] = None
    schedule: Optional[str] = None
    equipment_id: Optional[int] = None
    capacity: Optional[int] = None


class ClassResponse(BaseModel):
    id: int
    name: str
    trainer_id: int
    schedule: str
    equipment_id: Optional[int] = None
    capacity: int


class ClassRegister(BaseModel):
    member_id: int


# ─── Inter-Service Helpers ────────────────────────────────────────────────────

async def verify_trainer_exists(trainer_id: int) -> dict:
    """Call Trainer Service to confirm the trainer exists."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{TRAINER_SERVICE_URL}/trainers/{trainer_id}", headers={"Authorization": "Bearer " + create_access_token({"sub": "system"})})
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail="Trainer Service is unavailable. Cannot verify trainer.",
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Trainer Service timed out while verifying trainer.",
        )

    if response.status_code == 404:
        raise HTTPException(
            status_code=422,
            detail=f"Trainer with ID {trainer_id} does not exist in Trainer Service.",
        )
    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail="Unexpected error from Trainer Service.",
        )
    return response.json()

async def verify_member_active(member_id: int) -> dict:
    """Call Member Service to check if member exists and is Active."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{MEMBER_SERVICE_URL}/members/{member_id}", headers={"Authorization": "Bearer " + create_access_token({"sub": "system"})})
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Member Service is unavailable.")
    
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Member with ID {member_id} not found.")
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Unexpected error from Member Service.")
        
    member_data = response.json()
    if member_data.get("status") == "Expired":
        raise HTTPException(status_code=403, detail="Registration blocked: Member account is Expired.")
    return member_data


async def verify_equipment_exists(equipment_id: int) -> dict:
    """Call Equipment Service to verify equipment exists and get its details."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{EQUIPMENT_SERVICE_URL}/equipment/{equipment_id}", headers={"Authorization": "Bearer " + create_access_token({"sub": "system"})})
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Equipment Service is unavailable.")
        
    if response.status_code == 404:
        raise HTTPException(status_code=422, detail=f"Equipment with ID {equipment_id} does not exist.")
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Unexpected error fetching equipment from Equipment Service.")
        
    return response.json()


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
def health_check():
    return {"service": "Class Service", "status": "running", "port": 8083}


@app.post("/classes", response_model=ClassResponse, status_code=201, tags=["Classes"])
async def create_class(gym_class: ClassCreate, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    await verify_trainer_exists(gym_class.trainer_id)
    eq_data = await verify_equipment_exists(gym_class.equipment_id)
    
    if eq_data.get("condition", "").lower() == "maintenance":
         raise HTTPException(
             status_code=400, 
             detail=f"Class creation blocked: Required equipment '{eq_data.get('name')}' is currently under Maintenance."
         )
    if int(eq_data.get("quantity", 0)) <= 0:
         raise HTTPException(
             status_code=400, 
             detail=f"Class creation blocked: Required equipment '{eq_data.get('name')}' is out of stock."
         )
    
    cursor = db.execute(
        "INSERT INTO classes (name, trainer_id, equipment_id, schedule, capacity) VALUES (?, ?, ?, ?, ?)",
        (gym_class.name, gym_class.trainer_id, gym_class.equipment_id, gym_class.schedule, gym_class.capacity),
    )
    db.commit()
    row = db.execute("SELECT * FROM classes WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


@app.get("/classes", response_model=List[ClassResponse], tags=["Classes"])
def list_classes(db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    rows = db.execute("SELECT * FROM classes").fetchall()
    return [dict(r) for r in rows]


@app.get("/classes/{class_id}", response_model=ClassResponse, tags=["Classes"])
def get_class(class_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    row = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Class with ID {class_id} not found.")
    return dict(row)

@app.get("/classes/schedule/{trainer_id}", response_model=List[ClassResponse], tags=["Classes"])
def get_trainer_schedule(trainer_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    rows = db.execute("SELECT * FROM classes WHERE trainer_id = ? ORDER BY schedule", (trainer_id,)).fetchall()
    return [dict(r) for r in rows]

@app.get("/classes/equipment/{equipment_id}", response_model=List[ClassResponse], tags=["Classes"])
def get_classes_by_equipment(equipment_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    rows = db.execute("SELECT * FROM classes WHERE equipment_id = ?", (equipment_id,)).fetchall()
    return [dict(r) for r in rows]

@app.post("/classes/{class_id}/register", tags=["Classes"])
async def register_for_class(class_id: int, payload: ClassRegister, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    row = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Class {class_id} not found.")
        
    await verify_member_active(payload.member_id)
    
    db.execute("INSERT INTO class_registrations (class_id, member_id) VALUES (?, ?)", (class_id, payload.member_id))
    db.commit()
    return {"message": f"Member {payload.member_id} successfully registered for class {class_id}."}


@app.post("/classes/{class_id}/start", tags=["Classes"])
async def start_class(class_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    row = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Class {class_id} not found.")

    equipment_id = dict(row).get("equipment_id")
    if not equipment_id:
        return {"message": f"Class {class_id} started successfully. No specific equipment required."}

    eq_data = await verify_equipment_exists(equipment_id)
    if eq_data.get("condition", "").lower() == "maintenance":
         raise HTTPException(
             status_code=400, 
             detail=f"Class start blocked: Required equipment '{eq_data.get('name')}' is currently under Maintenance."
         )

    return {"message": f"Class {class_id} started successfully. Required equipment '{eq_data.get('name')}' is operational."}


@app.put("/classes/{class_id}", response_model=ClassResponse, tags=["Classes"])
async def update_class(class_id: int, gym_class: ClassUpdate, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    existing = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Class with ID {class_id} not found.")

    updates = {k: v for k, v in gym_class.model_dump().items() if v is not None}

    if "trainer_id" in updates:
        await verify_trainer_exists(updates["trainer_id"])
    if "equipment_id" in updates:
        await verify_equipment_exists(updates["equipment_id"])

    if not updates:
        return dict(existing)

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [class_id]
    db.execute(f"UPDATE classes SET {set_clause} WHERE id = ?", values)
    db.commit()

    row = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
    return dict(row)


@app.delete("/classes/{class_id}", tags=["Classes"])
def delete_class(class_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    existing = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Class with ID {class_id} not found.")
    db.execute("DELETE FROM classes WHERE id = ?", (class_id,))
    db.commit()
    return {"detail": f"Class {class_id} deleted successfully."}
