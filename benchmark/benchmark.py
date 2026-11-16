"""Benchmark: PostgreSQL vs DuckDB on the cleaned night-trip dataset.   See docs/METHODOLOGY.md

PROTOCOL (per engine and test size, on a fresh connection; the query order is a seeded shuffle,
identical for both engines, and recorded in the output):
  for every query:   1 x "first"   first execution of that query on this connection
                     W x "warmup"  (BENCH_WARMUPS, default 2) executions, timed and saved
                     R x "warm"    (BENCH_RUNS, default 5) executions, timed and saved
"first" is NOT a true cold start: the OS page cache is not flushed and the database files were
just loaded, so it only means "first execution after connecting, plus whatever the query order
had already warmed". Report it separately from "warm"; never mix them.

ACCEPTANCE: timings are written only if ALL of these pass, otherwise nothing is accepted
(the raw numbers go to rejected_timings.csv and the exit code is 1):
  - the database holds the same clean dataset as results of preprocess (fingerprint, row counts)
  - the FIRST result of every query is identical on PostgreSQL and DuckDB
  - PostgreSQL EXPLAIN ANALYZE shows an index scan for Q5 after the index (reported, not hidden)
  - row-level consistency of both engines (count, ids, sums, date bounds, night hours)

DEFINITIONS
  query time     wall-clock seconds of execute + fetchall in this Python process
                 (PostgreSQL: includes the client/server round trip; DuckDB: in-process)
  Q4             200 sequential lookups: an APPLICATION-LEVEL LATENCY workload, not pure engine time
  storage        PostgreSQL pg_total_relation_size(trips)+(zones); DuckDB file size after CHECKPOINT
  memory         NOT measured here: scripts/sample_docker_stats.py + summarize_memory.py
"""
from __future__ import annotations

import csv
import json
import os
import platform
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sql"))
import night  # noqa: E402
import queries as Q  # noqa: E402
from equivalence import compare_results  # noqa: E402
from stats import describe, percentile  # noqa: E402

BENCH_HEADER = "engine,months,rows,query,phase,run,query_order,seconds"


# ------------------------------------------------------------------ settings
def settings() -> dict:
    e = os.environ.get
    s = {
        "data_dir": Path(e("DATA_DIR", "/data")),
        "results_dir": Path(e("RESULTS_DIR", "/results")),
        "runs": int(e("BENCH_RUNS", "5")),
        "warmups": int(e("BENCH_WARMUPS", "2")),
        "seed": int(e("BENCH_SEED", "42")),
        "order": e("BENCH_ORDER", "shuffle"),
        "sizes": [int(x) for x in e("SIZES", "1,2,4").split(",") if x.strip()],
        "start_month": e("START_MONTH", "2026-04"),
    }
    if s["runs"] < 1 or s["warmups"] < 0:
        raise ValueError("BENCH_RUNS must be >= 1 and BENCH_WARMUPS >= 0")
    if s["order"] not in ("shuffle", "fixed"):
        raise ValueError("BENCH_ORDER must be shuffle or fixed")
    return s


def month_list(n: int, start_month: str) -> list[str]:
    year, month = (int(x) for x in start_month.split("-"))
    out = []
    for _ in range(n):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out


def query_order(size: int, seed: int, mode: str) -> list[str]:
    names = list(Q.ORDER)
    if mode == "shuffle":
        random.Random(seed * 1000 + size).shuffle(names)
    return names


# ------------------------------------------------------------------ engines
def pg_connect(schema: str) -> psycopg.Connection:
    e = os.environ.get
    conn = psycopg.connect(host=e("POSTGRES_HOST", "db"), port=e("POSTGRES_PORT", "5432"),
                           user=e("POSTGRES_USER", "postgres"), password=e("POSTGRES_PASSWORD", "postgres"),
                           dbname=e("POSTGRES_DB", "taxi"), autocommit=True)
    conn.execute(f"SET search_path TO {schema}")
    return conn


class Engine:
    """Uniform interface: run(sql) -> list of rows."""

    def __init__(self, name: str, run):
        self.name, self.run = name, run


def execute(engine: Engine, name: str, sql: dict, ids: list[int]):
    """One execution of a query. Returns (seconds, result rows, per-lookup seconds or None)."""
    if name == "Q4":
        rows, lat = [], []
        t0 = time.perf_counter()
        for trip_id in ids:
            a = time.perf_counter()
            r = engine.run(Q.point_sql(trip_id))
            lat.append(time.perf_counter() - a)
            if len(r) != 1:
                raise RuntimeError(f"{engine.name}: lookup of trip_id {trip_id} returned {len(r)} rows")
            rows.append(r[0])
        return time.perf_counter() - t0, rows, lat
    t0 = time.perf_counter()
    rows = engine.run(sql[name])
    return time.perf_counter() - t0, rows, None


def protocol(engine: Engine, label: str, months: int, rows_n: int, names: list[str], sql: dict,
             ids: list[int], cfg: dict, order_index: dict, out: list, firsts: dict, lookups: list) -> None:
    """first + warmups + warm runs for every query in `names` (in this order)."""
    for name in names:
        plan = [("first", 1)] + [("warmup", i) for i in range(1, cfg["warmups"] + 1)] \
            + [("warm", i) for i in range(1, cfg["runs"] + 1)]
        for phase, run in plan:
            seconds, rows, lat = execute(engine, name, sql, ids)
            if phase == "first":
                firsts[(label, months, name)] = rows
            out.append([label, months, rows_n, name, phase, run, order_index[name], f"{seconds:.6f}"])
            if lat is not None:
                lookups.extend([label, months, phase, run, f"{x * 1000:.4f}"] for x in lat)
            print(f"  {label:17s} {months}m {name} {phase:6s} {run}: {seconds:.4f} s", flush=True)


# ------------------------------------------------------------------ PostgreSQL plan checks
def plan_nodes(plan) -> list[str]:
    """Every 'Node Type' of an EXPLAIN (FORMAT JSON) result."""
    if isinstance(plan, str):
        plan = json.loads(plan)
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if "Node Type" in node:
                found.append(node["Node Type"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(plan)
    return found


def index_used(nodes: list[str]) -> bool:
    return any("Index" in n for n in nodes)


def explain_q5(conn, sql_q5: str, months: int, state: str, results_dir: Path) -> list:
    raw = conn.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql_q5}").fetchone()[0]
    if isinstance(raw, str):
        raw = json.loads(raw)
    (results_dir / "explain").mkdir(parents=True, exist_ok=True)
    (results_dir / "explain" / f"q5_{months}m_{state}.json").write_text(json.dumps(raw, indent=2))
    top = raw[0]
    nodes = plan_nodes(raw)
    print(f"  EXPLAIN ANALYZE Q5 {months}m {state}: {', '.join(nodes)} "
          f"(index used: {index_used(nodes)}, {top['Execution Time']:.1f} ms)", flush=True)
    return [months, state, ";".join(nodes), index_used(nodes), f"{top['Planning Time']:.3f}",
            f"{top['Execution Time']:.3f}"]


# ------------------------------------------------------------------ consistency checks
CHECK_SQL = """SELECT count(*), min(trip_id), max(trip_id), sum(fare_amount), sum(total_amount),
       min(pickup_ts), max(pickup_ts),
       count(*) FILTER (WHERE extract(hour FROM pickup_ts) NOT IN ({hours})),
       count(DISTINCT trip_id)
FROM trips"""


def consistency(pg_rows: tuple, dk_rows: tuple, meta_rows: int, months: int, cfg: dict) -> list[list]:
    """Row-level comparison of the two engines and of both with the preprocessing manifest."""
    names = ["row count", "min trip_id", "max trip_id", "sum fare_amount", "sum total_amount",
             "min pickup_ts", "max pickup_ts", "rows outside night hours", "distinct trip_id"]
    out = []
    from equivalence import values_equal
    for n, a, b in zip(names, pg_rows, dk_rows):
        out.append([months, n, a, b, "PASS" if values_equal(a, b, rel=1e-9, abs_=1e-6) else "FAIL"])
    out.append([months, "rows = manifest rows", pg_rows[0], meta_rows,
                "PASS" if pg_rows[0] == dk_rows[0] == meta_rows else "FAIL"])
    out.append([months, "trip_id = 1..rows", f"{pg_rows[1]}..{pg_rows[2]}", f"{dk_rows[1]}..{dk_rows[2]}",
                "PASS" if (pg_rows[1], pg_rows[2], pg_rows[8]) == (1, meta_rows, meta_rows)
                and (dk_rows[1], dk_rows[2], dk_rows[8]) == (1, meta_rows, meta_rows) else "FAIL"])
    first_day = datetime.strptime(cfg["start_month"] + "-01", "%Y-%m-%d")
    last = month_list(months, cfg["start_month"])[-1]
    y, m = (int(x) for x in last.split("-"))
    end = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1)
    inside = first_day <= pg_rows[5] and pg_rows[6] < end and first_day <= dk_rows[5] and dk_rows[6] < end
    out.append([months, f"pickup within [{first_day:%Y-%m-%d}, {end:%Y-%m-%d})",
                f"{pg_rows[5]}..{pg_rows[6]}", f"{dk_rows[5]}..{dk_rows[6]}", "PASS" if inside else "FAIL"])
    return out


# ------------------------------------------------------------------ output helpers
def write_csv(path: Path, header: str, rows: list[list]) -> None:
    """Proper CSV (quotes fields that contain commas, e.g. check names and error details)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header.split(","))
        w.writerows(rows)
    os.replace(tmp, path)


def summarize(rows: list[list]) -> list[list]:
    """One line per engine/size/query/phase: n, mean, median, sample stdev, min, max (seconds)."""
    groups: dict = {}
    for engine, months, nrows, query, phase, _run, _order, sec in rows:
        groups.setdefault((engine, months, nrows, query, phase), []).append(float(sec))
    out = []
    for (engine, months, nrows, query, phase), vals in sorted(groups.items()):
        d = describe(vals)
        out.append([engine, months, nrows, query, Q.WORKLOAD[query], phase, d["n"], f"{d['mean']:.6f}",
                    f"{d['median']:.6f}", "" if d["stdev"] is None else f"{d['stdev']:.6f}",
                    f"{d['min']:.6f}", f"{d['max']:.6f}"])
    return out


SUMMARY_HEADER = "engine,months,rows,query,workload,phase,n,mean_s,median_s,stdev_s,min_s,max_s"


def lookup_summary(lookups: list[list]) -> list[list]:
    groups: dict = {}
    for engine, months, phase, _run, ms in lookups:
        groups.setdefault((engine, months, phase), []).append(float(ms))
    return [[e, m, p, len(v), f"{sum(v) / len(v):.4f}", f"{percentile(v, 50):.4f}",
             f"{percentile(v, 95):.4f}", f"{percentile(v, 99):.4f}", f"{max(v):.4f}"]
            for (e, m, p), v in sorted(groups.items())]


def environment(cfg: dict, conn) -> list[str]:
    lines = [f"python {platform.python_version()}", f"duckdb {duckdb.__version__}",
             f"psycopg {psycopg.__version__}", f"cpu_count_seen_by_container {os.cpu_count()}"]
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemTotal"):
                lines.append("container_" + " ".join(line.split()))
    except OSError:
        pass
    lines.append(conn.execute("SELECT version()").fetchone()[0])
    for s in ("shared_buffers", "work_mem", "effective_cache_size", "max_parallel_workers_per_gather"):
        lines.append(f"postgres {s} = {conn.execute(f'SHOW {s}').fetchone()[0]}")
    d = duckdb.connect()
    for setting in ("threads", "memory_limit"):
        lines.append(f"duckdb {setting} = {d.execute(f'SELECT current_setting({chr(39)}{setting}{chr(39)})').fetchone()[0]}")
    d.close()
    lines.append(f"runs {cfg['runs']}, warmups {cfg['warmups']}, seed {cfg['seed']}, order {cfg['order']}, "
                 f"sizes {cfg['sizes']}")
    start, end, _ = Q.night_settings()
    lines.append(f"night window {night.label(start, end)}; Q5 range {Q.q5_range()}")
    lines += [f"{k} [{Q.WORKLOAD[k]}]: {v}" for k, v in Q.DESCRIPTIONS.items()]
    return lines


# ------------------------------------------------------------------ main
def main() -> int:
    cfg = settings()
    res, data = cfg["results_dir"], cfg["data_dir"]
    res.mkdir(parents=True, exist_ok=True)
    for name in ("benchmark.csv", "benchmark_summary.csv", "lookup_latency.csv", "disk_sizes.csv",
                 "index_build.csv", "explain_q5.csv", "phases.csv", "equivalence.csv",
                 "db_consistency.csv", "datasets.csv", "rejected_timings.csv", "environment.txt",
                 "run_status.json"):
        (res / name).unlink(missing_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def status(state: str, **extra) -> None:
        (res / "run_status.json").write_text(json.dumps(
            {"state": state, "started_utc": started, "runs": cfg["runs"], "warmups": cfg["warmups"],
             "seed": cfg["seed"], "order": cfg["order"], **extra}, indent=2))

    status("running")
    manifest_file = data / "clean" / "manifest.json"
    if not manifest_file.exists():
        print("ERROR: clean/manifest.json not found. Did the loader finish?")
        status("failed", reason="manifest missing")
        return 1
    manifest = json.loads(manifest_file.read_text())
    fp = manifest["fingerprint"]
    rows_of = {n: sum(m["kept"] for m in manifest["months"][:n]) for n in cfg["sizes"]}
    smallest = rows_of[min(cfg["sizes"])]
    ids = Q.point_ids(smallest)
    sql = Q.queries()

    # --- the databases must hold the dataset described by the manifest (cheap metadata read)
    try:
        for n in cfg["sizes"]:
            with pg_connect(f"m{n}") as c:
                meta = dict(c.execute("SELECT key, value FROM load_meta").fetchall())
            d = duckdb.connect(str(data / f"taxi_{n}m.duckdb"), read_only=True)
            dmeta = dict(d.execute("SELECT key, value FROM load_meta").fetchall())
            d.close()
            for label, m in (("postgres", meta), ("duckdb", dmeta)):
                if m.get("fingerprint") != fp or int(m.get("rows", -1)) != rows_of[n]:
                    raise RuntimeError(f"{label} size {n}m was loaded from a different dataset "
                                       f"(fingerprint/rows differ from manifest). Run the loader again.")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        status("failed", reason=str(exc))
        return 1

    with pg_connect("public") as c:
        env_lines = environment(cfg, c)
    (res / "environment.txt").write_text("\n".join(env_lines) + "\n")

    disk: list[list] = []
    for n in cfg["sizes"]:
        for kind in ("raw", "clean"):
            total = sum((data / kind / f"yellow_tripdata_{m}.parquet").stat().st_size
                        for m in month_list(n, cfg["start_month"]))
            disk.append([f"parquet_{kind}", n, total])

    timings: list[list] = []
    firsts: dict = {}
    lookups: list[list] = []
    explains: list[list] = []
    index_builds: list[list] = []
    phases: list[list] = []
    consistency_rows: list[list] = []
    pg_check, dk_check = {}, {}
    start_h, end_h, _ = Q.night_settings()
    hours = ",".join(str(h) for h in night.night_hours(start_h, end_h))

    print("=== PostgreSQL ===", flush=True)
    for n in cfg["sizes"]:
        order = query_order(n, cfg["seed"], cfg["order"])
        oi = {q: i + 1 for i, q in enumerate(order)}
        p0 = time.time()
        conn = pg_connect(f"m{n}")
        conn.execute("DROP INDEX IF EXISTS idx_trips_pickup_ts")
        eng = Engine("postgres", lambda s, c=conn: c.execute(s).fetchall())
        protocol(eng, "postgres", n, rows_of[n], order, sql, ids, cfg, oi, timings, firsts, lookups)
        size = conn.execute("SELECT pg_total_relation_size('trips') + pg_total_relation_size('zones')").fetchone()[0]
        disk += [["postgres", n, size],
                 ["postgres_heap", n, conn.execute("SELECT pg_relation_size('trips')").fetchone()[0]],
                 ["postgres_indexes", n, conn.execute("SELECT pg_indexes_size('trips')").fetchone()[0]]]
        pg_check[n] = conn.execute(CHECK_SQL.format(hours=hours)).fetchone()

        # Index experiment on Q5 (selects one night). The plan is checked with EXPLAIN ANALYZE.
        explains.append(explain_q5(conn, sql["Q5"], n, "no_index", res))
        t0 = time.perf_counter()
        conn.execute("CREATE INDEX idx_trips_pickup_ts ON trips (pickup_ts)")
        conn.execute("ANALYZE trips")
        index_builds.append([n, f"{time.perf_counter() - t0:.3f}"])
        explains.append(explain_q5(conn, sql["Q5"], n, "with_index", res))
        eng_idx = Engine("postgres_indexed", eng.run)
        protocol(eng_idx, "postgres_indexed", n, rows_of[n], ["Q5"], sql, ids, cfg, oi, timings, firsts, lookups)
        disk.append(["postgres_indexed", n, conn.execute(
            "SELECT pg_total_relation_size('trips') + pg_total_relation_size('zones')").fetchone()[0]])
        conn.execute("DROP INDEX IF EXISTS idx_trips_pickup_ts")
        conn.close()
        phases.append(["postgres", n, f"{p0:.2f}", f"{time.time():.2f}"])

    print("=== DuckDB ===", flush=True)
    for n in cfg["sizes"]:
        order = query_order(n, cfg["seed"], cfg["order"])
        oi = {q: i + 1 for i, q in enumerate(order)}
        p0 = time.time()
        path = data / f"taxi_{n}m.duckdb"
        con = duckdb.connect(str(path), read_only=True)
        eng = Engine("duckdb", lambda s, c=con: c.execute(s).fetchall())
        protocol(eng, "duckdb", n, rows_of[n], order, sql, ids, cfg, oi, timings, firsts, lookups)
        disk.append(["duckdb", n, path.stat().st_size])
        dk_check[n] = con.execute(CHECK_SQL.format(hours=hours)).fetchone()
        con.close()
        phases.append(["duckdb", n, f"{p0:.2f}", f"{time.time():.2f}"])

    # --- acceptance
    equivalence: list[list] = []
    for n in cfg["sizes"]:
        for q in Q.ORDER:
            ref = firsts[("duckdb", n, q)]
            for label in ("postgres", "postgres_indexed"):
                if (label, n, q) not in firsts:
                    continue
                ok, msg = compare_results(firsts[(label, n, q)], ref)
                if ok and q == "Q5" and ref[0][0] == 0:
                    ok, msg = False, "Q5 selected 0 trips: the test night is empty"
                equivalence.append([n, q, label, "duckdb", len(ref), "PASS" if ok else "FAIL", msg])
        consistency_rows += consistency(pg_check[n], dk_check[n], rows_of[n], n, cfg)
    write_csv(res / "equivalence.csv", "months,query,engine_a,engine_b,rows_compared,status,detail", equivalence)
    write_csv(res / "db_consistency.csv", "months,check,postgres,duckdb,status", consistency_rows)

    failed = [r for r in equivalence if r[5] == "FAIL"] + [r for r in consistency_rows if r[4] == "FAIL"]
    if failed:
        write_csv(res / "rejected_timings.csv", BENCH_HEADER, timings)
        print(f"\nERROR: {len(failed)} equivalence/consistency check(s) failed, timings NOT accepted:")
        for r in failed:
            print("  ", r)
        status("failed", reason="equivalence or consistency check failed", failed=len(failed))
        return 1

    write_csv(res / "benchmark.csv", BENCH_HEADER, timings)
    write_csv(res / "benchmark_summary.csv", SUMMARY_HEADER, summarize(timings))
    write_csv(res / "lookup_latency.csv", "engine,months,phase,lookups,mean_ms,median_ms,p95_ms,p99_ms,max_ms",
              lookup_summary(lookups))
    write_csv(res / "disk_sizes.csv", "what,months,bytes", disk)
    write_csv(res / "index_build.csv", "months,seconds", index_builds)
    write_csv(res / "explain_q5.csv", "months,state,plan_nodes,index_used,planning_ms,execution_ms", explains)
    write_csv(res / "phases.csv", "engine,months,start,end", phases)
    write_csv(res / "datasets.csv", "months,rows,first_trip_id,last_trip_id,pct_of_largest_rows,fingerprint",
              [[n, rows_of[n], 1, rows_of[n], f"{100 * rows_of[n] / max(rows_of.values()):.2f}", fp]
               for n in cfg["sizes"]])
    status("complete", finished_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
           fingerprint=fp, equivalence_checks=len(equivalence), consistency_checks=len(consistency_rows))
    print("\nBenchmark finished: all equivalence and consistency checks passed. Refresh the dashboard.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
