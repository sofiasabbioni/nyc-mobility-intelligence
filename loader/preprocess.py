"""Step 2: build the CLEANED NIGHT-TRIP dataset from the raw monthly files.

    data/raw (never modified)  ->  preprocess.py  ->  data/clean  ->  both databases

IMPORTANT: data/clean is a SUBSET of the raw data (valid trips with pickup in the night window).
It is NOT the complete dataset. The complete one (every trip, every column) is the optional
output of build_full_dataset.py.

Every source row ends in exactly ONE of these buckets, checked in this order (the first rule
that applies wins, so the reasons never overlap):
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from common import (RULES_VERSION, ZONES_FILE, Config, clean_dir, fingerprint, get_config,
                    manifest_path, month_list, night, quality_dir, raw_dir, read_manifest,
                    sha256_file, trip_file, write_csv, write_json)

# (code, name, description). Code 0 = kept. Order = priority.
REASONS = [
    (1, "pickup_missing", "pickup timestamp is null"),
    (2, "pickup_outside_source_month", "pickup timestamp is not inside the month of its own file"),
    (3, "dropoff_missing", "dropoff timestamp is null"),
    (4, "dropoff_before_pickup", "dropoff timestamp is earlier than pickup timestamp"),
    (5, "invalid_zone", "PULocationID or DOLocationID is null or smaller than 1"),
    (6, "invalid_amount", "trip_distance, fare_amount, tip_amount or total_amount is null, NaN or negative"),
    (7, "outside_night_window", "otherwise valid trip whose pickup hour is not in the night window"),
]
__doc__ += "\n".join(f"  {c}. {n}: {d}" for c, n, d in REASONS) + """

passenger_count may be NULL in the source: that is NOT an exclusion reason, the NULL is kept.
trip_id = 1, 2, 3, ... in month order, then row order of the source file (stable across runs).
Time zone: TLC timestamps are New York local time and are used as they are.

Rerun behaviour: a manifest (clean/manifest.json) stores a fingerprint of the rules version, the
settings and the SHA-256 of every raw file. If the fingerprint is the same, the clean files are
reused; if anything changed (night window, months, raw content, rules) they are rebuilt, never
silently reused. FORCE_REBUILD=1 forces a rebuild.
"""

# raw column -> our column -> type  (same order as sql/schema.sql, after trip_id)
COLUMNS = [
    ("tpep_pickup_datetime", "pickup_ts", pa.timestamp("us")),
    ("tpep_dropoff_datetime", "dropoff_ts", pa.timestamp("us")),
    ("PULocationID", "pu_location_id", pa.int32()),
    ("DOLocationID", "do_location_id", pa.int32()),
    ("passenger_count", "passenger_count", pa.int32()),
    ("trip_distance", "trip_distance", pa.float64()),
    ("fare_amount", "fare_amount", pa.float64()),
    ("tip_amount", "tip_amount", pa.float64()),
    ("total_amount", "total_amount", pa.float64()),
]
SCHEMA = pa.schema([("trip_id", pa.int64())] + [(c[1], c[2]) for c in COLUMNS])
BATCH_ROWS = 500_000
AMOUNTS = ("trip_distance", "fare_amount", "tip_amount", "total_amount")


def month_bounds(month: str):
    """[start, end) of a month as timestamp scalars."""
    year, mon = (int(x) for x in month.split("-"))
    start = datetime(year, mon, 1)
    end = datetime(year + (mon == 12), 1 if mon == 12 else mon + 1, 1)
    return pa.scalar(start, pa.timestamp("us")), pa.scalar(end, pa.timestamp("us"))


def classify(t: pa.Table, start, end, night_start: int, night_end: int) -> np.ndarray:
    """Reason code for every row (0 = keep). Pure function, tested in tests/."""
    n = t.num_rows
    code = pa.array(np.zeros(n, dtype=np.int8))

    def hit(cond, value):  # earlier (smaller) codes must win, so we apply from the last to the first
        nonlocal code
        code = pc.if_else(pc.fill_null(cond, False), pa.scalar(value, pa.int8()), code)

    pickup, dropoff = t["pickup_ts"], t["dropoff_ts"]
    hours = night.night_hours(night_start, night_end)
    hour = pc.hour(pickup)
    in_night = pc.is_in(hour, value_set=pa.array(hours, type=hour.type))
    hit(pc.invert(in_night), 7)
    bad_amount = None
    for col in AMOUNTS:  # null, NaN and negative are all "not >= 0"
        bad = pc.invert(pc.fill_null(pc.greater_equal(t[col], 0), False))
        bad_amount = bad if bad_amount is None else pc.or_(bad_amount, bad)
    hit(bad_amount, 6)
    bad_zone = None
    for col in ("pu_location_id", "do_location_id"):
        bad = pc.invert(pc.fill_null(pc.greater_equal(t[col], 1), False))
        bad_zone = bad if bad_zone is None else pc.or_(bad_zone, bad)
    hit(bad_zone, 5)
    hit(pc.less(dropoff, pickup), 4)
    hit(pc.is_null(dropoff), 3)
    hit(pc.invert(pc.fill_null(pc.and_(pc.greater_equal(pickup, start), pc.less(pickup, end)), False)), 2)
    hit(pc.is_null(pickup), 1)
    return code.to_numpy(zero_copy_only=False)


def clean_month(src: Path, dst: Path, month: str, first_id: int, cfg: Config) -> dict:
    """Clean one month. Returns the accounting of every source row."""
    start, end = month_bounds(month)
    tmp = dst.with_suffix(".parquet.part")
    counts = np.zeros(len(REASONS) + 1, dtype=np.int64)  # index = reason code, 0 = kept
    in_window_any = 0
    next_id = first_id
    with pq.ParquetWriter(tmp, SCHEMA, compression="snappy") as writer:
        for batch in pq.ParquetFile(src).iter_batches(
            batch_size=BATCH_ROWS, columns=[c[0] for c in COLUMNS]
        ):
            raw = pa.Table.from_batches([batch])
            t = pa.table({ours: pc.cast(raw[theirs], typ, safe=False) for theirs, ours, typ in COLUMNS})
            code = classify(t, start, end, cfg.night_start, cfg.night_end)
            counts += np.bincount(code, minlength=len(counts))
            hours = pc.hour(t["pickup_ts"])
            in_window_any += int(pc.sum(pc.is_in(
                hours, value_set=pa.array(night.night_hours(cfg.night_start, cfg.night_end),
                                          type=hours.type)).cast(pa.int64())).as_py() or 0)
            kept = t.filter(pa.array(code == 0))
            ids = pa.array(np.arange(next_id, next_id + kept.num_rows, dtype=np.int64))
            kept = kept.add_column(0, "trip_id", ids)
            writer.write_table(kept.cast(SCHEMA))
            next_id += kept.num_rows
    os.replace(tmp, dst)  # only a complete file gets its final name
    kept_rows = int(counts[0])
    return {
        "month": month,
        "source_rows": int(counts.sum()),
        "kept": kept_rows,
        "first_id": first_id if kept_rows else None,
        "last_id": first_id + kept_rows - 1 if kept_rows else None,
        "pickup_hour_in_window_any_validity": in_window_any,
        "excluded": {name: int(counts[code]) for code, name, _ in REASONS},
    }


def describe_raw(cfg: Config) -> tuple[list[dict], str]:
    """Name, size, SHA-256, rows and columns of every raw input (read only)."""
    raw = raw_dir()
    files = []
    for m in month_list(cfg.total_months, cfg.start_month):
        p = raw / trip_file(m)
        meta = pq.read_metadata(p)
        files.append({"name": p.name, "month": m, "bytes": p.stat().st_size, "sha256": sha256_file(p),
                      "rows": meta.num_rows, "columns": meta.num_columns})
    return files, sha256_file(raw / ZONES_FILE)


def write_reports(manifest: dict) -> None:
    q = quality_dir()
    write_csv(q / "raw_files.csv", ["file", "month", "bytes", "sha256", "rows", "columns"],
              [[r["name"], r["month"], r["bytes"], r["sha256"], r["rows"], r["columns"]]
               for r in manifest["raw"]])
    write_csv(q / "exclusion_rules.csv", ["priority", "reason", "description"],
              [[c, n, d] for c, n, d in REASONS])
    rows, summary = [], []
    for m in manifest["months"]:
        for _, name, _ in REASONS:
            n = m["excluded"][name]
            rows.append([m["month"], name, n, f"{100 * n / max(m['source_rows'], 1):.4f}"])
        excluded = sum(m["excluded"].values())
        summary.append([m["month"], m["source_rows"], m["kept"], excluded,
                        m["source_rows"] - m["kept"] - excluded,
                        m["pickup_hour_in_window_any_validity"], m["first_id"], m["last_id"]])
    write_csv(q / "exclusions.csv", ["source_month", "reason", "rows", "pct_of_source"], rows)
    write_csv(q / "preprocess_summary.csv",
              ["source_month", "source_rows", "night_clean_rows", "excluded_rows", "unaccounted_rows",
               "pickup_in_night_window_any_validity", "first_trip_id", "last_trip_id"], summary)


def validate(manifest: dict, cfg: Config) -> list[tuple[str, bool, str]]:
    """Independent checks of the clean files. Uses DuckDB SQL, not the pyarrow code above."""
    import duckdb

    checks: list[tuple[str, bool, str]] = []
    con = duckdb.connect()
    prev_last = 0
    hours = ",".join(str(h) for h in night.night_hours(cfg.night_start, cfg.night_end))
    total_source = total_kept = total_excl = 0
    for m in manifest["months"]:
        month = m["month"]
        path = clean_dir() / trip_file(month)
        excluded = sum(m["excluded"].values())
        total_source += m["source_rows"]; total_kept += m["kept"]; total_excl += excluded
        raw_rows = next(r["rows"] for r in manifest["raw"] if r["month"] == month)
        checks.append((f"{month} source rows = parquet metadata", m["source_rows"] == raw_rows,
                       f"{m['source_rows']} vs {raw_rows}"))
        checks.append((f"{month} kept + excluded = source", m["kept"] + excluded == m["source_rows"],
                       f"{m['kept']} + {excluded} = {m['kept'] + excluded}, source {m['source_rows']}"))
        start, end = month_bounds(month)
        lo, hi = start.as_py(), end.as_py()
        r = con.execute(f"""
            SELECT count(*), min(trip_id), max(trip_id), count(DISTINCT trip_id),
                   min(pickup_ts), max(pickup_ts),
                   count(*) FILTER (WHERE dropoff_ts < pickup_ts OR dropoff_ts IS NULL OR pickup_ts IS NULL),
                   count(*) FILTER (WHERE pu_location_id < 1 OR do_location_id < 1
                                    OR pu_location_id IS NULL OR do_location_id IS NULL),
                   count(*) FILTER (WHERE NOT (trip_distance >= 0 AND fare_amount >= 0
                                    AND tip_amount >= 0 AND total_amount >= 0)),
                   count(*) FILTER (WHERE extract(hour FROM pickup_ts) NOT IN ({hours}))
            FROM read_parquet('{path.as_posix()}')""").fetchone()
        n, idmin, idmax, idn, tmin, tmax, bad_t, bad_z, bad_a, bad_h = r
        checks.append((f"{month} clean rows = kept", n == m["kept"], f"{n} vs {m['kept']}"))
        if n:
            checks.append((f"{month} trip_id contiguous and continuing",
                           idmin == prev_last + 1 and idmax - idmin + 1 == n and idn == n,
                           f"ids {idmin}..{idmax}, rows {n}, distinct {idn}, previous last id {prev_last}"))
            checks.append((f"{month} pickup inside the month [{lo:%Y-%m-%d}, {hi:%Y-%m-%d})",
                           lo <= tmin and tmax < hi, f"{tmin} .. {tmax}"))
            prev_last = idmax
        checks.append((f"{month} timestamps valid (dropoff >= pickup, none null)", bad_t == 0, f"{bad_t} bad rows"))
        checks.append((f"{month} zones >= 1", bad_z == 0, f"{bad_z} bad rows"))
        checks.append((f"{month} amounts >= 0", bad_a == 0, f"{bad_a} bad rows"))
        checks.append((f"{month} pickup hour in night window {night.label(cfg.night_start, cfg.night_end)}",
                       bad_h == 0, f"{bad_h} rows outside"))
        # ids are also strictly increasing inside the file
        ordered = con.execute(f"""SELECT count(*) FROM (SELECT trip_id - lag(trip_id) OVER () AS d
                                  FROM read_parquet('{path.as_posix()}')) WHERE d IS NOT NULL AND d <> 1""").fetchone()[0]
        checks.append((f"{month} trip_id strictly +1 in file order", ordered == 0, f"{ordered} breaks"))
    checks.append(("TOTAL processed + excluded = source",
                   total_kept + total_excl == total_source,
                   f"{total_kept} + {total_excl} = {total_kept + total_excl}, source {total_source}"))
    con.close()
    return checks


def write_validation(checks: list[tuple[str, bool, str]]) -> bool:
    write_csv(quality_dir() / "validation.csv", ["check", "status", "detail"],
              [[c, "PASS" if ok else "FAIL", d] for c, ok, d in checks])
    bad = [c for c in checks if not c[1]]
    for c, ok, d in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {c}: {d}")
    return not bad


def main() -> int:
    cfg = get_config()
    raw, clean = raw_dir(), clean_dir()
    months = month_list(cfg.total_months, cfg.start_month)
    needed = [raw / trip_file(m) for m in months] + [raw / ZONES_FILE]
    missing = [str(p) for p in needed if not p.exists()]
    if missing:
        print("Missing raw files, run download_data.py first:\n  " + "\n  ".join(missing))
        return 1
    clean.mkdir(parents=True, exist_ok=True)

    print(f"Cleaned NIGHT-TRIP dataset (a subset of the raw data): pickup "
          f"{night.label(cfg.night_start, cfg.night_end)}, New York local time")
    t0 = time.perf_counter()
    raw_files, zones_sha = describe_raw(cfg)
    fp = fingerprint(cfg, raw_files, zones_sha)
    old = read_manifest()
    reusable = (old is not None and old.get("fingerprint") == fp and os.environ.get("FORCE_REBUILD") != "1"
                and all((clean / trip_file(m["month"])).exists()
                        and pq.read_metadata(clean / trip_file(m["month"])).num_rows == m["kept"]
                        for m in old["months"]))
    if reusable:
        print(f"Clean files are up to date (fingerprint {fp[:12]}), reusing them.")
        manifest = old
    else:
        if old is not None or any(clean.glob("*.parquet*")):
            print("Settings, rules or raw files changed (or FORCE_REBUILD=1): "
                  "removing the old clean files and rebuilding.")
        for p in list(clean.glob("*.parquet")) + list(clean.glob("*.part")) + [manifest_path()]:
            p.unlink(missing_ok=True)
        month_stats, next_id = [], 1
        for m in months:
            stats = clean_month(raw / trip_file(m), clean / trip_file(m), m, next_id, cfg)
            next_id += stats["kept"]
            month_stats.append(stats)
            print(f"{m}: {stats['source_rows']:,} source rows -> {stats['kept']:,} kept, "
                  f"{stats['source_rows'] - stats['kept']:,} excluded")
        manifest = {
            "fingerprint": fp, "rules_version": RULES_VERSION,
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "config": {"start_month": cfg.start_month, "total_months": cfg.total_months,
                       "night_start": cfg.night_start, "night_end": cfg.night_end,
                       "night_label": night.label(cfg.night_start, cfg.night_end)},
            "raw": raw_files, "zones_sha256": zones_sha, "months": month_stats,
            "reasons": [{"priority": c, "reason": n, "description": d} for c, n, d in REASONS],
        }
        write_json(manifest_path(), manifest)  # written last: a manifest means a complete clean set

    write_reports(manifest)
    print("\nExcluded rows by reason and source month (they sum to source - kept):")
    for m in manifest["months"]:
        parts = ", ".join(f"{k}={v:,}" for k, v in m["excluded"].items() if v)
        print(f"  {m['month']}: {parts or 'none'}")
    print("\nValidation:")
    ok = write_validation(validate(manifest, cfg))
    total = sum(m["kept"] for m in manifest["months"])
    print(f"\nsource rows: {sum(m['source_rows'] for m in manifest['months']):,}; "
          f"night clean rows: {total:,} (this is NOT the complete dataset)")
    print(f"preprocess time: {time.perf_counter() - t0:.1f} s")
    if not ok:
        print("ERROR: validation failed, see results/data_quality/validation.csv")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
