"""Step 1: get the original NYC TLC files into DATA_DIR/raw, WITHOUT changing them.

  - A file that already exists is used as it is: it is never overwritten, never downloaded
    again, never modified. So files you downloaded earlier on Windows can simply be copied
    into the folder data\\raw of this project (same names: yellow_tripdata_YYYY-MM.parquet and
    taxi_zone_lookup.csv). If all files are there, the network is not used at all.
  - A missing file is downloaded to a .part file and renamed only when complete.
  - Every existing file is checked (readable Parquet, needed columns). A damaged file stops
    the run with a message: we never delete or replace your files automatically.
Settings: START_MONTH, TOTAL_MONTHS, DATA_DIR (see .env.example).
"""
from __future__ import annotations

import os
import sys
import time
import urllib.request

import pyarrow.parquet as pq

from common import ZONES_FILE, get_config, month_list, raw_dir, trip_file

BASE_URL = "https://d37ci6vzurychx.cloudfront.net"
NEEDED = ["tpep_pickup_datetime", "tpep_dropoff_datetime", "PULocationID", "DOLocationID",
          "passenger_count", "trip_distance", "fare_amount", "tip_amount", "total_amount"]
ZONE_HEADER = ["LocationID", "Borough", "Zone", "service_zone"]


def download(url: str, path) -> None:
    print(f"{path.name}: downloading from {url}")
    start = time.perf_counter()
    temp = path.with_suffix(path.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "taxi-project-loader"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(temp, "wb") as out:
            while chunk := resp.read(1024 * 1024):
                out.write(chunk)
        os.replace(temp, path)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    print(f"{path.name}: downloaded ({path.stat().st_size / 1024**2:.1f} MB) "
          f"in {time.perf_counter() - start:.1f} s")


def ensure_file(name: str, url: str, raw) -> bool:
    """True if the file is available (it existed or was downloaded)."""
    path = raw / name
    if path.exists():
        print(f"{name}: found ({path.stat().st_size / 1024**2:.1f} MB), using it, no download")
        return True
    try:
        download(url, path)
        return True
    except Exception as exc:  # noqa: BLE001 - we want to report any network problem plainly
        print(f"ERROR: could not download {name}: {exc}\n"
              f"       Download it yourself from {url} and put it in {raw}")
        return False


def check_trip_file(path) -> str | None:
    """Return a problem description, or None if the file is a usable Parquet file."""
    try:
        meta = pq.read_metadata(path)
        names = pq.read_schema(path).names
    except Exception as exc:  # noqa: BLE001
        return f"cannot be read as Parquet ({exc}). Remove or replace it and run again."
    missing = [c for c in NEEDED if c not in names]
    if missing:
        return f"is missing columns {missing}"
    if meta.num_rows == 0:
        return "has 0 rows"
    return None


def main() -> int:
    cfg = get_config()
    months = month_list(cfg.total_months, cfg.start_month)
    raw = raw_dir()
    raw.mkdir(parents=True, exist_ok=True)
    print(f"Raw data for {months} in {raw} (originals are never modified)")

    ok = ensure_file(ZONES_FILE, f"{BASE_URL}/misc/{ZONES_FILE}", raw)
    if os.environ.get("WITH_ZONE_SHAPES") == "1":
        ok = ensure_file("taxi_zones.zip", f"{BASE_URL}/misc/taxi_zones.zip", raw) and ok
    for m in months:
        ok = ensure_file(trip_file(m), f"{BASE_URL}/trip-data/{trip_file(m)}", raw) and ok
    if not ok:
        return 1

    print("\nChecking files...")
    header = (raw / ZONES_FILE).read_text(encoding="utf-8-sig").splitlines()[0].split(",")
    if [h.strip().strip('"') for h in header] != ZONE_HEADER:
        print(f"ERROR: {ZONES_FILE} header is {header}, expected {ZONE_HEADER}")
        return 1
    total = 0
    for m in months:
        path = raw / trip_file(m)
        problem = check_trip_file(path)
        if problem:
            print(f"ERROR: {path.name} {problem}")
            return 1
        meta = pq.read_metadata(path)
        total += meta.num_rows
        print(f"{path.name}: {meta.num_rows:,} rows, {meta.num_columns} columns")
    print(f"\nTotal source rows: {total:,} ({months[0]} to {months[-1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
