"""The whole loader, in order. This is what the `loader` container runs.

  1. download_data.py        raw files -> data/raw (existing files are used, not downloaded again)
  2. preprocess.py           cleaned NIGHT-TRIP files -> data/clean (+ exclusion report, validation)
  3. build_full_dataset.py   only if BUILD_FULL_DATASET=1: all trips, all columns, one file
  4. nightlife_metrics.py    nightlife geography for the map -> results/nightlife. Reads the ORIGINAL
                             files in data/raw, NOT data/clean (the clean subset loses arrivals of
                             trips picked up before 18:00). Its night window is fixed at 18:00-05:59.
  5. for each test size:     load_postgres (schema m1/m2/m4) and load_duckdb (taxi_Nm.duckdb)
  6. city_results.py         small CSV tables for the dashboard

A step that fails stops the loader: nothing after it runs and "Loader finished." is not printed.
Rerun behaviour: steps 1-2 reuse what is valid; the nightlife files are replaced; databases are
rebuilt every run; load_times.csv is recreated.
"""
from __future__ import annotations

import os
import sys

import build_full_dataset
import city_results
import download_data
import load_duckdb
import load_postgres
import nightlife_metrics
import preprocess
from common import get_config, results_dir


def nightlife_step() -> int:
    """Step 4. The empty argument list matters: nightlife_metrics.main() uses argparse, and here it
    must read the settings (START_MONTH, TOTAL_MONTHS, DATA_DIR, RESULTS_DIR, MIN_TRIPS) from the
    environment, not the command line of whoever called the loader."""
    return nightlife_metrics.main([])


def main() -> int:
    cfg = get_config()
    (results_dir() / "load_times.csv").unlink(missing_ok=True)

    steps = [download_data.main, preprocess.main]
    if os.environ.get("BUILD_FULL_DATASET") == "1":
        steps.append(build_full_dataset.main)
    steps.append(nightlife_step)
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
