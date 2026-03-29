# 🏋️ Gym Management System

A production-grade **Microservices-based Gym Management System** built with **Python FastAPI**.  
Each service is independent, has its own **SQLite database**, and exposes full **Swagger UI** documentation.

---

## 📁 Project Structure

```
gym/
├── api_gateway/
│   ├── main.py            # Proxy gateway routing /api/* to microservices
│   └── requirements.txt
├── member_service/
│   ├── main.py            # CRUD for gym members
│   └── requirements.txt
├── trainer_service/
│   ├── main.py            # CRUD for trainers
│   └── requirements.txt
├── class_service/
│   ├── main.py            # CRUD for classes + Trainer ID verification
│   └── requirements.txt
├── equipment_service/
│   ├── main.py            # Equipment + maintenance management
│   └── requirements.txt
├── attendance_service/
│   ├── main.py            # Attendance logging + Member Active verification
│   └── requirements.txt
├── run_all.bat            # Windows launcher — starts all 6 services
└── README.md
```

---

## 🚀 Quick Start (Windows)

### Option A — One-Click Launch
Double-click **`run_all.bat`** in the `gym/` folder.  
It installs dependencies and opens all 6 services in separate terminal windows.

### Option B — Manual (recommended for development)

```bash
# 1. Install deps for a service (run inside each service folder)
pip install -r requirements.txt

# 2. Start a service
uvicorn main:app --reload --port <PORT>
```

---

## 🌐 Service Ports & Swagger URLs

| Service           | Port | Swagger UI                              |
|-------------------|------|------------------------------------------|
| API Gateway       | 8080 | http://localhost:8080/docs               |
| Member Service    | 8081 | http://localhost:8081/docs               |
| Trainer Service   | 8082 | http://localhost:8082/docs               |
| Class Service     | 8083 | http://localhost:8083/docs               |
| Equipment Service | 8084 | http://localhost:8084/docs               |
| Attendance Service| 8085 | http://localhost:8085/docs               |

---

## 🔗 API Gateway Routes

All requests can go through the gateway at **port 8080**:

| Gateway Route          | Forwards To         | Service       |
|------------------------|---------------------|---------------|
| `GET /api/member`      | `GET /members`      | Member (8081) |
| `POST /api/member`     | `POST /members`     | Member (8081) |
| `GET /api/trainer`     | `GET /trainers`     | Trainer (8082)|
| `POST /api/class`      | `POST /classes`     | Class (8083)  |
| `GET /api/equipment`   | `GET /equipment`    | Equip. (8084) |
| `POST /api/attendance` | `POST /attendance`  | Attend. (8085)|

---

## 🔄 Inter-Service Communication

### Attendance → Member Service
Before logging attendance, the **Attendance Service** calls the **Member Service** via `httpx`:
- ✅ Verifies the member **exists**
- ✅ Verifies the member's status is **`Active`** (rejects `Expired` members with `403`)

### Class → Trainer Service
Before creating/updating a class, the **Class Service** calls the **Trainer Service** via `httpx`:
- ✅ Verifies the trainer **exists** before linking them to a class

---

## 📋 Data Models

### Member
| Field  | Type   | Notes                    |
|--------|--------|--------------------------|
| id     | int    | Auto-increment           |
| name   | string | Required                 |
| email  | string | Unique, validated        |
| status | string | `Active` or `Expired`    |

### Trainer
| Field         | Type   | Notes    |
|---------------|--------|----------|
| id            | int    | Auto-increment |
| name          | string | Required |
| specialization| string | Required |

### Class
| Field      | Type   | Notes                        |
|------------|--------|------------------------------|
| id         | int    | Auto-increment               |
| name       | string | Required                     |
| trainer_id | int    | Verified against Trainer Svc |
| schedule   | string | e.g. `Mon/Wed 07:00`         |
| capacity   | int    | Default: 20                  |

### Equipment
| Field            | Type   | Notes                  |
|------------------|--------|------------------------|
| id               | int    | Auto-increment         |
| name             | string | Required               |
| category         | string | e.g. `Cardio`          |
| quantity         | int    | Default: 1             |
| condition        | string | Default: `Good`        |
| last_maintenance | string | ISO date `YYYY-MM-DD`  |
| next_maintenance | string | ISO date `YYYY-MM-DD`  |

### Attendance
| Field     | Type   | Notes                            |
|-----------|--------|----------------------------------|
| id        | int    | Auto-increment                   |
| member_id | int    | Verified Active via Member Svc   |
| date      | string | Defaults to today                |
| check_in  | string | HH:MM, defaults to current time  |
| notes     | string | Optional                         |

---

## ⚙️ Technical Details

- **Framework**: FastAPI (Python 3.10+)
- **Database**: SQLite (one `.db` file per service, auto-created on startup)
- **Validation**: Pydantic v2 with full type enforcement
- **HTTP Client**: `httpx` for async inter-service calls
- **API Docs**: Auto-generated Swagger UI at `/docs`, ReDoc at `/redoc`
- **Error Handling**: Proper HTTP status codes (404, 409, 422, 503, 504)
