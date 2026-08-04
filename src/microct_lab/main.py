"""FastAPI application: API + static frontend."""
from __future__ import annotations

import secrets
import warnings

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import get_route_path

from .config import WEB_DIR, settings
from .database import init_db
from .routers import analyses, datasets, measurements, models, projects, runs, system

app = FastAPI(title="microCT Segmentation Lab", version="0.1.0")

# Endpoints reachable without a token: the health probe a remote frontend uses to
# discover whether this server is up and whether it demands auth. Deliberately
# leaks nothing but the app name and that one boolean.
_PUBLIC_PATHS = {"/api/health"}

# Everything the token guards. /api/* is the data; the schema and docs endpoints
# are listed explicitly because they live outside that prefix and would otherwise
# hand an unauthenticated caller the complete API surface.
_PROTECTED_PREFIXES = ("/api/",)
_PROTECTED_EXACT = {"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}


@app.get("/api/health", tags=["system"])
def health():
    return {
        "ok": True,
        "app": "microct-seg-lab",
        "version": app.version,
        "auth_required": bool(settings.api_token),
    }


def _is_protected(path: str) -> bool:
    return path not in _PUBLIC_PATHS and (
        path.startswith(_PROTECTED_PREFIXES) or path in _PROTECTED_EXACT)


# --- Auth (innermost: runs after CORS has already vetted the origin) -----------
# Only active when MICROCT_API_TOKEN is set. Local same-origin use leaves it
# unset and nothing changes; a remote frontend must set it.
async def _require_token(request: Request, call_next):
    # Use the ASGI scope path — the SAME value the router dispatches on. NOT
    # request.url.path, which Starlette rebuilds by parsing
    # f"{scheme}://{host_header}{path}": a Host header containing a slash shifts
    # the parsed path (Host "evil/" turns "/api/runs" into "//api/runs"), so a
    # prefix test on it silently fails open while routing still reaches the
    # endpoint. That is an unauthenticated read of every dataset, from one
    # attacker-controlled header.
    path = get_route_path(request.scope)
    if settings.api_token and _is_protected(path) and request.method != "OPTIONS":
        # ^ preflight carries no Authorization header, so it must stay exempt.
        sent = request.headers.get("authorization", "")
        expected = f"Bearer {settings.api_token}"
        # Compare as bytes: compare_digest raises TypeError on a str holding any
        # non-ASCII character, and an attacker controls this header — as str
        # that turns a bad token into a 500 with a traceback instead of a 401.
        if not secrets.compare_digest(sent.encode("utf-8"), expected.encode("utf-8")):
            return JSONResponse({"detail": "missing or invalid API token"}, status_code=401)
    return await call_next(request)


app.middleware("http")(_require_token)

# The bundled SPA is same-origin, so CORS exists for local tooling and for a
# remote-hosted frontend the user opted into via MICROCT_ALLOWED_ORIGINS.
# Everything else is refused, so a random website the user visits can't drive
# this API cross-origin and read its responses.
# allow_credentials stays OFF on purpose: auth is a bearer token, not a cookie,
# so the browser never attaches ambient credentials to a cross-origin request.
# allow_private_network answers Chrome's Private Network Access preflight, which
# a public HTTPS page must pass before it may reach a loopback address. Without
# it Starlette rejects that preflight outright and a Vercel-hosted frontend can
# never reach localhost. It only ever applies to an origin that already passed
# the origin check above.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.extra_origins,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"], allow_headers=["*"],
    allow_private_network=True,
    max_age=600,
)

if settings.remote_origins and not settings.api_token:
    warnings.warn(
        "MICROCT_ALLOWED_ORIGINS names a non-local origin "
        f"({', '.join(settings.remote_origins)}) but MICROCT_API_TOKEN is unset. "
        "The API is reachable from that origin with no authentication. Set a token.",
        stacklevel=2,
    )

# Ensure the registry tables exist as soon as the app is imported (works under
# uvicorn, tests, and any WSGI/ASGI host — not only the startup event).
init_db()


app.include_router(system.router)
app.include_router(models.router)
app.include_router(datasets.router)
app.include_router(runs.router)
app.include_router(projects.router)
app.include_router(analyses.router)
app.include_router(measurements.router)

# Serve the SPA at "/" (registered last so /api/* routes win).
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
