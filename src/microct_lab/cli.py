"""Console entry points (see [project.scripts] in pyproject.toml)."""
from __future__ import annotations

import argparse

from .config import settings


def web() -> None:
    import uvicorn
    from .database import init_db
    init_db()
    print(f"microCT Segmentation Lab  ->  http://{settings.host}:{settings.port}")
    uvicorn.run("microct_lab.main:app", host=settings.host, port=settings.port)


def worker() -> None:
    from .worker import run_worker
    run_worker()


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
