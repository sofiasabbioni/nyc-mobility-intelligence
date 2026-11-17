"""Reading result files safely. No Streamlit here, so it can be unit-tested.

Every reader returns (data, problem). `problem` is None when the file is fine, otherwise a short
text for the dashboard. A missing, empty, half-written or wrong-schema file never raises.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

SCHEMAS = {
    "benchmark.csv": ["engine", "months", "rows", "query", "phase", "run", "query_order", "seconds"],
    "benchmark_summary.csv": ["engine", "months", "rows", "query", "workload", "phase", "n", "mean_s",
                              "median_s", "stdev_s", "min_s", "max_s"],
    "lookup_latency.csv": ["engine", "months", "phase", "lookups", "mean_ms", "median_ms", "p95_ms",
                           "p99_ms", "max_ms"],
    "load_times.csv": ["engine", "months", "rows", "seconds"],
    "disk_sizes.csv": ["what", "months", "bytes"],
    "index_build.csv": ["months", "seconds"],
    "explain_q5.csv": ["months", "state", "plan_nodes", "index_used", "planning_ms", "execution_ms"],
    "equivalence.csv": ["months", "query", "engine_a", "engine_b", "rows_compared", "status", "detail"],
    "db_consistency.csv": ["months", "check", "postgres", "duckdb", "status"],
    "datasets.csv": ["months", "rows", "first_trip_id", "last_trip_id", "pct_of_largest_rows"],
    "memory_summary.csv": ["engine", "months", "container", "peak_mem_mb"],
    "data_quality/exclusions.csv": ["source_month", "reason", "rows", "pct_of_source"],
    "data_quality/preprocess_summary.csv": ["source_month", "source_rows", "night_clean_rows",
                                            "excluded_rows", "unaccounted_rows"],
    "data_quality/validation.csv": ["check", "status", "detail"],
    "city/hourly.csv": ["hour", "trips", "avg_fare", "avg_tip"],
    "city/top_zones.csv": ["zone", "borough", "trips"],
    "city/boroughs.csv": ["borough", "trips"],
}


def read_csv_safe(results: Path, name: str, required: list[str] | None = None):
    path = Path(results) / name
    required = SCHEMAS.get(name, []) if required is None else required
    if not path.exists():
        return None, f"{name} not found yet"
    try:
        df = pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001 - empty, truncated or unreadable file
        return None, f"{name} cannot be read ({type(exc).__name__})"
    missing = [c for c in required if c not in df.columns]
    if missing:
        return None, f"{name} lacks columns {missing}"
    if df.empty:
        return None, f"{name} is empty"
    return df, None


def read_json_safe(results: Path, name: str):
    path = Path(results) / name
    if not path.exists():
        return None, f"{name} not found yet"
    try:
        data = json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001
        return None, f"{name} cannot be read ({type(exc).__name__})"
    return (data, None) if isinstance(data, dict) else (None, f"{name} has an unexpected format")


def timings_status(results: Path) -> tuple[bool, str]:
    """Timings may be shown only for a COMPLETE run whose equivalence checks all passed."""
    status, problem = read_json_safe(results, "run_status.json")
    if status is None:
        return False, "The benchmark has not started or has not written its status yet."
    state = status.get("state")
    if state == "running":
        return False, "The benchmark is still running. Refresh this page when it has finished."
    if state != "complete":
        return False, f"The benchmark did not complete (state: {state}, reason: {status.get('reason', 'unknown')}). Timings are not shown."
    eq, problem = read_csv_safe(results, "equivalence.csv")
    if eq is None:
        return False, f"Equivalence results unavailable ({problem}). Timings are not shown."
    if (eq["status"] != "PASS").any():
        return False, "Some query results differed between PostgreSQL and DuckDB. Timings are not shown."
    return True, "All query results are identical on PostgreSQL and DuckDB."


def size_labels(datasets: pd.DataFrame | None, months_values) -> dict:
    """Label with the ACTUAL number of rows, e.g. '1 month: 1,502,331 rows (25.0% of the rows of the largest test)'."""
    out = {}
    for m in sorted(set(int(x) for x in months_values)):
        if datasets is not None and (datasets["months"] == m).any():
            r = datasets[datasets["months"] == m].iloc[0]
            out[m] = f"{m} mo · {int(r['rows']):,} rows ({float(r['pct_of_largest_rows']):.0f}% of rows)"
        else:
            out[m] = f"{m} mo"
    return out


def ratio_sentences(summary: pd.DataFrame, labels: dict) -> list[str]:
    """Observed ratios of WARM median times, PostgreSQL / DuckDB, for the largest test size.
    Only descriptive statements about this run, not general claims."""
    if summary is None:
        return []
    warm = summary[summary["phase"] == "warm"]
    if warm.empty:
        return []
    biggest = warm["months"].max()
    out = []
    for q in sorted(warm["query"].unique()):
        sub = warm[(warm["months"] == biggest) & (warm["query"] == q)]
        pg = sub[sub["engine"] == "postgres"]["median_s"]
        dk = sub[sub["engine"] == "duckdb"]["median_s"]
        if len(pg) and len(dk) and float(dk.iloc[0]) > 0:
            out.append(f"{q}, {labels.get(int(biggest), biggest)}: PostgreSQL warm median "
                       f"{float(pg.iloc[0]):.4f} s, DuckDB {float(dk.iloc[0]):.4f} s "
                       f"(ratio {float(pg.iloc[0]) / float(dk.iloc[0]):.1f}x)")
    return out
