"""FastAPI application: API + static frontend."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import WEB_DIR
from .database import init_db
from .routers import datasets, models, runs, system

app = FastAPI(title="microCT Segmentation Lab", version="0.1.0")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# Ensure the registry tables exist as soon as the app is imported (works under
# uvicorn, tests, and any WSGI/ASGI host — not only the startup event).
init_db()


app.include_router(system.router)
app.include_router(models.router)
app.include_router(datasets.router)
app.include_router(runs.router)

# Serve the SPA at "/" (registered last so /api/* routes win).
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
