"""TerraSight API — serves the frozen contract the frontend builds against.

Run: uvicorn app.main:app --reload  (from backend/)
Data comes from Supabase when configured, else mock JSON (see app/db.py).
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db

API_PREFIX = "/api/backend"


class StripPrefix:
    """Make the backend proxy-agnostic. Some deployments (the Vercel `services`
    rewrite) forward the full `/api/backend/*` path to this app instead of
    stripping it, while local `next dev` strips it. Strip the prefix here if
    present so the routes below (/health, /map/tiles, ...) match either way.
    No change to the frozen contract — the routes are still unprefixed."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path == API_PREFIX or path.startswith(API_PREFIX + "/"):
                stripped = path[len(API_PREFIX):] or "/"
                scope = {**scope, "path": stripped, "raw_path": stripped.encode()}
        await self.app(scope, receive, send)


app = FastAPI(title="TerraSight API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])
app.add_middleware(StripPrefix)  # outermost: strip /api/backend before routing


@app.get("/health")
def health():
    return {"status": "ok", "source": "supabase" if db._client() else "mock"}


@app.get("/map/tiles")
def map_tiles():
    return db.fetch("tiles")


@app.get("/rover/path")
def rover_path():
    return db.fetch("rover_path")


@app.get("/sites")
def sites():
    return db.fetch("sites")


@app.get("/boundaries")
def boundaries():
    return db.fetch("boundaries")
