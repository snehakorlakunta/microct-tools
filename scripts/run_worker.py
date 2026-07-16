#!/usr/bin/env python3
"""Launch the job worker (equivalent to the `microct-worker` console command)."""
from microct_lab.worker import run_worker

if __name__ == "__main__":
    run_worker()
