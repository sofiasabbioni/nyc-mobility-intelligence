"""Settings and helpers shared by the loader scripts. You never run this file.

Settings are read from the environment EVERY time get_config() is called (no values are frozen
at import time). See .env.example for the list.

Data layout (DATA_DIR):
  raw/    the original monthly Parquet files + taxi_zone_lookup.csv. NEVER modified by our code.
  clean/  cleaned night trips, one file per month + manifest.json (rules, fingerprint, counts)
  full/   OPTIONAL combined dataset with every original trip (build_full_dataset.py)
  taxi_Nm.duckdb   DuckDB databases, one per test size
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sql"))
import night  # noqa: E402

# Bump this number whenever the cleaning rules in preprocess.py change: old clean files are
# then rebuilt automatically.
RULES_VERSION = "2"


@dataclass(frozen=True)
class Config:
    start_month: str
    total_months: int
    night_start: int
    night_end: int
    data_dir: Path
    results_dir: Path
    sizes: tuple


def _month_ok(value: str) -> bool:
    m = re.fullmatch(r"(\d{4})-(\d{2})", value)
    return bool(m) and 1 <= int(m.group(2)) <= 12


def get_config() -> Config:
    e = os.environ.get
    start_month = e("START_MONTH", "2026-04")
    if not _month_ok(start_month):
        raise ValueError(f"START_MONTH must look like 2026-04, got {start_month!r}")
    total = int(e("TOTAL_MONTHS", "4"))
    if total < 1:
        raise ValueError("TOTAL_MONTHS must be at least 1")
    ns, ne = int(e("NIGHT_START", "18")), int(e("NIGHT_END", "6"))
    night.validate_window(ns, ne)
    sizes = tuple(int(x) for x in e("SIZES", "1,2,4").split(",") if x.strip())
    if not sizes or list(sizes) != sorted(set(sizes)) or sizes[0] < 1 or sizes[-1] > total:
        raise ValueError(f"SIZES must be increasing months between 1 and TOTAL_MONTHS={total}, got {sizes}")
    return Config(start_month, total, ns, ne, Path(e("DATA_DIR", "./data")),
                  Path(e("RESULTS_DIR", "./results")), sizes)


def month_list(n: int, start_month: str | None = None) -> list[str]:
    year, month = (int(x) for x in (start_month or get_config().start_month).split("-"))
    out = []
    for _ in range(n):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out


def raw_dir() -> Path:
    return get_config().data_dir / "raw"


def clean_dir() -> Path:
    return get_config().data_dir / "clean"


def full_dir() -> Path:
    return get_config().data_dir / "full"


def results_dir() -> Path:
    return get_config().results_dir


def quality_dir() -> Path:
    return get_config().results_dir / "data_quality"


def duck_path(months: int) -> Path:
    return get_config().data_dir / f"taxi_{months}m.duckdb"


def trip_file(month: str) -> str:
    return f"yellow_tripdata_{month}.parquet"


ZONES_FILE = "taxi_zone_lookup.csv"


def append_csv(path: Path, header: list, row: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


def write_csv(path: Path, header: list, rows: list[list]) -> None:
    """Write a complete CSV file (replaces the old one)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 22):
            h.update(chunk)
    return h.hexdigest()


def manifest_path() -> Path:
    return clean_dir() / "manifest.json"


def read_manifest() -> dict | None:
    p = manifest_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, path)


def fingerprint(cfg: Config, raw_files: list[dict], zones_sha256: str) -> str:
    """Identity of a clean dataset: rules version + every setting that changes the clean rows +
    the exact content (SHA-256) of every raw input. If ANY of these changes, old clean files,
    old databases and old results do not match any more."""
    payload = {
        "rules_version": RULES_VERSION,
        "start_month": cfg.start_month,
        "total_months": cfg.total_months,
        "night_start": cfg.night_start,
        "night_end": cfg.night_end,
        "raw": [{"name": r["name"], "sha256": r["sha256"]} for r in raw_files],
        "zones_sha256": zones_sha256,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def months_rows(manifest: dict, months: int) -> int:
    """Number of clean rows in the first `months` months, from the manifest."""
    return sum(m["kept"] for m in manifest["months"][:months])
