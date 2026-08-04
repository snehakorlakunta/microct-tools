"""FastAPI application: API + static frontend."""
from __future__ import annotations

import re
import secrets
import warnings

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import get_route_path

from .config import WEB_DIR, settings
from .database import init_db
from .routers import analyses, datasets, measurements, models, projects, runs, system

app = FastAPI(title="microCT Segmentation Lab", version="0.1.0")

# Endpoints reachable without a token: the health probe a remote frontend uses to
# discover whether this server is up and whether it demands auth, and the token
# hand-off below, which guards itself. Health deliberately leaks nothing but the
# app name and that one boolean.
_PUBLIC_PATHS = {"/api/health", "/api/token"}

# Loopback addresses. Only a caller ON this machine may read the access token.
_LOOPBACK = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}

# Everything the token guards. /api/* is the data; the schema and docs endpoints
# are listed explicitly because they live outside that prefix and would otherwise
# hand an unauthenticated caller the complete API surface.
_PROTECTED_PREFIXES = ("/api/",)
_PROTECTED_EXACT = {"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}


LOCAL_ORIGIN_RE = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")


def _is_local_caller(request: Request) -> bool:
    """True when the request genuinely comes from this machine.

    Two cases, and BOTH must be pinned down:

    * An `Origin` header naming localhost — a page served from this machine. A
      browser always attaches the page's real origin to a cross-origin request,
      so a public website cannot forge this.
    * No `Origin` at all — a same-origin page or a local tool such as curl. This
      one needs the network check too: an absent `Origin` says the caller is not
      a cross-origin browser page, it says nothing about *where* the caller is.
      Without the loopback test, binding to 0.0.0.0 for LAN access would let
      anyone on the network skip the token entirely just by not sending a header
      browsers add on their own. Verified: it returned real dataset JSON.
    """
    origin = request.headers.get("origin")
    if origin:
        return bool(LOCAL_ORIGIN_RE.match(origin))
    client = request.client.host if request.client else None
    return client in _LOOPBACK


def _needs_token(request: Request) -> bool:
    if not settings.api_token:
        return False
    if settings.require_token_local:
        return True
    return not _is_local_caller(request)


@app.get("/api/health", tags=["system"])
def health(request: Request):
    return {
        "ok": True,
        "app": "microct-seg-lab",
        "version": app.version,
        # Whether THIS caller needs a token, not merely whether one is
        # configured. A page served from this machine is exempt, and telling it
        # otherwise would make the UI demand a secret it never has to send.
        "auth_required": _needs_token(request),
        "auth_configured": bool(settings.api_token),
    }


@app.get("/api/token", tags=["system"])
def access_token(request: Request):
    """Show the access token, so you can copy it into a remote UI.

    Guarded two ways, because handing out the credential that protects everything
    else needs to be narrower than "unauthenticated":

    1. **Loopback callers only.** With MICROCT_HOST=0.0.0.0 the server is on the
       LAN, and without this check any colleague could read the token straight
       off it.
    2. **No `Origin` header.** A browser attaches `Origin` to every cross-origin
       request, so this refuses all of them — including from origins CORS would
       otherwise allow. That matters because the localhost origin rule permits
       *any* local port, so any other web app you happen to be running could
       otherwise ask for this token and then drive the whole API with it.

    What is left is what a person actually needs: `curl`, or typing the URL into
    the address bar (a top-level navigation sends no `Origin`).
    """
    if not settings.api_token:
        return {
            "auth_required": False,
            "detail": "This server has no token set, so the UI needs only the base URL.",
        }

    client = request.client.host if request.client else None
    if client not in _LOOPBACK:
        raise HTTPException(
            403, "The access token is readable only from this machine. "
                 "Run `microct-token` in a terminal on the server instead.")

    if request.headers.get("origin"):
        raise HTTPException(
            403, "The access token cannot be read by a web page. Open "
                 "http://127.0.0.1:8000/api/token directly in a tab, or run "
                 "`microct-token` on the server.")

    return {
        "auth_required": True,
        "token": settings.api_token,
        "allowed_origins": settings.extra_origins,
        "hint": "Paste this into the UI's Connection panel along with this "
                "server's base URL.",
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
    if _needs_token(request) and _is_protected(path) and request.method != "OPTIONS":
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

# The security posture is reported by the startup banner in cli.py rather than
# warned about here: allowing a remote origin without a token is a supported
# configuration (it is what lets a colleague open the hosted UI and have it work
# against their own machine), not a mistake to be scolded for.

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

# Serve the frontend at "/" (registered last so /api/* routes win). This is the
# bundled SPA unless MICROCT_WEB_DIR points at another build — e.g. the Next.js
# static export, which then runs same-origin: no CORS, no Private Network Access
# preflight, no dependency on a hosted deployment being reachable.
app.mount("/", StaticFiles(directory=str(settings.frontend_dir), html=True), name="web")
