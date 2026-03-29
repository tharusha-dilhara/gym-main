"""
API Gateway - Port 8080
Routes all incoming requests to the appropriate downstream microservice
using httpx as a reverse proxy. Provides a unified entry point with
Swagger documentation listing all available routes by merging downstream OpenAPI schemas.
"""

import os
import sqlite3
from fastapi import FastAPI, Request, HTTPException, Depends, Body
from typing import Any
from fastapi.responses import JSONResponse
from fastapi.openapi.utils import get_openapi
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
import httpx
from typing import Dict, Any
from passlib.context import CryptContext
from auth import create_access_token, verify_token

# ─── App Setup ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Gym Management API Gateway",
    description="Unified API Gateway mirroring microservice routes exactly.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Auth & DB Setup ─────────────────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")

import hashlib

def get_password_hash(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return get_password_hash(plain_password) == hashed_password

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

class UserCreate(BaseModel):
    username: str
    password: str

# ─── Service Registry ─────────────────────────────────────────────────────────

SERVICE_MAP = {
    "members":    os.getenv("MEMBER_SERVICE_URL",    "http://member_service:8081"),
    "trainers":   os.getenv("TRAINER_SERVICE_URL",   "http://trainer_service:8082"),
    "classes":    os.getenv("CLASS_SERVICE_URL",     "http://class_service:8083"),
    "equipment":  os.getenv("EQUIPMENT_SERVICE_URL", "http://equipment_service:8084"),
    "attendance": os.getenv("ATTENDANCE_SERVICE_URL","http://attendance_service:8085"),
}

# Shared async client with reasonable timeouts
http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))

# ─── Proxy Forwarder ────────────────────────────────────────────────────────
async def forward_request(base_url: str, request: Request):
    path = request.url.path
    query = request.url.query
    
    target_url = f"{base_url}{path}"
    if query:
        target_url += f"?{query}"

    body = await request.body()
    forward_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }

    try:
        upstream_response = await http_client.request(
            method=request.method,
            url=target_url,
            headers=forward_headers,
            content=body,
        )
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail=f"Downstream service at {base_url} is not reachable.")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail=f"Downstream service at {base_url} timed out.")

    try:
        json_body = upstream_response.json()
    except Exception:
        json_body = {"raw": upstream_response.text}

    return JSONResponse(
        content=json_body,
        status_code=upstream_response.status_code,
        headers={
            k: v for k, v in upstream_response.headers.items()
            if k.lower() not in ("content-length", "transfer-encoding")
        },
    )

# ─── Gateway Routes ──────────────────────────────────────────────────────────

@app.get("/", tags=["Gateway"])
async def gateway_health():
    return {
        "service": "API Gateway",
        "status": "running",
        "port": 8080,
    }

@app.post("/register", tags=["Authentication"])
def register_user(user: UserCreate, db: sqlite3.Connection = Depends(get_db)):
    existing = db.execute("SELECT id FROM users WHERE username = ?", (user.username,)).fetchone()
    if existing:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    hashed_pwd = get_password_hash(user.password)
    db.execute("INSERT INTO users (username, hashed_password) VALUES (?, ?)", (user.username, hashed_pwd))
    db.commit()
    return {"message": "User registered successfully, you can now log in via /token"}

@app.post("/token", tags=["Authentication"])
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: sqlite3.Connection = Depends(get_db)):
    user = db.execute("SELECT * FROM users WHERE username = ?", (form_data.username,)).fetchone()
    if not user or not verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(status_code=400, detail="Incorrect username or password")

    access_token = create_access_token(data={"sub": form_data.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/members", tags=["Members"])
async def proxy_post_members_members(request: Request, payload: Dict[str, Any] = Body(None), current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["members"], request)

@app.get("/members", tags=["Members"])
async def proxy_get_members_members(request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["members"], request)

@app.get("/members/{member_id}", tags=["Members"])
async def proxy_get_members_members_member_id(member_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["members"], request)

@app.get("/members/{member_id}/attendance", tags=["Members"])
async def proxy_get_members_members_member_id_attendance(member_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["members"], request)

@app.get("/members/{member_id}/progress", tags=["Members"])
async def proxy_get_members_members_member_id_progress(member_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["members"], request)

@app.put("/members/{member_id}", tags=["Members"])
async def proxy_put_members_members_member_id(member_id: str, request: Request, payload: Dict[str, Any] = Body(None), current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["members"], request)

@app.delete("/members/{member_id}", tags=["Members"])
async def proxy_delete_members_members_member_id(member_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["members"], request)

@app.post("/trainers", tags=["Trainers"])
async def proxy_post_trainers_trainers(request: Request, payload: Dict[str, Any] = Body(None), current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["trainers"], request)

@app.get("/trainers", tags=["Trainers"])
async def proxy_get_trainers_trainers(request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["trainers"], request)

@app.get("/trainers/{trainer_id}", tags=["Trainers"])
async def proxy_get_trainers_trainers_trainer_id(trainer_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["trainers"], request)

@app.get("/trainers/{trainer_id}/schedule", tags=["Trainers"])
async def proxy_get_trainers_trainers_trainer_id_schedule(trainer_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["trainers"], request)

@app.put("/trainers/{trainer_id}", tags=["Trainers"])
async def proxy_put_trainers_trainers_trainer_id(trainer_id: str, request: Request, payload: Dict[str, Any] = Body(None), current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["trainers"], request)

@app.post("/trainers/{trainer_id}/equipment-reservations", tags=["Trainers"])
async def proxy_post_trainers_trainers_trainer_id_equipment_reservations(trainer_id: str, request: Request, payload: Dict[str, Any] = Body(None), current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["trainers"], request)

@app.delete("/trainers/{trainer_id}", tags=["Trainers"])
async def proxy_delete_trainers_trainers_trainer_id(trainer_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["trainers"], request)

@app.post("/classes", tags=["Classes"])
async def proxy_post_classes_classes(request: Request, payload: Dict[str, Any] = Body(None), current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["classes"], request)

@app.get("/classes", tags=["Classes"])
async def proxy_get_classes_classes(request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["classes"], request)

@app.get("/classes/{class_id}", tags=["Classes"])
async def proxy_get_classes_classes_class_id(class_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["classes"], request)

@app.get("/classes/schedule/{trainer_id}", tags=["Classes"])
async def proxy_get_classes_classes_schedule_trainer_id(trainer_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["classes"], request)

@app.get("/classes/equipment/{equipment_id}", tags=["Classes"])
async def proxy_get_classes_classes_equipment_equipment_id(equipment_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["classes"], request)

@app.post("/classes/{class_id}/register", tags=["Classes"])
async def proxy_post_classes_classes_class_id_register(class_id: str, request: Request, payload: Dict[str, Any] = Body(None), current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["classes"], request)

@app.post("/classes/{class_id}/start", tags=["Classes"])
async def proxy_post_classes_classes_class_id_start(class_id: str, request: Request, payload: Dict[str, Any] = Body(None), current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["classes"], request)

@app.put("/classes/{class_id}", tags=["Classes"])
async def proxy_put_classes_classes_class_id(class_id: str, request: Request, payload: Dict[str, Any] = Body(None), current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["classes"], request)

@app.delete("/classes/{class_id}", tags=["Classes"])
async def proxy_delete_classes_classes_class_id(class_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["classes"], request)

@app.post("/equipment", tags=["Equipment"])
async def proxy_post_equipment_equipment(request: Request, payload: Dict[str, Any] = Body(None), current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["equipment"], request)

@app.get("/equipment", tags=["Equipment"])
async def proxy_get_equipment_equipment(request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["equipment"], request)

@app.get("/equipment/{equipment_id}", tags=["Equipment"])
async def proxy_get_equipment_equipment_equipment_id(equipment_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["equipment"], request)

@app.get("/equipment/{equipment_id}/classes", tags=["Equipment"])
async def proxy_get_equipment_equipment_equipment_id_classes(equipment_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["equipment"], request)

@app.put("/equipment/{equipment_id}", tags=["Equipment"])
async def proxy_put_equipment_equipment_equipment_id(equipment_id: str, request: Request, payload: Dict[str, Any] = Body(None), current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["equipment"], request)

@app.delete("/equipment/{equipment_id}", tags=["Equipment"])
async def proxy_delete_equipment_equipment_equipment_id(equipment_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["equipment"], request)

@app.get("/equipment/maintenance/due", tags=["Equipment"])
async def proxy_get_equipment_equipment_maintenance_due(request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["equipment"], request)

@app.post("/equipment/{equipment_id}/report-breakdown", tags=["Equipment"])
async def proxy_post_equipment_equipment_equipment_id_report_breakdown(equipment_id: str, request: Request, payload: Dict[str, Any] = Body(None), current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["equipment"], request)

@app.get("/equipment/{equipment_id}/maintenance-schedule", tags=["Equipment"])
async def proxy_get_equipment_equipment_equipment_id_maintenance_schedule(equipment_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["equipment"], request)

@app.post("/attendance", tags=["Attendance"])
async def proxy_post_attendance_attendance(request: Request, payload: Dict[str, Any] = Body(None), current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["attendance"], request)

@app.get("/attendance", tags=["Attendance"])
async def proxy_get_attendance_attendance(request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["attendance"], request)

@app.get("/attendance/member/{member_id}", tags=["Attendance"])
async def proxy_get_attendance_attendance_member_member_id(member_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["attendance"], request)

@app.get("/attendance/{record_id}", tags=["Attendance"])
async def proxy_get_attendance_attendance_record_id(record_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["attendance"], request)

@app.delete("/attendance/{record_id}", tags=["Attendance"])
async def proxy_delete_attendance_attendance_record_id(record_id: str, request: Request, current_user: str = Depends(verify_token)):
    return await forward_request(SERVICE_MAP["attendance"], request)

# ─── Custom OpenAPI Schema Merging ──────────────────────────────────────────────

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    import httpx
    for service_name, url in SERVICE_MAP.items():
        try:
            # Synchronously fetch the OpenAPI schema from the downstream service
            with httpx.Client(timeout=2.0) as client:
                res = client.get(f"{url}/openapi.json")
            
            if res.status_code == 200:
                downstream = res.json()

                # Merge components/schemas
                if "components" in downstream and "schemas" in downstream["components"]:
                    if "components" not in openapi_schema:
                        openapi_schema["components"] = {"schemas": {}}
                    if "schemas" not in openapi_schema["components"]:
                        openapi_schema["components"]["schemas"] = {}
                    
                    for k, v in downstream["components"]["schemas"].items():
                        openapi_schema["components"]["schemas"][k] = v

                # Merge path specifically for requestBody and responses
                if "paths" in downstream:
                    for path, path_info in downstream["paths"].items():
                        if path in openapi_schema.get("paths", {}):
                            for method, op in path_info.items():
                                if method in openapi_schema["paths"][path]:
                                    target_op = openapi_schema["paths"][path][method]
                                    if "requestBody" in op:
                                        target_op["requestBody"] = op["requestBody"]
                                    if "responses" in op:
                                        target_op["responses"].update(op["responses"])
        except Exception as e:
            print(f"Skipping OpenAPI merge for {service_name}: {e}")

    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

# ─── Shutdown ─────────────────────────────────────────────────────────────────

@app.on_event("shutdown")
async def shutdown_event():
    await http_client.aclose()
