"""Small result tables of the "night in the city" question, for the dashboard.

Runs Q1, Q2, Q3 on the largest DuckDB file and saves them in results/city/. They describe the
cleaned NIGHT-TRIP subset only. The dashboard reads these CSV files, never a database.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import duckdb

from common import duck_path, get_config, results_dir, write_json

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sql"))
import queries  # noqa: E402


def main() -> int:
    cfg = get_config()
    biggest = max(cfg.sizes)
    db = duck_path(biggest)
    if not db.exists():
        print(f"ERROR: {db} not found")
        return 1
    out = results_dir() / "city"
    out.mkdir(parents=True, exist_ok=True)
    q = queries.queries()
    con = duckdb.connect(str(db), read_only=True)
    for name, key in (("hourly", "Q1"), ("top_zones", "Q2"), ("boroughs", "Q3")):
        cur = con.execute(q[key])
        columns = [d[0] for d in cur.description]
        rows = cur.fetchall()
        with open(out / f"{name}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(columns)
            w.writerows(rows)
        print(f"city result {name}.csv: {len(rows)} rows (cleaned night trips, first {biggest} month(s))")
    meta = dict(con.execute("SELECT key, value FROM load_meta").fetchall())
    con.close()
    write_json(out / "meta.json", {"months": biggest, "rows": int(meta["rows"]),
                                   "fingerprint": meta["fingerprint"]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
