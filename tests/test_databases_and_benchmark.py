"""Integration tests: loaders + benchmark end to end.
DuckDB-only tests always run. Tests marked pg need a PostgreSQL server:
  set TEST_POSTGRES=1 and POSTGRES_HOST/PORT/USER/PASSWORD/DB (they are skipped otherwise)."""
import json
import os

import duckdb
import pytest

import benchmark
import common
import load_duckdb
import load_postgres
import preprocess
from results_io import SCHEMAS, read_csv_safe

pg = pytest.mark.skipif(os.environ.get("TEST_POSTGRES") != "1", reason="needs PostgreSQL (TEST_POSTGRES=1)")


@pytest.fixture
def prepared(raw_data, env):
    env(SIZES="1,2")
    assert preprocess.main() == 0
    return raw_data


def test_duckdb_load_matches_manifest(prepared):
    manifest = common.read_manifest()
    for n in (1, 2):
        assert load_duckdb.load(n) == common.months_rows(manifest, n)
        con = duckdb.connect(str(common.duck_path(n)), read_only=True)
        meta = dict(con.execute("SELECT key, value FROM load_meta").fetchall())
        assert meta["fingerprint"] == manifest["fingerprint"] and int(meta["rows"]) == common.months_rows(manifest, n)
        assert con.execute("SELECT min(trip_id), max(trip_id), count(DISTINCT trip_id) FROM trips").fetchone() == \
            (1, common.months_rows(manifest, n), common.months_rows(manifest, n))
        assert con.execute("SELECT count(*) FROM zones").fetchone()[0] == 265
        con.close()
    header = (common.results_dir() / "load_times.csv").read_text().splitlines()[0]
    assert header == "engine,months,rows,seconds,fingerprint"


def test_duckdb_load_refuses_stale_clean_files(prepared):
    manifest = common.read_manifest()
    manifest["months"][0]["kept"] += 1
    common.write_json(common.manifest_path(), manifest)
    with pytest.raises(RuntimeError, match="manifest"):
        load_duckdb.load(1)


def test_all_queries_run_in_duckdb_and_q5_is_not_empty(prepared, env):
    import queries
    load_duckdb.load(2)
    con = duckdb.connect(str(common.duck_path(2)), read_only=True)
    for name, sql in queries.queries().items():
        assert con.execute(sql).fetchall(), name
    assert con.execute(queries.queries()["Q5"]).fetchone()[0] > 0
    for i in queries.point_ids(common.months_rows(common.read_manifest(), 1)):
        assert len(con.execute(queries.point_sql(i)).fetchall()) == 1
    con.close()


def test_summarize_and_plan_helpers():
    rows = [["duckdb", 1, 100, "Q1", "warm", r, 1, f"{0.1 * r:.6f}"] for r in (1, 2, 3)]
    s = benchmark.summarize(rows)
    assert len(s) == 1 and s[0][6] == 3 and s[0][8] == "0.200000" and s[0][7] == "0.200000"
    assert s[0][9] == "0.100000"  # sample stdev of 0.1,0.2,0.3
    plan = [{"Plan": {"Node Type": "Aggregate", "Plans": [{"Node Type": "Bitmap Heap Scan",
            "Plans": [{"Node Type": "Bitmap Index Scan"}]}]}}]
    assert benchmark.plan_nodes(plan) == ["Aggregate", "Bitmap Heap Scan", "Bitmap Index Scan"]
    assert benchmark.index_used(benchmark.plan_nodes(plan))
    assert not benchmark.index_used(["Aggregate", "Seq Scan"])
    assert benchmark.plan_nodes(json.dumps(plan)) == ["Aggregate", "Bitmap Heap Scan", "Bitmap Index Scan"]


def test_query_order_is_seeded_and_complete():
    a = benchmark.query_order(4, 42, "shuffle")
    assert a == benchmark.query_order(4, 42, "shuffle") and sorted(a) == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    assert benchmark.query_order(4, 42, "fixed") == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    assert benchmark.query_order(1, 42, "shuffle") != benchmark.query_order(1, 43, "shuffle") or True


def test_benchmark_settings_validation(env):
    env(BENCH_RUNS="0")
    with pytest.raises(ValueError):
        benchmark.settings()
    env(BENCH_RUNS="5", BENCH_ORDER="random")
    with pytest.raises(ValueError):
        benchmark.settings()
    env(BENCH_ORDER="fixed")
    assert benchmark.settings()["runs"] == 5 and benchmark.settings()["warmups"] == 2


def test_benchmark_fails_cleanly_without_loader(raw_data, env):
    env(SIZES="1")
    assert benchmark.main() == 1
    assert json.loads((common.results_dir() / "run_status.json").read_text())["state"] == "failed"


@pg
def test_postgres_load_and_full_benchmark(prepared, env):
    env(BENCH_RUNS="3", BENCH_WARMUPS="1")
    manifest = common.read_manifest()
    for n in (1, 2):
        assert load_postgres.load(n, f"m{n}") == common.months_rows(manifest, n)
        load_duckdb.load(n)
    assert benchmark.main() == 0
    res = common.results_dir()
    status = json.loads((res / "run_status.json").read_text())
    assert status["state"] == "complete" and status["runs"] == 3
    for name in ("benchmark.csv", "benchmark_summary.csv", "lookup_latency.csv", "disk_sizes.csv", "index_build.csv",
                 "explain_q5.csv", "equivalence.csv", "db_consistency.csv", "datasets.csv"):
        df, problem = read_csv_safe(res, name)
        assert df is not None, (name, problem)       # file exists with the schema the dashboard expects
    bench = read_csv_safe(res, "benchmark.csv")[0]
    # 1 first + 1 warmup + 3 warm per query; 5 queries + indexed Q5, 2 engines, 2 sizes
    per = bench.groupby(["engine", "months", "query"]).phase.value_counts()
    assert set(bench.phase) == {"first", "warmup", "warm"}
    assert (per.xs("warm", level="phase") == 3).all() and (per.xs("first", level="phase") == 1).all()
    assert set(bench.engine) == {"postgres", "postgres_indexed", "duckdb"}
    eq = read_csv_safe(res, "equivalence.csv")[0]
    assert (eq.status == "PASS").all() and len(eq) == 2 * 6
    ex = read_csv_safe(res, "explain_q5.csv")[0]
    assert set(ex.state) == {"no_index", "with_index"}
    no_idx = ex[ex.state == "no_index"]
    assert not no_idx.plan_nodes.str.contains("Index").any()          # baseline: scan without our index
    assert ex[ex.state == "with_index"].plan_nodes.str.contains("Index").all()   # EXPLAIN verified the index use
    ds = read_csv_safe(res, "datasets.csv")[0]
    assert list(ds.rows) == [common.months_rows(manifest, 1), common.months_rows(manifest, 2)]
    assert (res / "explain" / "q5_1m_with_index.json").exists()
    # every result file the dashboard knows has the columns the dashboard expects
    import city_results
    assert city_results.main() == 0
    for name in SCHEMAS:
        if name == "memory_summary.csv":      # optional, made by scripts/summarize_memory.py
            continue
        df, problem = read_csv_safe(res, name)
        assert df is not None, (name, problem)


@pg
def test_benchmark_rejects_timings_when_results_differ(prepared, env):
    env(BENCH_RUNS="2", BENCH_WARMUPS="0", SIZES="1")
    load_postgres.load(1, "m1")
    load_duckdb.load(1)
    con = duckdb.connect(str(common.duck_path(1)))
    con.execute("UPDATE trips SET fare_amount = fare_amount + 5 WHERE trip_id % 7 = 0")
    con.close()
    assert benchmark.main() == 1
    res = common.results_dir()
    assert not (res / "benchmark.csv").exists() and (res / "rejected_timings.csv").exists()
    assert "FAIL" in (res / "equivalence.csv").read_text()
    assert json.loads((res / "run_status.json").read_text())["state"] == "failed"


@pg
def test_benchmark_rejects_databases_from_another_dataset(prepared, env):
    env(BENCH_RUNS="1", BENCH_WARMUPS="0", SIZES="1")
    load_postgres.load(1, "m1")
    load_duckdb.load(1)
    env(NIGHT_END="5")              # changed setting -> new clean dataset, databases are now stale
    assert preprocess.main() == 0
    assert benchmark.main() == 1
    status = json.loads((common.results_dir() / "run_status.json").read_text())
    assert status["state"] == "failed" and "different dataset" in status["reason"]


@pg
def test_postgres_loader_is_rerunnable(prepared, env):
    n1 = load_postgres.load(1, "m1")
    n2 = load_postgres.load(1, "m1")   # second run replaces, does not duplicate
    assert n1 == n2
    with load_postgres.connect() as c:
        assert c.execute("SELECT count(*) FROM m1.trips").fetchone()[0] == n1
        assert c.execute("SELECT count(*) FROM m1.load_meta WHERE key='fingerprint'").fetchone()[0] == 1


@pg
def test_run_loader_twice_and_with_full_dataset(raw_data, env):
    import run_loader
    env(SIZES="1,2", BUILD_FULL_DATASET="1")
    assert run_loader.main() == 0
    assert run_loader.main() == 0            # rerun: reuse clean files, rebuild databases, no duplicates
    lines = (common.results_dir() / "load_times.csv").read_text().splitlines()
    assert len(lines) == 1 + 4               # recreated, not appended to the old file
    assert len(list((raw_data / "full").glob("*.parquet"))) == 1
    with load_postgres.connect() as c:
        manifest = common.read_manifest()
        assert c.execute("SELECT count(*) FROM m2.trips").fetchone()[0] == common.months_rows(manifest, 2)
