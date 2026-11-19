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


# ------------------------------------------------------------ presentation helpers (pure, tested)
ANALYTICAL = ["Q1", "Q2", "Q3", "Q5"]   # SQL query timings. Q4 (client-side lookups) is never mixed in.


def speedup_table(summary: pd.DataFrame | None, phase: str = "warm") -> pd.DataFrame:
    """Matched medians for the SAME (months, query, phase): PostgreSQL (no index) vs DuckDB only.

    ratio  = PostgreSQL median seconds / DuckDB median seconds  (> 1: DuckDB faster, < 1: PostgreSQL faster)
    factor = slower / faster, always >= 1, read together with `winner`.
    Rows where one engine is missing or a median is not > 0 are skipped, never guessed.
    """
    cols = ["months", "query", "postgres_s", "duckdb_s", "ratio", "winner", "factor"]
    if summary is None or summary.empty:
        return pd.DataFrame(columns=cols)
    s = summary[(summary["phase"] == phase) & summary["query"].isin(ANALYTICAL)
                & summary["engine"].isin(["postgres", "duckdb"])]
    if s.empty:
        return pd.DataFrame(columns=cols)
    wide = s.pivot_table(index=["months", "query"], columns="engine", values="median_s", aggfunc="first")
    if "postgres" not in wide.columns or "duckdb" not in wide.columns:
        return pd.DataFrame(columns=cols)
    wide = wide.dropna(subset=["postgres", "duckdb"])
    wide = wide[(wide["postgres"] > 0) & (wide["duckdb"] > 0)]
    out = wide.reset_index().rename(columns={"postgres": "postgres_s", "duckdb": "duckdb_s"})
    out.columns.name = None
    out["months"] = out["months"].astype(int)
    out["ratio"] = out["postgres_s"] / out["duckdb_s"]
    out["winner"] = out["ratio"].map(lambda r: "DuckDB" if r > 1 else ("PostgreSQL" if r < 1 else "tie"))
    out["factor"] = out[["postgres_s", "duckdb_s"]].max(axis=1) / out[["postgres_s", "duckdb_s"]].min(axis=1)
    return out[cols].sort_values(["query", "months"]).reset_index(drop=True)


def key_findings(summary: pd.DataFrame | None, lookup: pd.DataFrame | None = None,
                 explain: pd.DataFrame | None = None) -> list[dict]:
    """Up to three descriptive cards for the LARGEST test size, warm medians, built only from the files.

    Call it only after timings_status() said the run is complete and equivalent.
    Each card: {"title", "value", "detail"}. A card whose inputs are missing or unverified is omitted.
    """
    out: list[dict] = []
    sp = speedup_table(summary, "warm")
    if sp.empty:
        return out
    biggest = int(sp["months"].max())

    # 1. analytical query with the largest observed gap (Q1-Q3)
    a = sp[(sp["months"] == biggest) & sp["query"].isin(["Q1", "Q2", "Q3"])]
    if not a.empty:
        r = a.loc[a["factor"].idxmax()]
        out.append({"title": f"Largest analytical gap ({r['query']}, {biggest} mo)",
                    "value": f"{r['winner']} {r['factor']:.1f}x faster",
                    "detail": f"Warm median: PostgreSQL {r['postgres_s']:.4f} s, DuckDB {r['duckdb_s']:.4f} s."})

    # 2. Q4 point lookups: only shown when PostgreSQL is actually faster (a counterexample)
    if lookup is not None and not lookup.empty:
        lw = lookup[(lookup["phase"] == "warm") & (lookup["months"] == biggest)]
        pg = lw[lw["engine"] == "postgres"]["median_ms"]
        dk = lw[lw["engine"] == "duckdb"]["median_ms"]
        if len(pg) and len(dk) and float(pg.iloc[0]) > 0 and float(dk.iloc[0]) > float(pg.iloc[0]):
            n = lw["lookups"].iloc[0]
            out.append({"title": f"Q4 point lookups ({biggest} mo)",
                        "value": f"PostgreSQL {float(dk.iloc[0]) / float(pg.iloc[0]):.1f}x faster",
                        "detail": f"Median per lookup: PostgreSQL {float(pg.iloc[0]):.3f} ms, DuckDB "
                                  f"{float(dk.iloc[0]):.3f} ms ({n} sequential client-side lookups, "
                                  f"not an SQL query time)."})

    # 3. Q5 index effect: only when the recorded plan really used the index
    if explain is not None and not explain.empty and summary is not None:
        e = explain[(explain["months"] == biggest) & (explain["state"] == "with_index")]
        used = len(e) > 0 and e["index_used"].astype(str).str.lower().eq("true").all()
        q5 = summary[(summary["query"] == "Q5") & (summary["phase"] == "warm") & (summary["months"] == biggest)]
        before = q5[q5["engine"] == "postgres"]["median_s"]
        after = q5[q5["engine"] == "postgres_indexed"]["median_s"]
        if used and len(before) and len(after) and float(before.iloc[0]) > 0 and float(after.iloc[0]) > 0:
            b, f = float(before.iloc[0]), float(after.iloc[0])
            word = "faster" if f < b else "slower"
            out.append({"title": f"Q5 with index on pickup_ts ({biggest} mo)",
                        "value": f"{max(b, f) / min(b, f):.1f}x {word}",
                        "detail": f"PostgreSQL warm median {b:.4f} s without, {f:.4f} s with the index "
                                  f"(plan used the index)."})
    return out


def quality_kpis(summ: pd.DataFrame | None) -> dict | None:
    """Totals over all months of data_quality/preprocess_summary.csv.
    `balanced` is True when source = kept + excluded and nothing is unaccounted."""
    if summ is None or summ.empty:
        return None
    k = {"source": int(summ["source_rows"].sum()), "kept": int(summ["night_clean_rows"].sum()),
         "excluded": int(summ["excluded_rows"].sum()), "unaccounted": int(summ["unaccounted_rows"].sum())}
    k["balanced"] = k["unaccounted"] == 0 and k["source"] == k["kept"] + k["excluded"]
    return k
