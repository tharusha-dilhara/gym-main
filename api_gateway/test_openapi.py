import asyncio
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
import httpx

app = FastAPI(title="Gateway Test")

@app.api_route("/{path:path}", methods=["GET", "POST"])
def catch_all(path: str):
    return {"path": path}

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title="Gym Management API Gateway",
        version="1.0.0",
        description="Unified API Gateway without path prefixes.",
        routes=app.routes,
    )
    # Remove the generic path
    if "/{path}" in openapi_schema["paths"]:
        del openapi_schema["paths"]["/{path}"]
    
    # Just mock openapi fetching
    openapi_schema["paths"]["/members"] = {
        "get": {"summary": "List Members", "responses": {"200": {"description": "OK"}}}
    }
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

if __name__ == "__main__":
    import uvicorn
    # uvicorn.run(app, port=8000)
    print("Schema generated successfully:", app.openapi())

