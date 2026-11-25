"""Load the CLEAN night-trip files into PostgreSQL (one schema per test size: m1, m2, m4).

LOAD TIME (defined identically for DuckDB): wall-clock seconds from "files checked" to "data
durable and statistics ready", i.e. CREATE SCHEMA + tables (with primary keys) + zones + all
trips + ANALYZE + COMMIT. It does NOT include dropping the previous copy or the row-count
verification afterwards. Note for PostgreSQL: Parquet is decoded in Python (pyarrow), serialised
to CSV and sent with COPY, so the number includes that client-side work.

Rerun behaviour: the schema is dropped and rebuilt every time (no stale data possible).
Standalone:   MONTHS=1 POSTGRES_SCHEMA=public python load_postgres.py
"""
from __future__ import annotations

import csv
import io
import os
import sys
import time
from pathlib import Path

import psycopg
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from common import (ZONES_FILE, append_csv, clean_dir, get_config, month_list, months_rows,
                    raw_dir, read_manifest, results_dir, trip_file)

COLUMN_NAMES = ("trip_id, pickup_ts, dropoff_ts, pu_location_id, do_location_id, "
                "passenger_count, trip_distance, fare_amount, tip_amount, total_amount")
BATCH_ROWS = 200_000
SCHEMA_SQL = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"
LOAD_HEADER = ["engine", "months", "rows", "seconds", "fingerprint"]


def connect() -> psycopg.Connection:
    e = os.environ.get
    return psycopg.connect(host=e("POSTGRES_HOST", "localhost"), port=e("POSTGRES_PORT", "5432"),
                           user=e("POSTGRES_USER", "postgres"), password=e("POSTGRES_PASSWORD", "postgres"),
                           dbname=e("POSTGRES_DB", "taxi"))


def load_zones(cur: psycopg.Cursor, path: Path) -> int:
    with open(path, newline="", encoding="utf-8-sig") as f:
        n = sum(1 for _ in csv.reader(f)) - 1
    with open(path, "rb") as f, cur.copy(
        "COPY zones (location_id, borough, zone, service_zone) FROM STDIN WITH (FORMAT csv, HEADER true)"
    ) as copy:
        while chunk := f.read(1 << 20):
            copy.write(chunk)
    return n


def load_trips_file(cur: psycopg.Cursor, path: Path) -> int:
    rows = 0
    opts = pacsv.WriteOptions(include_header=False)
    with cur.copy(f"COPY trips ({COLUMN_NAMES}) FROM STDIN WITH (FORMAT csv, HEADER false)") as copy:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=BATCH_ROWS):
            buf = io.BytesIO()
            pacsv.write_csv(batch, buf, write_options=opts)
            copy.write(buf.getvalue())
            rows += batch.num_rows
    return rows


def load(months: int, schema: str) -> int:
    """Load the first `months` months into `schema`. Returns the number of trips loaded."""
    if not schema.replace("_", "").isalnum():
        raise ValueError(f"bad schema name: {schema}")
    cfg = get_config()
    manifest = read_manifest()
    if manifest is None:
        raise FileNotFoundError("clean/manifest.json not found: run preprocess.py first")
    zones_csv = raw_dir() / ZONES_FILE
    files = [clean_dir() / trip_file(m) for m in month_list(months, cfg.start_month)]
    missing = [str(f) for f in files + [zones_csv] if not f.exists()]
    if missing:
        raise FileNotFoundError("Missing files, run download_data.py and preprocess.py first:\n  "
                                + "\n  ".join(missing))
    expected = sum(pq.read_metadata(f).num_rows for f in files)
    if expected != months_rows(manifest, months):
        raise RuntimeError("clean files do not match manifest.json: run preprocess.py again")

    with connect() as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")  # not part of the load time
        conn.commit()
        t0 = time.perf_counter()
        total = 0
        with conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA {schema}")
            cur.execute(f"SET search_path TO {schema}")
            cur.execute(SCHEMA_SQL.read_text())
            z = load_zones(cur, zones_csv)
            print(f"[postgres {schema}] zones {z} rows")
            for f in files:
                n = load_trips_file(cur, f)
                total += n
                print(f"[postgres {schema}] trips {f.name}: {n:,} rows")
            cur.execute("ANALYZE trips")
            cur.execute("ANALYZE zones")
        conn.commit()
        seconds = time.perf_counter() - t0

        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {schema}")
            cur.execute("SELECT count(*), min(trip_id), max(trip_id) FROM trips")
            in_db, idmin, idmax = cur.fetchone()
            meta = {"fingerprint": manifest["fingerprint"], "months": str(months), "rows": str(in_db),
                    "first_trip_id": str(idmin), "last_trip_id": str(idmax)}
            cur.executemany("INSERT INTO load_meta VALUES (%s, %s)", list(meta.items()))
        conn.commit()

    print(f"[postgres {schema}] expected {expected:,}, loaded {total:,}, in database {in_db:,}")
    if not (expected == total == in_db) or idmin != 1 or idmax != in_db:
        raise RuntimeError(f"consistency check failed (ids {idmin}..{idmax})")
    print(f"[postgres {schema}] load time {seconds:.2f} s")
    append_csv(results_dir() / "load_times.csv", LOAD_HEADER,
               ["postgres", months, in_db, f"{seconds:.3f}", manifest["fingerprint"]])
    return in_db


def main() -> int:
    months = int(os.environ.get("MONTHS", str(get_config().total_months)))
    schema = os.environ.get("POSTGRES_SCHEMA", "public")
    try:
        load(months, schema)
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f"ERROR: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
