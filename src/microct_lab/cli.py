"""Console entry points (see [project.scripts] in pyproject.toml)."""
from __future__ import annotations

import argparse

from .config import settings


def web() -> None:
    import uvicorn
    from .database import init_db
    init_db()
    print(f"microCT Segmentation Lab  ->  http://{settings.host}:{settings.port}")

    # Say plainly who may reach this server and whether they need a secret. The
    # alternative is the operator hunting through .env while a browser sits on an
    # empty token field, which is exactly the confusion this replaces.
    if settings.remote_origins:
        print()
        print("  A remotely-hosted UI may connect to this server:")
        for o in settings.remote_origins:
            print(f"    {o}")
        if settings.api_token:
            # Same reasoning as Jupyter printing its token: the operator owns this
            # console, and only a token they set is ever shown.
            print()
            print(f"    access token:  {settings.api_token}")
            print("    Paste it into the UI's Connection panel with the base URL above.")
            if not settings.require_token_local:
                print("    (Pages served from this machine are exempt and need no token.)")
        else:
            print()
            print("    No token required — open the page and it connects.")
            print("    Only the origin(s) listed above may reach this server; set")
            print("    MICROCT_API_TOKEN to also require a shared secret.")
        print()
    elif settings.api_token:
        print()
        print(f"  Access token set, but no remote origin is allowed, so only this")
        print(f"  machine can reach the API. Run `microct-token` to see it.")
        print()

    uvicorn.run("microct_lab.main:app", host=settings.host, port=settings.port)


def worker(argv: list[str] | None = None) -> None:
    from .worker import KINDS, run_worker
    ap = argparse.ArgumentParser(prog="microct-worker",
                                 description="Poll the registry and execute queued jobs.")
    ap.add_argument("--kind", default="all", choices=list(KINDS),
                    help="Which job kinds to work: 'segmentation' needs the GPU stack, "
                         "'measurement' is CPU-only, 'all' does both")
    args = ap.parse_args(argv)
    run_worker(args.kind)


def show_token() -> None:
    """Print the access token a remotely-hosted UI needs. Reads .env directly, so
    it works whether or not the server is running."""
    if not settings.api_token:
        print("No access token is set on this server.")
        print()
        print("You only need one when a UI hosted elsewhere (e.g. Vercel) drives")
        print("this server. To enable that, set both of these in .env and restart:")
        print("    MICROCT_ALLOWED_ORIGINS=https://your-app.vercel.app")
        print("    MICROCT_API_TOKEN=" +
              "<generate: python -c \"import secrets; print(secrets.token_urlsafe(32))\">")
        print()
        print("Until then the built-in dashboard at "
              f"http://{settings.host}:{settings.port} needs no token.")
        return

    origins = ", ".join(settings.extra_origins) or "(none — set MICROCT_ALLOWED_ORIGINS)"
    print("Access token for the remote UI's Connection panel:")
    print()
    print(f"    {settings.api_token}")
    print()
    print(f"  base URL         http://{settings.host}:{settings.port}")
    print(f"  allowed origin   {origins}")


def measure_worker() -> None:
    """CPU-only measurement worker — run alongside the GPU segmentation worker."""
    worker(["--kind", "measurement"])


def ingest_cli() -> None:
    ap = argparse.ArgumentParser(prog="microct-ingest",
                                 description="Scan a data root and (re)register datasets.")
    ap.add_argument("root", nargs="?", default=None,
                    help="Data root to scan (default: MICROCT_DATA_ROOT)")
    args = ap.parse_args()
    from .database import SessionLocal, init_db
    from .registry import ingest_root
    init_db()
    db = SessionLocal()
    res = ingest_root(db, args.root)
    db.close()
    print(f"root={res['root']}")
    print(f"created={len(res['created'])} updated={len(res['updated'])} "
          f"skipped(no slices)={res['skipped_no_slices']}")
    for n in res["created"]:
        print("  +", n)


def register_model_cli() -> None:
    ap = argparse.ArgumentParser(prog="microct-register-model",
                                 description="Register a trained nnU-Net model folder.")
    ap.add_argument("path", help="Model folder (contains plans.json, dataset.json, fold_*)")
    ap.add_argument("--name")
    ap.add_argument("--family")
    ap.add_argument("--version")
    args = ap.parse_args()
    from .database import SessionLocal, init_db
    from .registry import register_model
    init_db()
    db = SessionLocal()
    m = register_model(db, args.path, args.name, args.family, args.version)
    print(f"registered model #{m.id}: {m.name}")
    print(f"  family={m.family} version={m.version} dice={m.cross_val_dice} fp={m.fingerprint}")
    db.close()
