#!/usr/bin/env python3
"""Scan a data root and (re)register datasets (equivalent to `microct-ingest`)."""
from microct_lab.cli import ingest_cli

if __name__ == "__main__":
    ingest_cli()
