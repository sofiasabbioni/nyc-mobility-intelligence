"""The whole loader, in order. This is what the `loader` container runs.

  1. download_data.py        raw files -> data/raw (existing files are used, not downloaded again)
  2. preprocess.py           cleaned NIGHT-TRIP files -> data/clean (+ exclusion report, validation)
  3. build_full_dataset.py   only if BUILD_FULL_DATASET=1: all trips, all columns, one file
  4. for each test size:     load_postgres (schema m1/m2/m4) and load_duckdb (taxi_Nm.duckdb)
  5. city_results.py         small CSV tables for the dashboard

Rerun behaviour: steps 1-2 reuse what is valid; databases are rebuilt every run; load_times.csv
is recreated.
"""
from __future__ import annotations

import os
import sys

import build_full_dataset
import city_results
import download_data
import load_duckdb
import load_postgres
import preprocess
from common import get_config, results_dir


def main() -> int:
    cfg = get_config()
    (results_dir() / "load_times.csv").unlink(missing_ok=True)

    steps = [download_data.main, preprocess.main]
    if os.environ.get("BUILD_FULL_DATASET") == "1":
        steps.append(build_full_dataset.main)
    for step in steps:
        if step() != 0:
            return 1

    for n in cfg.sizes:
        print(f"\n=== test size: first {n} month(s) ===")
        load_postgres.load(n, f"m{n}")
        load_duckdb.load(n)

    if city_results.main() != 0:
        return 1
    print("\nLoader finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
