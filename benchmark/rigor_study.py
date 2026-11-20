"""Read-only benchmark rigor study: query plans and parallelism for Q1-Q3 (Marin).

A SUPPLEMENT to benchmark.py, never a replacement. It does not change benchmark.py, the SQL
in sql/queries.py, the equivalence gate or any existing result file. See docs/BENCHMARK_RIGOR.md.

What it can do (all read-only):
  plans    PostgreSQL EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) and DuckDB EXPLAIN ANALYZE of the
           canonical Q1-Q3, for each test size and each parallelism variant. Records estimated vs
           actual rows of the aggregate nodes, workers planned / launched, scan and aggregate nodes.
  timings  supplementary timings in several independent SESSIONS: each session has its own seeded
           query order, and the engine order alternates (PostgreSQL first, then DuckDB first, ...).
           Variants: engine defaults, DuckDB threads (e.g. 1, 3) and PostgreSQL
           max_parallel_workers_per_gather (e.g. 0, 2), all session-local. These settings do NOT
           guarantee equal effective CPU use; actual PostgreSQL workers are read from the plans.

SAFETY
  * --help and --dry-run never connect to a database and never import the database drivers.
  * Real database access needs --confirm-real-run.
  * DuckDB files are opened with read_only=True. PostgreSQL sessions are opened with
    default_transaction_read_only=on (checked with SHOW before any query), and every statement
    passes a guard that only allows SELECT / WITH, EXPLAIN of those, SHOW and two session settings.
  * Before running, the tool checks that every schema / DuckDB file holds the dataset of
    clean/manifest.json (fingerprint and row count). It never creates, loads or repairs anything.
  * Output goes to a NEW directory (default RESULTS_DIR/marin_rigor_YYYYMMDD) that must not exist
    yet; it is never the root of RESULTS_DIR, never inside DATA_DIR, and existing files are never
    overwritten. benchmark.csv / benchmark_summary.csv are never written.
  * "first" executions are NOT cold-cache measurements (the OS cache is not flushed).

Real run (only after Sofia approves the environment; the db service must already be running with the
loaded schemas m1, m2, m4; --no-deps so that the loader is NOT started):
  docker compose run --rm --no-deps benchmark python rigor_study.py --confirm-real-run --stage plans
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sql"))
import queries as Q  # noqa: E402
from equivalence import compare_results  # noqa: E402
from stats import describe  # noqa: E402

STUDY_QUERIES = ("Q1", "Q2", "Q3")
ENGINES = ("postgres", "duckdb")
DEFAULT = "default"
# Files of the main benchmark: this tool never writes a file with one of these names.
PROTECTED_NAMES = {
    "benchmark.csv", "benchmark_summary.csv", "lookup_latency.csv", "disk_sizes.csv", "index_build.csv",
    "explain_q5.csv", "phases.csv", "equivalence.csv", "db_consistency.csv", "datasets.csv",
    "rejected_timings.csv", "environment.txt", "run_status.json", "load_times.csv",
}
CAVEATS = [
    "System-level comparison: PostgreSQL is client/server, DuckDB is embedded in this Python process; "
    "planner, execution pipeline, overhead and caching differ, not only the storage layout.",
    "Thread / worker settings limit parallelism but do not guarantee equal effective CPU use; "
    "max_parallel_workers_per_gather=N allows up to N workers plus the leader, and the planner may use fewer.",
    "'first' is the first execution on a new connection, NOT a cold-cache run: the OS page cache is not "
    "flushed and PostgreSQL shared buffers survive between connections.",
    "EXPLAIN ANALYZE executes the SELECT (read-only, but it uses CPU and I/O).",
    "Q4 and Q5 are separate workloads (lookup latency, index experiment) and are not part of this study.",
]


class StudyError(RuntimeError):
    """A problem that stops the study without changing anything."""


# ------------------------------------------------------------------ read-only guard
_WRITE_WORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|VACUUM|GRANT|REVOKE|COPY|REINDEX|CLUSTER|"
    r"COMMENT|INTO|ATTACH|DETACH|INSTALL|LOAD|CHECKPOINT|EXPORT|IMPORT|CALL|DO|LOCK|REFRESH|SECURITY|"
    r"PRAGMA|RESET)\b", re.IGNORECASE)
_EXPLAIN = re.compile(r"^EXPLAIN\s*(\([^)]*\)\s*)?(ANALY[SZ]E\s+)?(?P<body>.*)$", re.IGNORECASE | re.DOTALL)
_SET = re.compile(r"^SET\s+(SESSION\s+)?(?P<name>max_parallel_workers_per_gather|search_path|threads)"
                  r"\s*(=|TO)\s*(?P<value>[A-Za-z0-9_]+)$", re.IGNORECASE)
_EXPLAIN_OPTIONS = {"ANALYZE", "ANALYSE", "BUFFERS", "VERBOSE", "COSTS", "TIMING", "SUMMARY", "SETTINGS",
                    "FORMAT", "JSON", "TEXT", "TRUE", "FALSE", "ON", "OFF"}


def assert_read_only_sql(sql: str) -> str:
    """Return the statement if it is allowed, raise StudyError otherwise.

    Allowed: one SELECT / WITH query, EXPLAIN [ANALYZE] of one, SHOW, and SET of
    max_parallel_workers_per_gather, search_path or threads (session-local settings)."""
    s = sql.strip().rstrip(";").strip()
    if not s:
        raise StudyError("empty SQL")
    if ";" in s:
        raise StudyError("only one statement is allowed")
    m = _SET.match(s)
    if m:
        return sql
    if re.match(r"^SHOW\s+[A-Za-z_]+$", s, re.IGNORECASE):
        return sql
    body = s
    m = _EXPLAIN.match(s)
    if m:
        options = m.group(1) or ""
        for word in re.findall(r"[A-Za-z_]+", options):
            if word.upper() not in _EXPLAIN_OPTIONS:
                raise StudyError(f"EXPLAIN option not allowed: {word}")
        body = m.group("body").strip()
    if not re.match(r"^(SELECT|WITH)\b", body, re.IGNORECASE):
        raise StudyError(f"not a read-only query: {s[:60]!r}")
    bad = _WRITE_WORDS.search(body)
    if bad:
        raise StudyError(f"write keyword {bad.group(1).upper()!r} is not allowed in this study")
    return sql


class Executor:
    """Uniform read-only interface over one database connection. `raw_run(sql) -> rows`."""

    def __init__(self, engine: str, months: int, variant: str, raw_run, close=lambda: None):
        self.engine, self.months, self.variant = engine, months, variant
        self._raw_run, self._close = raw_run, close
        self._checked: set[str] = set()  # each statement is checked once, so timed runs add no regex work

    def run(self, sql: str) -> list[tuple]:
        if sql not in self._checked:
            assert_read_only_sql(sql)
            self._checked.add(sql)
        return self._raw_run(sql)

    def close(self) -> None:
        self._close()


# ------------------------------------------------------------------ settings and variants
def parse_int_list(text: str, name: str) -> list[int]:
    try:
        values = [int(x) for x in text.split(",") if x.strip()]
    except ValueError:
        raise StudyError(f"{name} must be comma-separated integers, got {text!r}") from None
    if not values or any(v < 0 for v in values):
        raise StudyError(f"{name} must hold non-negative integers, got {text!r}")
    return values


def variants(duckdb_threads: list[int], pg_workers: list[int]) -> list[dict]:
    """Engine defaults first, then one labelled variant per setting value."""
    out = [{"engine": "postgres", "variant": DEFAULT, "setting": None, "value": None},
           {"engine": "duckdb", "variant": DEFAULT, "setting": None, "value": None}]
    for w in pg_workers:
        out.append({"engine": "postgres", "variant": f"pg_workers_{w}",
                    "setting": "max_parallel_workers_per_gather", "value": w})
    for t in duckdb_threads:
        if t < 1:
            raise StudyError("DuckDB threads must be at least 1")
        out.append({"engine": "duckdb", "variant": f"duckdb_threads_{t}", "setting": "threads", "value": t})
    return out


def study_sql(names=STUDY_QUERIES) -> dict[str, str]:
    """The canonical SQL text from sql/queries.py, unchanged."""
    all_sql = Q.queries()
    unknown = [n for n in names if n not in STUDY_QUERIES]
    if unknown:
        raise StudyError(f"this study covers Q1-Q3 only, not {unknown}")
    return {n: all_sql[n] for n in names}


def session_schedule(sessions: int, seed: int, names=STUDY_QUERIES) -> list[dict]:
    """Deterministic: session i uses seed + i for its query order; the engine order alternates."""
    if sessions < 1:
        raise StudyError("--sessions must be at least 1")
    plan = []
    for i in range(sessions):
        order = list(names)
        random.Random(seed + i).shuffle(order)
        engines = list(ENGINES) if i % 2 == 0 else list(reversed(ENGINES))
        plan.append({"session": i + 1, "seed": seed + i, "engine_order": engines, "query_order": order})
    return plan


# ------------------------------------------------------------------ output (never overwrite)
class OutputDir:
    """A NEW directory; refuses existing paths, the results root, data folders and protected names."""

    def __init__(self, path: Path, results_dir: Path, data_dir: Path):
        path, results_dir, data_dir = (Path(p).resolve() for p in (path, results_dir, data_dir))
        if path == results_dir:
            raise StudyError("the output directory must be a NEW subdirectory, not RESULTS_DIR itself")
        if path == data_dir or data_dir in path.parents:
            raise StudyError(f"refusing to write inside DATA_DIR ({data_dir})")
        if path.exists():
            raise StudyError(f"{path} already exists: choose a new --output-dir (nothing was overwritten)")
        self.path = path

    def create(self) -> None:
        self.path.mkdir(parents=True, exist_ok=False)

    def file(self, name: str) -> Path:
        p = (self.path / name).resolve()
        if self.path not in p.parents:
            raise StudyError(f"{name} is outside the output directory")
        if p.name in PROTECTED_NAMES:
            raise StudyError(f"{p.name} belongs to the main benchmark and is never written by this study")
        if p.exists():
            raise StudyError(f"{p} already exists: refusing to overwrite")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def write_text(self, name: str, text: str) -> Path:
        p = self.file(name)
        with open(p, "x", encoding="utf-8") as f:
            f.write(text)
        return p

    def write_json(self, name: str, data) -> Path:
        return self.write_text(name, json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")

    def write_csv(self, name: str, header: list[str], rows: list[list]) -> Path:
        p = self.file(name)
        with open(p, "x", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        return p


# ------------------------------------------------------------------ plan parsing
def walk_plan(node: dict):
    yield node
    for child in node.get("Plans", []) or []:
        yield from walk_plan(child)


def summarize_pg_plan(explain_json) -> dict:
    """Facts from EXPLAIN (ANALYZE, FORMAT JSON). Fields PostgreSQL did not supply stay None."""
    if isinstance(explain_json, str):
        explain_json = json.loads(explain_json)
    root = explain_json[0] if isinstance(explain_json, list) else explain_json
    if not isinstance(root, dict) or "Plan" not in root:
        raise StudyError("not a PostgreSQL EXPLAIN (FORMAT JSON) result")
    nodes = list(walk_plan(root["Plan"]))
    planned = [n["Workers Planned"] for n in nodes if "Workers Planned" in n]
    launched = [n["Workers Launched"] for n in nodes if "Workers Launched" in n]
    aggregates = []
    for n in nodes:
        if n.get("Node Type") == "Aggregate":
            loops = n.get("Actual Loops")
            actual = n.get("Actual Rows")
            aggregates.append({
                "strategy": n.get("Strategy"),
                "partial_mode": n.get("Partial Mode"),
                "estimated_rows": n.get("Plan Rows"),
                "actual_rows_per_loop": actual,
                "actual_loops": loops,
                "actual_rows_total": actual * loops if actual is not None and loops else None,
            })
    top_agg = aggregates[0] if aggregates else None
    ratio = None
    if top_agg and top_agg["estimated_rows"] is not None and top_agg["actual_rows_total"]:
        ratio = top_agg["estimated_rows"] / top_agg["actual_rows_total"]
    return {
        "node_types": [n.get("Node Type") for n in nodes],
        "scans": [f"{n.get('Node Type')}({n.get('Relation Name')})" for n in nodes
                  if "Scan" in (n.get("Node Type") or "")],
        "workers_planned": max(planned) if planned else None,
        "workers_launched": max(launched) if launched else None,
        "aggregates": aggregates,
        "top_aggregate_estimated_rows": top_agg["estimated_rows"] if top_agg else None,
        "top_aggregate_actual_rows": top_agg["actual_rows_total"] if top_agg else None,
        "top_aggregate_estimate_ratio": ratio,
        "planning_ms": root.get("Planning Time"),
        "execution_ms": root.get("Execution Time"),
        "jit_ms": (root.get("JIT") or {}).get("Timing", {}).get("Total"),
    }


# DuckDB 2.0 draws each operator as a box whose name sits in the top border: "╭─ Perfect Hash Group By ───╮".
_DUCK_BOX = re.compile(r"╭─\s*([^─╮┚┖┒┎]+?)\s*─")
# Older DuckDB versions print operator names in capitals: "PERFECT_HASH_GROUP_BY".
_DUCK_OPERATOR = re.compile(r"\b((?:[A-Z]+_)*(?:SCAN|GROUP_BY|JOIN|PROJECTION|ORDER_BY|TOP_N|FILTER|"
                            r"AGGREGATE|LIMIT|STREAMING_LIMIT))\b")
_DUCK_TOTAL = re.compile(r"Total Time:\s*([0-9.]+)\s*s", re.IGNORECASE)


def summarize_duckdb_plan(text: str) -> dict:
    """Best-effort facts from DuckDB EXPLAIN ANALYZE text: operators from the top of the plan down,
    in order of appearance (each name once), and the total time if printed. The full text is
    always saved next to it."""
    text = text or ""
    found = [name for name in _DUCK_BOX.findall(text) if name != "Summary"] or _DUCK_OPERATOR.findall(text)
    ops: list[str] = []
    for op in found:
        if op not in ops:
            ops.append(op)
    m = _DUCK_TOTAL.search(text)
    return {"operators": ops, "total_time_ms": float(m.group(1)) * 1000 if m else None}


def duckdb_explain_text(rows: list[tuple]) -> str:
    return "\n".join(str(r[-1]) for r in rows)


# ------------------------------------------------------------------ real connections (lazy imports)
def connect_postgres(months: int, variant: dict) -> Executor:
    import psycopg  # only for real runs

    e = os.environ.get
    conn = psycopg.connect(host=e("POSTGRES_HOST", "db"), port=e("POSTGRES_PORT", "5432"),
                           user=e("POSTGRES_USER", "postgres"), password=e("POSTGRES_PASSWORD", "postgres"),
                           dbname=e("POSTGRES_DB", "taxi"), autocommit=True,
                           options="-c default_transaction_read_only=on")
        def postgres_raw_run(sql):
        cursor = conn.execute(sql)
        if cursor.description is None:
            return []
        return cursor.fetchall()

    ex = Executor(
        "postgres",
        months,
        variant["variant"],
        postgres_raw_run,
        conn.close,
    )

    try:
        prepare_postgres(ex, months, variant)
    except Exception:
        conn.close()
        raise

    return ex


def prepare_postgres(ex: Executor, months: int, variant: dict) -> None:
    """Refuse anything but a read-only session, then set the schema and the variant's setting."""
    ro = ex.run("SHOW default_transaction_read_only")[0][0]
    if str(ro).lower() != "on":
        ex.close()
        raise StudyError("PostgreSQL session is not read-only: stopping before any query")
    ex.run(f"SET search_path TO m{int(months)}")
    if variant["setting"] == "max_parallel_workers_per_gather":
        ex.run(f"SET max_parallel_workers_per_gather = {int(variant['value'])}")


def connect_duckdb(months: int, variant: dict, data_dir: Path) -> Executor:
    import duckdb  # only for real runs

    path = data_dir / f"taxi_{months}m.duckdb"
    if not path.exists():
        raise StudyError(f"{path} not found: run the loader first (this tool never creates databases)")
    config = {"threads": int(variant["value"])} if variant["setting"] == "threads" else {}
    con = duckdb.connect(str(path), read_only=True, config=config)
    return Executor("duckdb", months, variant["variant"], lambda s: con.execute(s).fetchall(), con.close)


def engine_settings(ex: Executor) -> dict:
    if ex.engine == "postgres":
        names = ("max_parallel_workers_per_gather", "max_parallel_workers", "max_worker_processes",
                 "work_mem", "shared_buffers", "jit")
        return {n: ex.run(f"SHOW {n}")[0][0] for n in names}
    return {n: ex.run(f"SELECT current_setting('{n}')")[0][0] for n in ("threads", "memory_limit")}


# ------------------------------------------------------------------ preflight (no changes, ever)
def read_manifest(data_dir: Path) -> dict:
    p = data_dir / "clean" / "manifest.json"
    if not p.exists():
        raise StudyError(f"{p} not found: the loader has not built this dataset here (nothing was changed)")
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise StudyError(f"{p} cannot be read: {exc}") from None


def expected_rows(manifest: dict, sizes: list[int]) -> dict[int, int]:
    months = manifest.get("months") or []
    if not months or max(sizes) > len(months):
        raise StudyError(f"manifest has {len(months)} month(s); sizes {sizes} cannot be checked")
    return {n: sum(m["kept"] for m in months[:n]) for n in sizes}


def check_dataset(ex: Executor, fingerprint: str, rows: int) -> None:
    """The database must hold exactly the manifest's dataset; otherwise stop."""
    try:
        meta = dict(ex.run("SELECT key, value FROM load_meta"))
        count = ex.run("SELECT count(*) FROM trips")[0][0]
    except StudyError:
        raise
    except Exception as exc:  # noqa: BLE001 - a missing table is reported, never created
        raise StudyError(f"{ex.engine} {ex.months}m: tables not available ({exc})") from None
    if meta.get("fingerprint") != fingerprint:
        raise StudyError(f"{ex.engine} {ex.months}m was loaded from a different dataset (fingerprint)")
    if int(meta.get("rows", -1)) != rows or int(count) != rows:
        raise StudyError(f"{ex.engine} {ex.months}m: expected {rows} rows, load_meta says "
                         f"{meta.get('rows')}, table has {count}")


# ------------------------------------------------------------------ the two stages
def run_plans(cfg: dict, connect, out: OutputDir, fingerprint: str, rows_of: dict) -> list[list]:
    sql = study_sql(cfg["queries"])
    summary = []
    for n in cfg["sizes"]:
        for v in cfg["variants"]:
            ex = connect(v["engine"], n, v)
            try:
                check_dataset(ex, fingerprint, rows_of[n])
                for q, text in sql.items():
                    if v["engine"] == "postgres":
                        plan = ex.run(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {text}")[0][0]
                        if isinstance(plan, str):
                            plan = json.loads(plan)
                        name = f"plans/pg_{n}m_{q}_{v['variant']}.json"
                        out.write_json(name, plan)
                        s = summarize_pg_plan(plan)
                        summary.append(["postgres", n, rows_of[n], q, v["variant"], v["setting"] or "", v["value"],
                                        s["execution_ms"], s["planning_ms"], s["jit_ms"], ">".join(s["node_types"]),
                                        s["workers_planned"], s["workers_launched"],
                                        s["top_aggregate_estimated_rows"], s["top_aggregate_actual_rows"],
                                        s["top_aggregate_estimate_ratio"], name])
                    else:
                        text_plan = duckdb_explain_text(ex.run(f"EXPLAIN ANALYZE {text}"))
                        name = f"plans/duckdb_{n}m_{q}_{v['variant']}.txt"
                        out.write_text(name, text_plan + "\n")
                        s = summarize_duckdb_plan(text_plan)
                        summary.append(["duckdb", n, rows_of[n], q, v["variant"], v["setting"] or "", v["value"],
                                        s["total_time_ms"], None, None, ">".join(s["operators"]),
                                        None, None, None, None, None, name])
                    print(f"  plan {v['engine']:8s} {n}m {q} {v['variant']}", flush=True)
            finally:
                ex.close()
    return summary


PLAN_HEADER = ["engine", "months", "rows", "query", "variant", "setting", "value", "execution_ms", "planning_ms",
               "jit_ms", "node_types", "workers_planned", "workers_launched", "agg_estimated_rows",
               "agg_actual_rows", "agg_estimate_ratio", "plan_file"]
TIMING_HEADER = ["session", "seed", "engine_position", "engine", "variant", "setting", "value", "months", "rows",
                 "query", "query_position", "phase", "run", "seconds"]


def run_timings(cfg: dict, connect, fingerprint: str, rows_of: dict):
    """Returns (timing rows, equivalence rows). First results are compared per session and size
    against DuckDB's default variant with sql/equivalence.compare_results."""
    sql = study_sql(cfg["queries"])
    timings, equivalence = [], []
    phases = [("first", 1)] + [("warmup", i) for i in range(1, cfg["warmups"] + 1)] \
        + [("warm", i) for i in range(1, cfg["runs"] + 1)]
    for s in session_schedule(cfg["sessions"], cfg["seed"], cfg["queries"]):
        firsts = {}
        for pos, engine in enumerate(s["engine_order"], start=1):
            for n in cfg["sizes"]:
                for v in (v for v in cfg["variants"] if v["engine"] == engine):
                    ex = connect(engine, n, v)
                    try:
                        check_dataset(ex, fingerprint, rows_of[n])
                        for qpos, q in enumerate(s["query_order"], start=1):
                            for phase, run in phases:
                                t0 = time.perf_counter()
                                result = ex.run(sql[q])
                                seconds = time.perf_counter() - t0
                                if phase == "first":
                                    firsts[(engine, v["variant"], n, q)] = result
                                timings.append([s["session"], s["seed"], pos, engine, v["variant"], v["setting"] or "",
                                                v["value"], n, rows_of[n], q, qpos, phase, run, f"{seconds:.6f}"])
                        print(f"  session {s['session']} {engine:8s} {n}m {v['variant']} done", flush=True)
                    finally:
                        ex.close()
        for (engine, variant, n, q), result in sorted(firsts.items()):
            ref = firsts.get(("duckdb", DEFAULT, n, q))
            if ref is None or (engine, variant) == ("duckdb", DEFAULT):
                continue
            ok, msg = compare_results(result, ref)
            equivalence.append([s["session"], n, q, f"{engine}:{variant}", "duckdb:default", len(ref),
                                "PASS" if ok else "FAIL", msg])
    return timings, equivalence


def summarize_timings(timings: list[list]) -> tuple[list[list], list[list]]:
    """(summary over all sessions per engine/variant/size/query/phase,
        per-session warm medians and the spread between sessions)."""
    groups: dict = {}
    for r in timings:
        groups.setdefault((r[3], r[4], r[7], r[9], r[11]), []).append(float(r[13]))
    summary = []
    for (engine, variant, n, q, phase), vals in sorted(groups.items()):
        d = describe(vals)
        summary.append([engine, variant, n, q, phase, d["n"], f"{d['mean']:.6f}", f"{d['median']:.6f}",
                        "" if d["stdev"] is None else f"{d['stdev']:.6f}", f"{d['min']:.6f}", f"{d['max']:.6f}"])
    per_session: dict = {}
    for r in timings:
        if r[11] == "warm":
            per_session.setdefault((r[3], r[4], r[7], r[9]), {}).setdefault(r[0], []).append(float(r[13]))
    sessions = []
    for (engine, variant, n, q), by_s in sorted(per_session.items()):
        medians = [describe(v)["median"] for _, v in sorted(by_s.items())]
        lo, hi = min(medians), max(medians)
        spread = (hi - lo) / lo * 100 if lo > 0 else None
        sessions.append([engine, variant, n, q, len(medians), " ".join(f"{m:.6f}" for m in medians),
                         f"{describe(medians)['median']:.6f}", "" if spread is None else f"{spread:.1f}"])
    return summary, sessions


SUMMARY_HEADER = ["engine", "variant", "months", "query", "phase", "n", "mean_s", "median_s", "stdev_s",
                  "min_s", "max_s"]
SESSION_HEADER = ["engine", "variant", "months", "query", "sessions", "warm_median_per_session_s",
                  "median_of_session_medians_s", "between_session_range_pct"]


# ------------------------------------------------------------------ command line
def build_parser() -> argparse.ArgumentParser:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    p = argparse.ArgumentParser(
        prog="rigor_study.py",
        description="Read-only study of Q1-Q3 query plans, parallelism and repeatability "
                    "(supplement to benchmark.py; never changes it or its results).",
        epilog="Without --confirm-real-run nothing connects to a database. Real run, inside the benchmark "
               "image with the db service already running:  docker compose run --rm --no-deps benchmark "
               "python rigor_study.py --confirm-real-run --stage plans")
    p.add_argument("--dry-run", action="store_true", help="print the plan of the study and exit (no database access)")
    p.add_argument("--confirm-real-run", action="store_true", help="required to connect to the real databases")
    p.add_argument("--stage", choices=("plans", "timings", "all"), default="plans",
                   help="plans = EXPLAIN ANALYZE only (default); timings = repeated sessions; all = both")
    p.add_argument("--output-dir", type=Path, default=None,
                   help=f"NEW directory for the outputs (default: RESULTS_DIR/marin_rigor_{today}); must not exist")
    p.add_argument("--sizes", default=os.environ.get("SIZES", "1,2,4"), help="test sizes in months (default: SIZES or 1,2,4)")
    p.add_argument("--queries", default="Q1,Q2,Q3", help="subset of Q1,Q2,Q3 (default: all three)")
    p.add_argument("--duckdb-threads", default="1,3", help="DuckDB thread variants besides the default (default: 1,3)")
    p.add_argument("--pg-workers", default="0,2",
                   help="PostgreSQL max_parallel_workers_per_gather variants besides the default (default: 0,2)")
    p.add_argument("--sessions", type=int, default=3, help="independent timing sessions (default: 3)")
    p.add_argument("--runs", type=int, default=5, help="warm runs per query per session (default: 5)")
    p.add_argument("--warmups", type=int, default=2, help="warm-up runs per query per session (default: 2)")
    p.add_argument("--seed", type=int, default=int(os.environ.get("BENCH_SEED", "42")) + 100,
                   help="base seed; session i uses seed+i (default: BENCH_SEED+100, so not the main benchmark's order)")
    return p


def make_config(args) -> dict:
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    results_dir = Path(os.environ.get("RESULTS_DIR", "/results"))
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    if args.runs < 1 or args.warmups < 0:
        raise StudyError("--runs must be >= 1 and --warmups >= 0")
    queries = [q.strip().upper() for q in args.queries.split(",") if q.strip()]
    study_sql(queries)  # validates the subset
    return {
        "stage": args.stage,
        "data_dir": data_dir,
        "results_dir": results_dir,
        "output_dir": args.output_dir or results_dir / f"marin_rigor_{today}",
        "sizes": parse_int_list(args.sizes, "--sizes"),
        "queries": queries,
        "variants": variants(parse_int_list(args.duckdb_threads, "--duckdb-threads"),
                             parse_int_list(args.pg_workers, "--pg-workers")),
        "sessions": args.sessions,
        "runs": args.runs,
        "warmups": args.warmups,
        "seed": args.seed,
    }


def describe_plan(cfg: dict) -> str:
    sql = study_sql(cfg["queries"])
    lines = ["DRY RUN: no database is contacted, nothing is written.", "",
             f"stage            {cfg['stage']}",
             f"output directory {cfg['output_dir']} (must not exist yet)",
             f"sizes (months)   {cfg['sizes']}",
             f"queries          {', '.join(cfg['queries'])} (canonical SQL from sql/queries.py)",
             "variants:"]
    lines += [f"  {v['engine']:8s} {v['variant']}" + (f"  ({v['setting']} = {v['value']})" if v["setting"] else "")
              for v in cfg["variants"]]
    if cfg["stage"] in ("plans", "all"):
        n_plans = len(cfg["sizes"]) * len(cfg["variants"]) * len(cfg["queries"])
        lines.append(f"plans            {n_plans} EXPLAIN ANALYZE executions (each runs the SELECT once)")
    if cfg["stage"] in ("timings", "all"):
        per = 1 + cfg["warmups"] + cfg["runs"]
        n_exec = cfg["sessions"] * len(cfg["sizes"]) * len(cfg["variants"]) * len(cfg["queries"]) * per
        lines.append(f"timings          {cfg['sessions']} sessions x {len(cfg['sizes'])} sizes x "
                     f"{len(cfg['variants'])} variants x {len(cfg['queries'])} queries x {per} executions = {n_exec}")
        lines.append("sessions:")
        lines += [f"  session {s['session']}: seed {s['seed']}, engines {' then '.join(s['engine_order'])}, "
                  f"queries {' '.join(s['query_order'])}"
                  for s in session_schedule(cfg["sessions"], cfg["seed"], cfg["queries"])]
    lines.append("")
    lines += [f"{q}: {' '.join(text.split())[:110]}..." for q, text in sql.items()]
    lines += ["", "Caveats:"] + [f"  - {c}" for c in CAVEATS]
    lines += ["", "To run for real (only after approval): add --confirm-real-run."]
    return "\n".join(lines)


def environment(cfg: dict, connect, sample_size: int) -> dict:
    env = {"python": platform.python_version(), "platform": platform.platform(),
           "cpu_count_seen": os.cpu_count(), "settings_by_variant": {}}
    for v in cfg["variants"]:
        ex = connect(v["engine"], sample_size, v)
        try:
            env["settings_by_variant"][f"{v['engine']}:{v['variant']}"] = engine_settings(ex)
            if v["engine"] == "postgres" and v["variant"] == DEFAULT:
                env["postgres_version"] = ex.run("SELECT version()")[0][0]
            if v["engine"] == "duckdb" and v["variant"] == DEFAULT:
                env["duckdb_version"] = ex.run("SELECT version()")[0][0]
        finally:
            ex.close()
    return env


def real_connect_factory(cfg: dict):
    def connect(engine: str, months: int, variant: dict) -> Executor:
        if engine == "postgres":
            return connect_postgres(months, variant)
        return connect_duckdb(months, variant, cfg["data_dir"])
    return connect


def main(argv=None, connect_factory=real_connect_factory) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = make_config(args)
        if args.dry_run or not args.confirm_real_run:
            print(describe_plan(cfg))
            if not args.dry_run:
                print("\n--confirm-real-run was not given: stopped before any database access.")
            return 0

        out = OutputDir(cfg["output_dir"], cfg["results_dir"], cfg["data_dir"])  # refuses existing paths
        manifest = read_manifest(cfg["data_dir"])
        fingerprint = manifest.get("fingerprint")
        rows_of = expected_rows(manifest, cfg["sizes"])
        connect = connect_factory(cfg)
        for n in cfg["sizes"]:  # preflight: every database must hold the manifest's dataset
            for engine in ENGINES:
                ex = connect(engine, n, {"engine": engine, "variant": DEFAULT, "setting": None, "value": None})
                try:
                    check_dataset(ex, fingerprint, rows_of[n])
                finally:
                    ex.close()
        out.create()
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        protocol = {"tool": "benchmark/rigor_study.py", "started_utc": started, "stage": cfg["stage"],
                    "sizes": cfg["sizes"], "rows": rows_of, "fingerprint": fingerprint,
                    "queries": cfg["queries"], "variants": cfg["variants"], "sessions": cfg["sessions"],
                    "runs": cfg["runs"], "warmups": cfg["warmups"], "seed": cfg["seed"],
                    "schedule": session_schedule(cfg["sessions"], cfg["seed"], cfg["queries"]),
                    "caveats": CAVEATS, "environment": environment(cfg, connect, min(cfg["sizes"]))}
        status = "complete"
        if cfg["stage"] in ("plans", "all"):
            print("=== plans ===", flush=True)
            out.write_csv("plan_summary.csv", PLAN_HEADER, run_plans(cfg, connect, out, fingerprint, rows_of))
        if cfg["stage"] in ("timings", "all"):
            print("=== timings ===", flush=True)
            timings, equivalence = run_timings(cfg, connect, fingerprint, rows_of)
            out.write_csv("rigor_equivalence.csv", ["session", "months", "query", "engine_variant", "reference",
                                                    "rows_compared", "status", "detail"], equivalence)
            if any(r[6] == "FAIL" for r in equivalence):
                out.write_csv("rigor_timings_REJECTED.csv", TIMING_HEADER, timings)
                status = "failed: a variant returned a different result; timings rejected"
            else:
                summary, sessions = summarize_timings(timings)
                out.write_csv("rigor_timings.csv", TIMING_HEADER, timings)
                out.write_csv("rigor_timing_summary.csv", SUMMARY_HEADER, summary)
                out.write_csv("rigor_session_medians.csv", SESSION_HEADER, sessions)
        protocol["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        protocol["status"] = status
        out.write_json("protocol.json", protocol)
        print(f"\nStudy {status}. Outputs in {out.path}")
        return 0 if status == "complete" else 1
    except StudyError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
