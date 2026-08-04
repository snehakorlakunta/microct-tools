#!/usr/bin/env python
"""Back up the registry database — and optionally retarget it for another machine.

WHY NOT JUST COPY registry.db
-----------------------------
The registry runs in SQLite **WAL mode**, so a committed write does not
necessarily live in `registry.db` yet — it can sit in `registry.db-wal` until a
checkpoint. Copying the one file while the server is running silently produces a
database that is *valid* but *stale*, which is the worst kind of bad backup:
nothing errors, you just quietly lose recent work. On this install that gap was
large enough that a naive copy was missing the entire `measurements` table.

This script uses SQLite's online backup API, which is safe against a running
server and always yields one consistent file with the WAL folded in.

WHAT ELSE YOU NEED
------------------
The database stores **absolute paths** to data on this machine — dataset slice
folders, model folders, per-run output dirs, mask/preview/log files, thumbnails.
Moving the .db to another computer without those paths existing there gives you a
catalog whose every record points at nothing. Two ways to deal with that:

  * Recreate the same paths on the target, or
  * `--rewrite <OLD>=<NEW>` to retarget every stored path prefix as it is copied.

Thumbnails live outside the DB in <STATE_DIR>/thumbnails; `--with-thumbnails`
bundles them alongside.

USAGE
-----
    python scripts/backup_registry.py --out backup.db
    python scripts/backup_registry.py --out bundle.zip --with-thumbnails
    python scripts/backup_registry.py --report-paths
    python scripts/backup_registry.py --out moved.db \
        --rewrite "C:\\skscan\\snehawa\\microct\\USBFiles=D:\\microct"
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from microct_lab.config import settings  # noqa: E402

# Every column that holds a filesystem path, plus the JSON columns that embed one.
PATH_COLUMNS = {
    "datasets": ["slices_path", "thumbnail"],
    "models": ["path"],
    "runs": ["output_dir", "input_nii", "mask_nii", "preview_png", "log_path"],
    "measurements": ["output_dir", "log_path", "annotated_nii", "xlsx_path"],
}
# Path strings also hide inside these JSON blobs (e.g. measurement params carry
# mask_nii / input_nii), so they get a textual substitution rather than a
# column assignment.
JSON_COLUMNS = {
    "runs": ["params", "env", "model_snapshot"],
    "measurements": ["params", "metrics", "env"],
}


def db_path() -> str:
    return os.path.join(str(settings.state_dir), "registry.db")


def online_backup(src: str, dest: str) -> None:
    """Consistent copy of a possibly-live database, WAL included."""
    if not os.path.isfile(src):
        sys.exit(f"registry not found: {src}")
    if os.path.exists(dest):
        os.remove(dest)
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(dest)
    try:
        with target:
            source.backup(target)
    finally:
        source.close()
        target.close()


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "select 1 from sqlite_master where type='table' and name=?", (table,)
    ).fetchone() is not None


def report_paths(path: str) -> None:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    print(f"Absolute paths recorded in {path}:\n")
    prefixes: dict[str, int] = {}
    for table, cols in PATH_COLUMNS.items():
        if not table_exists(con, table):
            continue
        for col in cols:
            try:
                rows = con.execute(
                    f"select {col} from {table} where {col} is not null and {col} != ''"
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            for (v,) in rows:
                # Group by the first three segments — enough to see the root that
                # would have to exist on the target machine.
                parts = str(v).replace("/", "\\").split("\\")
                prefixes.setdefault("\\".join(parts[:4]), 0)
                prefixes["\\".join(parts[:4])] += 1
    if not prefixes:
        print("  (none)")
    for pfx, n in sorted(prefixes.items(), key=lambda kv: -kv[1]):
        exists = "exists here" if os.path.exists(pfx) else "MISSING here"
        print(f"  {n:4d} ref(s)  {pfx}   [{exists}]")
    print("\nEach of these must exist on the target machine, or be retargeted with")
    print("  --rewrite \"<OLD>=<NEW>\"")
    con.close()


def rewrite_paths(path: str, old: str, new: str) -> int:
    """Substitute a path prefix everywhere it is stored. Returns rows changed."""
    con = sqlite3.connect(path)
    changed = 0
    try:
        with con:
            for table, cols in PATH_COLUMNS.items():
                if not table_exists(con, table):
                    continue
                for col in cols:
                    try:
                        cur = con.execute(
                            f"update {table} set {col} = replace({col}, ?, ?) "
                            f"where {col} like ?", (old, new, f"%{old}%"))
                        changed += cur.rowcount
                    except sqlite3.OperationalError:
                        continue
            # JSON blobs: substitute inside the serialized text. Both the raw and
            # the JSON-escaped form of a Windows path appear (\ vs \\), so try both.
            for table, cols in JSON_COLUMNS.items():
                if not table_exists(con, table):
                    continue
                for col in cols:
                    for a, b in ((old, new), (old.replace("\\", "\\\\"), new.replace("\\", "\\\\"))):
                        try:
                            cur = con.execute(
                                f"update {table} set {col} = replace({col}, ?, ?) "
                                f"where {col} like ?", (a, b, f"%{a}%"))
                            changed += cur.rowcount
                        except sqlite3.OperationalError:
                            continue
            # Validate the JSON survived the substitution — a botched replace that
            # produced unparseable JSON would otherwise only surface at runtime.
            for table, cols in JSON_COLUMNS.items():
                if not table_exists(con, table):
                    continue
                for col in cols:
                    try:
                        rows = con.execute(
                            f"select id, {col} from {table} where {col} is not null").fetchall()
                    except sqlite3.OperationalError:
                        continue
                    for rid, blob in rows:
                        if not blob:
                            continue
                        try:
                            json.loads(blob)
                        except Exception:
                            sys.exit(f"path rewrite corrupted JSON in {table}.{col} id={rid} "
                                     f"— backup NOT written")
    finally:
        con.close()
    return changed


def main() -> None:
    ap = argparse.ArgumentParser(description="Back up / retarget the microCT registry.")
    ap.add_argument("--out", help="Destination .db (or .zip with --with-thumbnails)")
    ap.add_argument("--with-thumbnails", action="store_true",
                    help="Bundle <STATE_DIR>/thumbnails into a zip alongside the DB")
    ap.add_argument("--rewrite", action="append", default=[], metavar="OLD=NEW",
                    help="Retarget a stored path prefix; repeatable")
    ap.add_argument("--report-paths", action="store_true",
                    help="List the absolute paths the registry depends on, then exit")
    args = ap.parse_args()

    src = db_path()

    if args.report_paths:
        report_paths(src)
        return

    if not args.out:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.out = f"registry_backup_{stamp}." + ("zip" if args.with_thumbnails else "db")

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    tmpdir = tempfile.mkdtemp(prefix="microct_backup_")
    try:
        staged = os.path.join(tmpdir, "registry.db")
        online_backup(src, staged)
        print(f"[backup] {src}  ->  consistent snapshot ({os.path.getsize(staged):,} bytes)")

        for spec in args.rewrite:
            if "=" not in spec:
                sys.exit(f"--rewrite expects OLD=NEW, got {spec!r}")
            old, new = spec.split("=", 1)
            n = rewrite_paths(staged, old, new)
            print(f"[rewrite] {old}  ->  {new}   ({n} value(s) updated)")

        if args.with_thumbnails:
            thumbs = str(settings.thumbs_dir)
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(staged, "state/registry.db")
                count = 0
                if os.path.isdir(thumbs):
                    for name in sorted(os.listdir(thumbs)):
                        p = os.path.join(thumbs, name)
                        if os.path.isfile(p):
                            z.write(p, f"state/thumbnails/{name}")
                            count += 1
                print(f"[bundle] + {count} thumbnail(s)")
            print(f"[done] {out}  ({os.path.getsize(out):,} bytes)")
            print("\nOn the target machine: unzip over MICROCT_STATE_DIR, then confirm the")
            print("data/model/results roots in its .env point at real folders.")
        else:
            shutil.move(staged, out)
            print(f"[done] {out}")
            print("\nRestore: stop the server, replace <STATE_DIR>/registry.db with this file,")
            print("and DELETE any registry.db-wal / registry.db-shm sitting next to it —")
            print("a stale WAL from the old database would be replayed over your restore.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
