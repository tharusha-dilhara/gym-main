"""
Trainer Service - Port 8082
Handles CRUD operations for gym trainers.
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
    title="Trainer Service",
    description="Manages gym trainer profiles and specializations.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

DB_PATH = os.path.join(os.path.dirname(__file__), "trainers.db")
CLASS_SERVICE_URL = os.getenv("CLASS_SERVICE_URL", "http://localhost:8083")
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
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trainers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL,
            specialization  TEXT    NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS equipment_reservations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            trainer_id      INTEGER NOT NULL,
            equipment_id    INTEGER NOT NULL,
            reservation_date TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


init_db()

# ─── Pydantic Models ──────────────────────────────────────────────────────────

class TrainerCreate(BaseModel):
    name: str
    specialization: str

    model_config = {"json_schema_extra": {"example": {"name": "Alice Smith", "specialization": "Yoga"}}}


class TrainerUpdate(BaseModel):
    name: Optional[str] = None
    specialization: Optional[str] = None


class TrainerResponse(BaseModel):
    id: int
    name: str
    specialization: str

class ReservationCreate(BaseModel):
    equipment_id: int
    reservation_date: str

# ─── Inter-Service Helpers ────────────────────────────────────────────────────

async def verify_equipment_exists(equipment_id: int) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{EQUIPMENT_SERVICE_URL}/equipment/{equipment_id}", headers={"Authorization": "Bearer " + create_access_token({"sub": "system"})})
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Equipment Service is unavailable.")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Equipment Service timed out.")

    if response.status_code == 404:
        raise HTTPException(status_code=422, detail=f"Equipment with ID {equipment_id} does not exist.")
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Unexpected error from Equipment Service.")
    return response.json()


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
def health_check():
    return {"service": "Trainer Service", "status": "running", "port": 8082}


@app.post("/trainers", response_model=TrainerResponse, status_code=201, tags=["Trainers"])
def create_trainer(trainer: TrainerCreate, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    cursor = db.execute(
        "INSERT INTO trainers (name, specialization) VALUES (?, ?)",
        (trainer.name, trainer.specialization),
    )
    db.commit()
    row = db.execute("SELECT * FROM trainers WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


@app.get("/trainers", response_model=List[TrainerResponse], tags=["Trainers"])
def list_trainers(db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    rows = db.execute("SELECT * FROM trainers").fetchall()
    return [dict(r) for r in rows]


@app.get("/trainers/{trainer_id}", response_model=TrainerResponse, tags=["Trainers"])
def get_trainer(trainer_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    row = db.execute("SELECT * FROM trainers WHERE id = ?", (trainer_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Trainer with ID {trainer_id} not found.")
    return dict(row)

@app.get("/trainers/{trainer_id}/schedule", tags=["Trainers"])
async def get_trainer_schedule(trainer_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    row = db.execute("SELECT * FROM trainers WHERE id = ?", (trainer_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Trainer with ID {trainer_id} not found.")
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{CLASS_SERVICE_URL}/classes/schedule/{trainer_id}", headers={"Authorization": "Bearer " + create_access_token({"sub": "system"})})
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Class Service is unavailable.")
        
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching schedule from Class Service.")
        
    return response.json()


@app.put("/trainers/{trainer_id}", response_model=TrainerResponse, tags=["Trainers"])
def update_trainer(trainer_id: int, trainer: TrainerUpdate, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    existing = db.execute("SELECT * FROM trainers WHERE id = ?", (trainer_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Trainer with ID {trainer_id} not found.")

    updates = {k: v for k, v in trainer.model_dump().items() if v is not None}
    if not updates:
        return dict(existing)

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [trainer_id]
    db.execute(f"UPDATE trainers SET {set_clause} WHERE id = ?", values)
    db.commit()

    row = db.execute("SELECT * FROM trainers WHERE id = ?", (trainer_id,)).fetchone()
    return dict(row)


@app.post("/trainers/{trainer_id}/equipment-reservations", tags=["Trainers"])
async def reserve_equipment(trainer_id: int, payload: ReservationCreate, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    row = db.execute("SELECT * FROM trainers WHERE id = ?", (trainer_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Trainer with ID {trainer_id} not found.")

    await verify_equipment_exists(payload.equipment_id)

    cursor = db.execute(
        "INSERT INTO equipment_reservations (trainer_id, equipment_id, reservation_date) VALUES (?, ?, ?)",
        (trainer_id, payload.equipment_id, payload.reservation_date)
    )
    db.commit()
    return {"message": f"Equipment {payload.equipment_id} successfully reserved for Trainer {trainer_id} on {payload.reservation_date}."}


@app.delete("/trainers/{trainer_id}", tags=["Trainers"])
def delete_trainer(trainer_id: int, db: sqlite3.Connection = Depends(get_db), current_user: str = Depends(verify_token)):
    existing = db.execute("SELECT * FROM trainers WHERE id = ?", (trainer_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail=f"Trainer with ID {trainer_id} not found.")
    db.execute("DELETE FROM trainers WHERE id = ?", (trainer_id,))
    db.commit()
    return {"detail": f"Trainer {trainer_id} deleted successfully."}
