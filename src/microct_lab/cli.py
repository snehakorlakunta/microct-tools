"""Console entry points (see [project.scripts] in pyproject.toml)."""
from __future__ import annotations

import argparse

from .config import settings


def web() -> None:
    import uvicorn
    from .database import init_db
    init_db()
    print(f"microCT Segmentation Lab  ->  http://{settings.host}:{settings.port}")

    # When a remotely-hosted UI is allowed, print what it needs to connect.
    # Same reasoning as Jupyter printing its token: the operator owns this
    # console, and the alternative is them hunting through .env while a browser
    # sits on an empty field. Only ever printed for a token the operator set.
    if settings.api_token:
        origins = ", ".join(settings.extra_origins) or "(none configured)"
        print()
        print("  Remote UI access is ENABLED.")
        print(f"    allowed origin(s):  {origins}")
        print(f"    access token:       {settings.api_token}")
        print("    Paste both into the UI's Connection panel.")
        if settings.remote_origins:
            print("    NOTE: /api/* now requires this token, so the dashboard bundled")
            print("          at the URL above will return 401 until you unset it.")
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
