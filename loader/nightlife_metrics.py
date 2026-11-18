"""Nightlife geography: we count pickup and drop-off EVENTS per taxi zone, for the map.

    data/raw (never modified)  ->  nightlife_metrics.py  ->  results/nightlife/*.csv

Why we wrote a separate exporter
    Our benchmark dataset (preprocess.py) keeps trips whose PICKUP is in the night window. That is
    right for the benchmark, but it loses arrivals: a taxi taken at 17:55 that drops people at 18:15
    is a night arrival. So here we split every trip into two independent events:
      - a pickup event   at tpep_pickup_datetime  in PULocationID
      - a drop-off event at tpep_dropoff_datetime in DOLocationID
    and we classify each event by ITS OWN timestamp. A trip gives 0, 1 or 2 night events.

The definitions we use (docs/NIGHTLIFE_METHODOLOGY.md explains them)
    night            hour >= 18 or hour < 6  (18:00:00 included, 06:00:00 excluded)
    night_date       the date of the evening: events at 00:00-05:59 belong to the previous day
    weekend          nights whose night_date is a Friday or a Saturday; all other nights: weekday
    evening_arrivals drop-offs 18:00-23:59
    late_departures  pickups   00:00-04:59
    late_arrivals    drop-offs 00:00-04:59   (05:00-05:59 is in hourly.csv, not in the late KPIs)
    nightlife_score  late_departures / late_arrivals, blank when late_arrivals = 0
    avg_*            count / nights (nights of that night_type), never / trips

What we write (replaced on every run, never appended)
    results/nightlife/zone_metrics.csv   one row per zone and night_type (weekday, weekend, all)
    results/nightlife/hourly.csv         zone_id, night_type, hour, pickups, dropoffs
    results/nightlife/validation.json    inputs, covered nights, row and event counts

How we run it (from the repository root)
    python loader/nightlife_metrics.py              # default: 4 months from 2026-04 (START_MONTH, TOTAL_MONTHS)
    python loader/nightlife_metrics.py --help
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ZONES_FILE, get_config, month_list, raw_dir, results_dir, trip_file  # noqa: E402

VERSION = "1"

NIGHT_START, NIGHT_END = 18, 6                  # fixed by the data contract, NOT by .env
NIGHT_HOURS = list(range(NIGHT_START, 24)) + list(range(0, NIGHT_END))   # 18..23, 0..5
EVENING_HOURS = range(18, 24)                   # 18:00-23:59
LATE_HOURS = range(0, 5)                        # 00:00-04:59
WEEKEND_DAYS = (4, 5)                           # Monday = 0, so Friday and Saturday
NIGHT_TYPES = ("weekday", "weekend", "all")

AIRPORT_ZONES = {1: "Newark", 132: "JFK", 138: "LaGuardia"}
UNKNOWN_BOROUGHS = {"Unknown", "N/A", ""}
DEFAULT_MIN_EVENTS = 100                        # same default as the dashboard (MIN_TRIPS)

COLUMNS = ["tpep_pickup_datetime", "tpep_dropoff_datetime", "PULocationID", "DOLocationID"]
BATCH_ROWS = 500_000

ZONE_METRICS_COLUMNS = [
    "zone_id", "zone", "borough", "night_type", "nights",
    "evening_arrivals", "late_departures", "late_arrivals",
    "avg_evening_arrivals", "avg_late_departures", "avg_late_arrivals",
    "nightlife_score", "enough_trips", "excluded_reason",
]
HOURLY_COLUMNS = ["zone_id", "night_type", "hour", "pickups", "dropoffs"]

# Trip-level rules, checked in this order (same names and order as preprocess.py, rules 1-4).
# A trip that fails one of them gives NO event at all.
TRIP_REASONS = ["pickup_missing", "pickup_outside_source_month", "dropoff_missing", "dropoff_before_pickup"]


# ---------------------------------------------------------------- small pure helpers (tested)

def month_bounds(month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    year, mon = (int(x) for x in month.split("-"))
    start = datetime(year, mon, 1)
    end = datetime(year + (mon == 12), 1 if mon == 12 else mon + 1, 1)
    return pd.Timestamp(start), pd.Timestamp(end)


def covered_nights(months: list[str]) -> tuple[date, date]:
    """First and last night_date that are COMPLETE in the input files.

    Files are organised by PICKUP month, so the input contains every pickup in [start, end).
    Night D lasts from D 18:00 to D+1 06:00. It is complete only if it starts at or after `start`
    and ends at or before `end`. So the first complete night is the first day of the first month
    (the night before it misses its evening) and the last one is two days before `end`
    (the last night of the last month continues into a month that was not loaded).
    Drop-offs are complete too: they come from trips picked up earlier, which are in the input.
    """
    start, _ = month_bounds(months[0])
    _, end = month_bounds(months[-1])
    first, last = start.date(), (end - timedelta(days=2)).date()
    if last < first:
        raise ValueError("the input covers no complete night")
    return first, last


def nights_per_type(first: date, last: date) -> dict[str, int]:
    days = pd.date_range(first, last, freq="D")
    weekend = int(days.day_of_week.isin(WEEKEND_DAYS).sum())
    return {"weekday": len(days) - weekend, "weekend": weekend, "all": len(days)}


def to_local(ts: pd.Series) -> pd.Series:
    """TLC timestamps are New York local clock time without a time zone: used as they are.
    If a file ever contains time-zone-aware values, convert them to New York time first."""
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("America/New_York").dt.tz_localize(None)
    return ts


def read_zones(path: Path) -> pd.DataFrame:
    z = pd.read_csv(path, dtype={"LocationID": "int64", "Borough": "string", "Zone": "string"},
                    keep_default_na=False)
    z = z.rename(columns={"LocationID": "zone_id", "Zone": "zone", "Borough": "borough"})
    return z[["zone_id", "zone", "borough"]].drop_duplicates("zone_id").sort_values("zone_id")


def trip_reason(pickup: pd.Series, dropoff: pd.Series, start, end) -> pd.Series:
    """Index into TRIP_REASONS for every row, -1 = valid trip. The first rule that applies wins."""
    code = pd.Series(-1, index=pickup.index, dtype="int8")
    rules = [
        pickup.isna(),
        (pickup < start) | (pickup >= end),
        dropoff.isna(),
        dropoff < pickup,
    ]
    for i in reversed(range(len(rules))):        # apply from last to first: earlier rules win
        code = code.mask(rules[i].fillna(False).astype(bool), i)
    return code


def events(ts: pd.Series, zone: pd.Series) -> pd.DataFrame:
    """Night events of one kind (pickup OR drop-off), aggregated: zone_id, night_date, hour, n."""
    hour = ts.dt.hour
    night = (hour >= NIGHT_START) | (hour < NIGHT_END)
    ts, hour, zone = ts[night], hour[night], zone[night]
    night_date = ts.dt.normalize() - pd.to_timedelta((hour < NIGHT_END).astype("int64"), unit="D")
    df = pd.DataFrame({"zone_id": zone.astype("int64"), "night_date": night_date.dt.date, "hour": hour})
    return df.groupby(["zone_id", "night_date", "hour"], as_index=False).size().rename(columns={"size": "n"})


def night_type_of(night_date: pd.Series) -> pd.Series:
    weekend = pd.to_datetime(night_date).dt.day_of_week.isin(WEEKEND_DAYS)
    return pd.Series(np.where(weekend, "weekend", "weekday"), index=night_date.index)


# ---------------------------------------------------------------- reading the raw files

def collect_events(files: list[tuple[str, Path]], zone_ids: set[int], stats: dict) -> pd.DataFrame:
    """Read every file in batches; return aggregated events: kind, zone_id, night_date, hour, n."""
    parts = []
    for month, path in files:
        start, end = month_bounds(month)
        pf = pq.ParquetFile(path)
        missing = [c for c in COLUMNS if c not in pf.schema_arrow.names]
        if missing:
            raise ValueError(f"{path.name} has no column(s) {missing}")
        file_rows = 0
        for batch in pf.iter_batches(batch_size=BATCH_ROWS, columns=COLUMNS):
            df = batch.to_pandas()
            file_rows += len(df)
            pickup = to_local(pd.to_datetime(df["tpep_pickup_datetime"]))
            dropoff = to_local(pd.to_datetime(df["tpep_dropoff_datetime"]))
            reason = trip_reason(pickup, dropoff, start, end)
            for i, name in enumerate(TRIP_REASONS):
                stats["invalid_trips"][name] += int((reason == i).sum())
            valid = reason == -1
            stats["valid_trips"] += int(valid.sum())

            for kind, ts, loc in (("pickup", pickup, df["PULocationID"]), ("dropoff", dropoff, df["DOLocationID"])):
                loc = pd.to_numeric(loc, errors="coerce")
                good_zone = loc.isin(zone_ids)       # null, < 1 or not in the official lookup -> no event
                stats["invalid_zone_events"][kind] += int((valid & ~good_zone).sum())
                keep = valid & good_zone
                ev = events(ts[keep], loc[keep])
                ev.insert(0, "kind", kind)
                parts.append(ev)
        stats["files"].append({"month": month, "file": path.name, "bytes": path.stat().st_size, "rows": file_rows})
        stats["raw_rows"] += file_rows

    if not parts:
        return pd.DataFrame(columns=["kind", "zone_id", "night_date", "hour", "n"])
    allev = pd.concat(parts, ignore_index=True)
    return allev.groupby(["kind", "zone_id", "night_date", "hour"], as_index=False)["n"].sum()


# ---------------------------------------------------------------- building the two tables

def build_hourly(ev: pd.DataFrame, zones: pd.DataFrame) -> pd.DataFrame:
    """Complete grid: every zone x night_type x night hour, zeros included. 'all' = weekday + weekend,
    computed once from the events (never by adding rows of this file again)."""
    by_type = ev.assign(night_type=night_type_of(ev["night_date"]))
    both = pd.concat([by_type, by_type.assign(night_type="all")], ignore_index=True)
    counts = both.pivot_table(index=["zone_id", "night_type", "hour"], columns="kind", values="n",
                              aggfunc="sum", fill_value=0)
    grid = pd.MultiIndex.from_product([zones["zone_id"], NIGHT_TYPES, NIGHT_HOURS],
                                      names=["zone_id", "night_type", "hour"])
    counts = counts.reindex(grid, fill_value=0)
    out = pd.DataFrame({
        "pickups": counts["pickup"] if "pickup" in counts else 0,
        "dropoffs": counts["dropoff"] if "dropoff" in counts else 0,
    }).reset_index()
    out[["pickups", "dropoffs"]] = out[["pickups", "dropoffs"]].astype("int64")
    return out[HOURLY_COLUMNS]


def excluded_reason(zone_id: int, borough, enough: bool) -> str | None:
    """One reason per row, in this priority: unknown zone, airport, low volume. None = valid."""
    if pd.isna(borough) or str(borough).strip() in UNKNOWN_BOROUGHS:
        return "unknown zone"
    if zone_id in AIRPORT_ZONES:
        return "airport"
    if not enough:
        return "low volume"
    return None


def build_zone_metrics(hourly: pd.DataFrame, zones: pd.DataFrame, nights: dict[str, int],
                       min_events: int) -> pd.DataFrame:
    h = hourly
    def total(col, hours):
        sel = h[h["hour"].isin(hours)]
        return sel.groupby(["zone_id", "night_type"])[col].sum()

    m = pd.DataFrame({
        "evening_arrivals": total("dropoffs", EVENING_HOURS),
        "late_departures": total("pickups", LATE_HOURS),
        "late_arrivals": total("dropoffs", LATE_HOURS),
    }).reset_index()
    m = zones.merge(m, on="zone_id", how="left")
    m["nights"] = m["night_type"].map(nights).astype("int64")
    for col in ("evening_arrivals", "late_departures", "late_arrivals"):
        m[col] = m[col].astype("int64")
        m[f"avg_{col}"] = (m[col] / m["nights"]).round(4)
    m["nightlife_score"] = (m["late_departures"] / m["late_arrivals"].where(m["late_arrivals"] > 0)).round(4)
    m["enough_trips"] = ((m["late_departures"] + m["late_arrivals"]) >= min_events) & (m["late_arrivals"] > 0)
    m["excluded_reason"] = [excluded_reason(z, b, e) for z, b, e in
                            zip(m["zone_id"], m["borough"], m["enough_trips"])]
    m["night_type"] = pd.Categorical(m["night_type"], NIGHT_TYPES, ordered=True)
    m = m.sort_values(["zone_id", "night_type"]).reset_index(drop=True)
    m["night_type"] = m["night_type"].astype(str)
    return m[ZONE_METRICS_COLUMNS]


def write_atomic_csv(df: pd.DataFrame, path: Path) -> None:
    """Missing values are written as EMPTY cells (pandas reads them back as NaN)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp, index=False, na_rep="")
    os.replace(tmp, path)


# ---------------------------------------------------------------- the callable function

def build_nightlife_metrics(raw: Path, months: list[str], out_dir: Path,
                            min_events: int = DEFAULT_MIN_EVENTS) -> dict:
    """Read the raw monthly files of `months` from `raw`, write the two CSVs and validation.json
    into `out_dir` (replacing old ones). Returns the validation summary."""
    raw, out_dir = Path(raw), Path(out_dir)
    if not months:
        raise ValueError("no months given")
    if months != month_list(len(months), months[0]):
        raise ValueError(f"months must be consecutive, got {months}")
    files = [(m, raw / trip_file(m)) for m in months]
    needed = [p for _, p in files] + [raw / ZONES_FILE]
    missing = [str(p) for p in needed if not p.exists()]
    if missing:
        raise FileNotFoundError("missing input file(s): " + ", ".join(missing))

    zones = read_zones(raw / ZONES_FILE)
    first, last = covered_nights(months)
    nights = nights_per_type(first, last)

    stats = {"raw_rows": 0, "valid_trips": 0, "files": [],
             "invalid_trips": {r: 0 for r in TRIP_REASONS},
             "invalid_zone_events": {"pickup": 0, "dropoff": 0}}
    ev = collect_events(files, set(zones["zone_id"]), stats)

    in_cover = (ev["night_date"] >= first) & (ev["night_date"] <= last)
    outside = ev[~in_cover].groupby("kind")["n"].sum()
    ev = ev[in_cover]
    counted = ev.groupby("kind")["n"].sum()

    hourly = build_hourly(ev, zones)
    metrics = build_zone_metrics(hourly, zones, nights, min_events)

    out_dir.mkdir(parents=True, exist_ok=True)
    write_atomic_csv(metrics, out_dir / "zone_metrics.csv")
    write_atomic_csv(hourly, out_dir / "hourly.csv")

    summary = {
        "processing_version": VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "input_files": stats["files"],
        "zone_lookup": ZONES_FILE,
        "months": months,
        "complete_nights": {"first_night_date": first.isoformat(), "last_night_date": last.isoformat(),
                            "interval": f"{first} 18:00 -> {last + timedelta(days=1)} 06:00", **nights},
        "raw_rows": stats["raw_rows"],
        "valid_trips": stats["valid_trips"],
        "invalid_trips": stats["invalid_trips"],
        "invalid_zone_events": stats["invalid_zone_events"],
        "night_events_counted": {k: int(counted.get(k, 0)) for k in ("pickup", "dropoff")},
        "night_events_outside_complete_nights": {k: int(outside.get(k, 0)) for k in ("pickup", "dropoff")},
        "min_late_events_for_enough_trips": min_events,
        "excluded_zones": {r: int((metrics["excluded_reason"] == r).sum())
                           for r in ("unknown zone", "airport", "low volume")},
    }
    tmp = out_dir / "validation.json.tmp"
    tmp.write_text(json.dumps(summary, indent=2))
    os.replace(tmp, out_dir / "validation.json")
    return summary


# ---------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    cfg = get_config()
    p = argparse.ArgumentParser(description="Export nightlife pickup/drop-off metrics per taxi zone.")
    p.add_argument("--raw-dir", type=Path, default=raw_dir(), help="folder with the raw Parquet files and taxi_zone_lookup.csv")
    p.add_argument("--start-month", default=cfg.start_month, help="first month, e.g. 2026-04")
    p.add_argument("--months", type=int, default=cfg.total_months, help="number of consecutive months")
    p.add_argument("--out-dir", type=Path, default=results_dir() / "nightlife", help="output folder")
    p.add_argument("--min-events", type=int, default=int(os.environ.get("MIN_TRIPS", DEFAULT_MIN_EVENTS)),
                   help="late pickups + late drop-offs needed for enough_trips (default 100, env MIN_TRIPS)")
    a = p.parse_args(argv)

    months = month_list(a.months, a.start_month)
    s = build_nightlife_metrics(a.raw_dir, months, a.out_dir, a.min_events)
    c = s["complete_nights"]
    print(f"Months: {months[0]} .. {months[-1]}  ({s['raw_rows']:,} raw rows, {s['valid_trips']:,} valid trips)")
    print(f"Complete nights: {c['interval']}  ({c['all']} nights: {c['weekday']} weekday, {c['weekend']} weekend)")
    print(f"Night events counted: {s['night_events_counted']}")
    print(f"Written: {a.out_dir / 'zone_metrics.csv'}, {a.out_dir / 'hourly.csv'}, {a.out_dir / 'validation.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
