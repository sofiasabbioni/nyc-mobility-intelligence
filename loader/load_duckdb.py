"""Load the CLEAN night-trip files into DuckDB (one database file per test size).

LOAD TIME: same definition as load_postgres.py: from "files checked" to "durable and ready"
(tables with primary keys + zones + trips + CHECKPOINT). The old file is deleted before the
timer starts. DuckDB reads the Parquet files itself (no client-side decoding).
Rerun behaviour: the database file is deleted and rebuilt every time.
Standalone:   MONTHS=1 python load_duckdb.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from common import (ZONES_FILE, append_csv, clean_dir, duck_path, get_config, month_list,
                    months_rows, raw_dir, read_manifest, results_dir, trip_file)

DDL = (Path(__file__).resolve().parent.parent / "sql" / "schema.sql").read_text()
LOAD_HEADER = ["engine", "months", "rows", "seconds", "fingerprint"]


def sql_literal(path: Path) -> str:
    return "'" + path.as_posix().replace("'", "''") + "'"


def load(months: int, db_path: Path | None = None) -> int:
    db_path = db_path or duck_path(months)
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

    db_path.parent.mkdir(parents=True, exist_ok=True)
    for p in (db_path, Path(str(db_path) + ".wal")):
        p.unlink(missing_ok=True)

    t0 = time.perf_counter()
    con = duckdb.connect(str(db_path))
    con.execute(DDL)
    con.execute(f"""INSERT INTO zones SELECT * FROM read_csv({sql_literal(zones_csv)}, header = true,
                    columns = {{'LocationID': 'INTEGER', 'Borough': 'VARCHAR', 'Zone': 'VARCHAR',
                                'service_zone': 'VARCHAR'}})""")
    file_list = "[" + ", ".join(sql_literal(f) for f in files) + "]"
    con.execute(f"INSERT INTO trips BY NAME SELECT * FROM read_parquet({file_list})")
    con.execute("CHECKPOINT")
    seconds = time.perf_counter() - t0

    in_db, idmin, idmax = con.execute("SELECT count(*), min(trip_id), max(trip_id) FROM trips").fetchone()
    meta = {"fingerprint": manifest["fingerprint"], "months": str(months), "rows": str(in_db),
            "first_trip_id": str(idmin), "last_trip_id": str(idmax)}
    for k, v in meta.items():
        con.execute("INSERT INTO load_meta VALUES (?, ?)", [k, v])
    con.execute("CHECKPOINT")
    con.close()

    print(f"[duckdb {months}m] expected {expected:,}, in database {in_db:,}")
    if expected != in_db or idmin != 1 or idmax != in_db:
        raise RuntimeError(f"consistency check failed (ids {idmin}..{idmax})")
    print(f"[duckdb {months}m] load time {seconds:.2f} s, file {db_path.stat().st_size / 1024**2:.0f} MB")
    append_csv(results_dir() / "load_times.csv", LOAD_HEADER,
               ["duckdb", months, in_db, f"{seconds:.3f}", manifest["fingerprint"]])
    return in_db


def main() -> int:
    months = int(os.environ.get("MONTHS", str(get_config().total_months)))
    try:
        load(months)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"ERROR: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
