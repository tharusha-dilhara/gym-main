import json

base_url = "{{base_url}}"

def create_request(name, method, path, requires_auth=True):
    # path starts with /
    req = {
        "name": name,
        "request": {
            "method": method,
            "header": [],
            "url": {
                "raw": f"{base_url}{path}",
                "host": [
                    "{{base_url}}"
                ],
                "path": [p for p in path.split("/") if p]
            }
        },
        "response": []
    }
    
    if requires_auth:
        req["request"]["auth"] = {
            "type": "bearer",
            "bearer": [
                {
                    "key": "token",
                    "value": "{{access_token}}",
                    "type": "string"
                }
            ]
        }
        
    return req

collection = {
    "info": {
        "name": "Gym Management System API",
        "description": "API collection for Gym Management Platform Gateway (port 8080)",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
    },
    "variable": [
        {
            "key": "base_url",
            "value": "http://localhost:8080",
            "type": "string"
        },
        {
            "key": "access_token",
            "value": "",
            "type": "string"
        }
    ],
    "item": []
}

# Gateway & Auth
gateway_req = create_request("Health Check", "GET", "/", requires_auth=False)

register_req = create_request("Register User", "POST", "/register", requires_auth=False)
register_req["request"]["header"].append({"key": "Content-Type", "value": "application/json"})
register_req["request"]["body"] = {
    "mode": "raw",
    "raw": "{\n    \"username\": \"admin\",\n    \"password\": \"password123\"\n}",
    "options": {"raw": {"language": "json"}}
}

token_req = create_request("Login (Get Token)", "POST", "/token", requires_auth=False)
token_req["request"]["header"].append({"key": "Content-Type", "value": "application/x-www-form-urlencoded"})
token_req["request"]["body"] = {
    "mode": "urlencoded",
    "urlencoded": [
        {"key": "username", "value": "admin", "type": "text"},
        {"key": "password", "value": "password123", "type": "text"}
    ]
}
token_req["event"] = [
    {
        "listen": "test",
        "script": {
            "exec": [
                "var jsonData = pm.response.json();",
                "if (jsonData.access_token) {",
                "    pm.collectionVariables.set(\"access_token\", jsonData.access_token);",
                "}"
            ],
            "type": "text/javascript"
        }
    }
]

# Adding Gateway and Auth folder
collection["item"].append({
    "name": "Authentication & Gateway",
    "item": [gateway_req, register_req, token_req]
})

# Helper definition
folders = {
    "Members": [],
    "Trainers": [],
    "Classes": [],
    "Equipment": [],
    "Attendance": []
}

def add_req(folder, name, method, path, has_body=False):
    req = create_request(name, method, path, requires_auth=True)
    if has_body:
        req["request"]["header"].append({"key": "Content-Type", "value": "application/json"})
        req["request"]["body"] = {
            "mode": "raw",
            "raw": "{\n    \n}",
            "options": {"raw": {"language": "json"}}
        }
    folders[folder].append(req)

# Members
add_req("Members", "Create Member", "POST", "/members", True)
add_req("Members", "Get All Members", "GET", "/members")
add_req("Members", "Get Member by ID", "GET", "/members/:member_id")
add_req("Members", "Get Member Attendance", "GET", "/members/:member_id/attendance")
add_req("Members", "Get Member Progress", "GET", "/members/:member_id/progress")
add_req("Members", "Update Member", "PUT", "/members/:member_id", True)
add_req("Members", "Delete Member", "DELETE", "/members/:member_id")

# Trainers
add_req("Trainers", "Create Trainer", "POST", "/trainers", True)
add_req("Trainers", "Get All Trainers", "GET", "/trainers")
add_req("Trainers", "Get Trainer by ID", "GET", "/trainers/:trainer_id")
add_req("Trainers", "Get Trainer Schedule", "GET", "/trainers/:trainer_id/schedule")
add_req("Trainers", "Update Trainer", "PUT", "/trainers/:trainer_id", True)
add_req("Trainers", "Create Equipment Reservation", "POST", "/trainers/:trainer_id/equipment-reservations", True)
add_req("Trainers", "Delete Trainer", "DELETE", "/trainers/:trainer_id")

# Classes
add_req("Classes", "Create Class", "POST", "/classes", True)
add_req("Classes", "Get All Classes", "GET", "/classes")
add_req("Classes", "Get Class by ID", "GET", "/classes/:class_id")
add_req("Classes", "Get Class Schedule for Trainer", "GET", "/classes/schedule/:trainer_id")
add_req("Classes", "Get Classes for Equipment", "GET", "/classes/equipment/:equipment_id")
add_req("Classes", "Register Member to Class", "POST", "/classes/:class_id/register", True)
add_req("Classes", "Start Class", "POST", "/classes/:class_id/start", True)
add_req("Classes", "Update Class", "PUT", "/classes/:class_id", True)
add_req("Classes", "Delete Class", "DELETE", "/classes/:class_id")

# Equipment
add_req("Equipment", "Create Equipment", "POST", "/equipment", True)
add_req("Equipment", "Get All Equipment", "GET", "/equipment")
add_req("Equipment", "Get Equipment by ID", "GET", "/equipment/:equipment_id")
add_req("Equipment", "Get Classes using Equipment", "GET", "/equipment/:equipment_id/classes")
add_req("Equipment", "Update Equipment", "PUT", "/equipment/:equipment_id", True)
add_req("Equipment", "Delete Equipment", "DELETE", "/equipment/:equipment_id")
add_req("Equipment", "Get Equipment Maintenance Due", "GET", "/equipment/maintenance/due")
add_req("Equipment", "Report Equipment Breakdown", "POST", "/equipment/:equipment_id/report-breakdown", True)
add_req("Equipment", "Get Equipment Maintenance Schedule", "GET", "/equipment/:equipment_id/maintenance-schedule")

# Attendance
add_req("Attendance", "Create Attendance Record", "POST", "/attendance", True)
add_req("Attendance", "Get All Attendance", "GET", "/attendance")
add_req("Attendance", "Get Member Attendance", "GET", "/attendance/member/:member_id")
add_req("Attendance", "Get Attendance Record", "GET", "/attendance/:record_id")
add_req("Attendance", "Delete Attendance Record", "DELETE", "/attendance/:record_id")

# Add folders to collection
for folder_name, folder_items in folders.items():
    for item in folder_items:
        variables = []
        for part in item["request"]["url"]["path"]:
            if part.startswith(":"):
                variables.append({"key": part[1:], "value": "1"})
        if variables:
            item["request"]["url"]["variable"] = variables
            
    collection["item"].append({
        "name": folder_name,
        "item": folder_items
    })

with open('Gym_Management_System_Postman_Collection.json', 'w') as f:
    json.dump(collection, f, indent=2)

print("Collection generated successfully!")
