"""Tests of benchmark/rigor_study.py with FAKE connections and tiny synthetic data only.
No real NYC data, no PostgreSQL server, no Docker. No performance numbers are asserted."""
import csv
import json
from pathlib import Path

import pytest

import equivalence
import queries as Q
import rigor_study as R

FP = "f" * 64
ROWS = {1: 1000, 2: 2100}


# ------------------------------------------------------------------ helpers
def pg_plan(workers=True, est_groups=200, actual_groups=12):
    """A PostgreSQL EXPLAIN (ANALYZE, FORMAT JSON) result shaped like the real ones in results/explain/."""
    scan = {"Node Type": "Seq Scan", "Parallel Aware": True, "Relation Name": "trips", "Plan Rows": 700000,
            "Actual Rows": 540000, "Actual Loops": 3}
    partial = {"Node Type": "Aggregate", "Strategy": "Hashed", "Partial Mode": "Partial", "Plan Rows": est_groups,
               "Actual Rows": actual_groups, "Actual Loops": 3, "Plans": [scan]}
    if workers:
        gather = {"Node Type": "Gather Merge", "Plan Rows": est_groups * 2, "Actual Rows": actual_groups * 3,
                  "Actual Loops": 1, "Workers Planned": 2, "Workers Launched": 2,
                  "Plans": [{"Node Type": "Sort", "Plans": [partial]}]}
        final = {"Node Type": "Aggregate", "Strategy": "Sorted", "Partial Mode": "Finalize", "Plan Rows": est_groups,
                 "Actual Rows": actual_groups, "Actual Loops": 1, "Plans": [gather]}
    else:
        scan = dict(scan, **{"Parallel Aware": False, "Actual Loops": 1})
        final = {"Node Type": "Aggregate", "Strategy": "Hashed", "Partial Mode": "Simple", "Plan Rows": est_groups,
                 "Actual Rows": actual_groups, "Actual Loops": 1, "Plans": [scan]}
    return [{"Plan": {"Node Type": "Sort", "Plan Rows": est_groups, "Actual Rows": actual_groups,
                      "Actual Loops": 1, "Plans": [final]},
             "Planning Time": 0.2, "Execution Time": 123.4, "JIT": {"Timing": {"Total": 9.5}}}]


# Shaped like DuckDB 2.0's EXPLAIN ANALYZE output (boxes, operator name in the top border).
DUCK_TEXT = """\
╭─ Summary ───────────╮
│ Total Time: 0.0071s │
╰─────────────────────╯
╭─ Projection ──────────────────────╮
│ Projections: CAST(#0 AS INTEGER), │
│ 12 rows                       0µs │
╰─────────────────┒┎────────────────╯
╭─ Order By ──────┚┖────────────────╮
│ Order By: hour ASC                │
╰─────────────────┒┎────────────────╯
╭─ Perfect Hash Group By ───────────╮
│ Groups: #0                        │
╰─────────────────┒┎────────────────╯
╭─ Projection ────┚┖────────────────╮
│ Projections: pickup_ts            │
╰─────────────────┒┎────────────────╯
╭─ Table Scan ────┚┖────────────────╮
│ Table: taxi_4m.main.trips         │
│ Type: Sequential Scan             │
╰───────────────────────────────────╯
"""
# The older capital-letter format, still understood.
DUCK_TEXT_OLD = """\
Total Time: 0.0071s
| PROJECTION |
| ORDER_BY |
| PERFECT_HASH_GROUP_BY |
| TABLE_SCAN |  trips
"""


class FakeDB:
    """Answers the statements the study sends; records every call. `differ` = (engine, variant)
    whose canonical query results are changed, to exercise the equivalence gate."""

    def __init__(self, fingerprint=FP, rows=ROWS, read_only="on", differ=None):
        self.fingerprint, self.rows, self.read_only, self.differ = fingerprint, rows, read_only, differ
        self.calls = []
        self.canonical = {text: q for q, text in Q.queries().items()}

    def factory(self, cfg):
        def connect(engine, months, variant):
            def raw(sql):
                self.calls.append((engine, months, variant["variant"], sql))
                return self.answer(engine, months, variant, sql)
            ex = R.Executor(engine, months, variant["variant"], raw)
            if engine == "postgres":
                R.prepare_postgres(ex, months, variant)
            return ex
        return connect

    def answer(self, engine, months, variant, sql):
        s = sql.strip()
        if s == "SHOW default_transaction_read_only":
            return [(self.read_only,)]
        if s.startswith("SET "):
            return []
        if s.startswith("SHOW "):
            return [("2",)]
        if "current_setting" in s or "version()" in s:
            return [("x",)]
        if s == "SELECT key, value FROM load_meta":
            return [("fingerprint", self.fingerprint), ("rows", str(self.rows[months]))]
        if s == "SELECT count(*) FROM trips":
            return [(self.rows[months],)]
        if s.startswith("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)"):
            return [(pg_plan(),)]
        if s.startswith("EXPLAIN ANALYZE"):
            return [("analyzed_plan", DUCK_TEXT)]
        if sql in self.canonical:
            q = self.canonical[sql]
            value = 2.5
            if self.differ == (engine, variant["variant"]):
                value = 9.9
            return [(1, q, value), (2, q, 1.25)]
        raise AssertionError(f"unexpected SQL in fake: {s[:80]}")


def write_manifest(data_dir: Path, fingerprint=FP):
    (data_dir / "clean").mkdir(parents=True, exist_ok=True)
    (data_dir / "clean" / "manifest.json").write_text(json.dumps(
        {"fingerprint": fingerprint, "months": [{"kept": 1000}, {"kept": 1100}]}))


def never_connect(cfg):
    def connect(*a, **k):
        raise AssertionError("the study connected to a database")
    return connect


def read_rows(path: Path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


# ------------------------------------------------------------------ canonical SQL
def test_study_sql_comes_from_the_canonical_query_module():
    canonical = Q.queries()
    assert R.study_sql() == {q: canonical[q] for q in ("Q1", "Q2", "Q3")}
    assert R.study_sql(["Q2"]) == {"Q2": canonical["Q2"]}
    with pytest.raises(R.StudyError):
        R.study_sql(["Q4"])


def test_the_equivalence_gate_is_the_projects_own():
    assert R.compare_results is equivalence.compare_results
    source = Path(R.__file__).read_text()
    assert "import benchmark" not in source and "benchmark.main" not in source


# ------------------------------------------------------------------ plan parsing
def test_pg_plan_parser_reads_workers_estimates_and_times():
    s = R.summarize_pg_plan(pg_plan())
    assert s["workers_planned"] == 2 and s["workers_launched"] == 2
    assert s["top_aggregate_estimated_rows"] == 200
    assert s["top_aggregate_actual_rows"] == 12
    assert s["top_aggregate_estimate_ratio"] == pytest.approx(200 / 12)
    assert s["execution_ms"] == 123.4 and s["planning_ms"] == 0.2 and s["jit_ms"] == 9.5
    assert "Gather Merge" in s["node_types"] and s["scans"] == ["Seq Scan(trips)"]
    partial = [a for a in s["aggregates"] if a["partial_mode"] == "Partial"][0]
    assert partial["actual_rows_total"] == 36  # rows per loop x loops


def test_pg_plan_parser_without_workers_and_from_a_json_string():
    s = R.summarize_pg_plan(json.dumps(pg_plan(workers=False)))
    assert s["workers_planned"] is None and s["workers_launched"] is None
    assert s["top_aggregate_estimated_rows"] == 200


def test_pg_plan_parser_rejects_something_else():
    with pytest.raises(R.StudyError):
        R.summarize_pg_plan([{"no": "plan"}])


def test_duckdb_plan_parser_reads_the_duckdb_2_boxes():
    s = R.summarize_duckdb_plan(DUCK_TEXT)
    assert s["operators"] == ["Projection", "Order By", "Perfect Hash Group By", "Table Scan"]
    assert s["total_time_ms"] == pytest.approx(7.1)


def test_duckdb_plan_parser_falls_back_to_the_old_format():
    s = R.summarize_duckdb_plan(DUCK_TEXT_OLD)
    assert s["operators"] == ["PROJECTION", "ORDER_BY", "PERFECT_HASH_GROUP_BY", "TABLE_SCAN"]
    assert s["total_time_ms"] == pytest.approx(7.1)
    assert R.summarize_duckdb_plan("no timing here") == {"operators": [], "total_time_ms": None}


# ------------------------------------------------------------------ schedule and variants
def test_session_schedule_is_seeded_and_alternates_engines():
    a = R.session_schedule(3, 142)
    assert a == R.session_schedule(3, 142)
    assert [s["seed"] for s in a] == [142, 143, 144]
    assert [s["engine_order"] for s in a] == [["postgres", "duckdb"], ["duckdb", "postgres"],
                                              ["postgres", "duckdb"]]
    for s in a:
        assert sorted(s["query_order"]) == ["Q1", "Q2", "Q3"]
    with pytest.raises(R.StudyError):
        R.session_schedule(0, 1)


def test_variants_record_their_settings():
    v = R.variants([1, 3], [0, 2])
    assert [(x["engine"], x["variant"]) for x in v] == [
        ("postgres", "default"), ("duckdb", "default"), ("postgres", "pg_workers_0"), ("postgres", "pg_workers_2"),
        ("duckdb", "duckdb_threads_1"), ("duckdb", "duckdb_threads_3")]
    assert v[4]["setting"] == "threads" and v[4]["value"] == 1
    assert v[3]["setting"] == "max_parallel_workers_per_gather" and v[3]["value"] == 2
    with pytest.raises(R.StudyError):
        R.variants([0], [])
    with pytest.raises(R.StudyError):
        R.parse_int_list("1,x", "--sizes")


# ------------------------------------------------------------------ read-only guard
@pytest.mark.parametrize("sql", list(Q.queries().values())[:3] + [
    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + Q.queries()["Q1"],
    "EXPLAIN ANALYZE " + Q.queries()["Q2"],
    "SHOW max_parallel_workers_per_gather",
    "SET max_parallel_workers_per_gather = 0",
    "SET search_path TO m4",
    "SELECT key, value FROM load_meta",
    "SELECT count(*) FROM trips;",
])
def test_guard_allows_read_only_statements(sql):
    assert R.assert_read_only_sql(sql) == sql


@pytest.mark.parametrize("sql", [
    "DROP TABLE trips", "CREATE INDEX i ON trips (pickup_ts)", "INSERT INTO trips VALUES (1)",
    "UPDATE trips SET fare_amount = 0", "DELETE FROM trips", "ANALYZE trips", "VACUUM trips",
    "SELECT 1; DROP TABLE trips", "SELECT * INTO copy_of_trips FROM trips", "EXPLAIN ANALYZE DELETE FROM trips",
    "EXPLAIN (ANALYZE, FORMAT JSON) INSERT INTO trips VALUES (1)", "SET work_mem = '1GB'",
    "COPY trips TO '/tmp/x.csv'", "PRAGMA threads=2", "CHECKPOINT", "TRUNCATE trips", "",
    "WITH x AS (DELETE FROM trips RETURNING *) SELECT * FROM x",
])
def test_guard_refuses_writes(sql):
    with pytest.raises(R.StudyError):
        R.assert_read_only_sql(sql)


def test_executor_stops_a_write_before_it_reaches_the_database():
    sent = []
    ex = R.Executor("postgres", 1, "default", lambda s: sent.append(s) or [])
    with pytest.raises(R.StudyError):
        ex.run("DROP TABLE trips")
    assert sent == []


def test_postgres_session_that_is_not_read_only_is_refused():
    db = FakeDB(read_only="off")
    connect = db.factory({})
    with pytest.raises(R.StudyError, match="not read-only"):
        connect("postgres", 1, R.variants([], [])[0])
    assert [c[3] for c in db.calls] == ["SHOW default_transaction_read_only"]


# ------------------------------------------------------------------ output protection
def test_output_dir_refuses_overwrites_and_protected_places(tmp_path):
    results, data = tmp_path / "results", tmp_path / "data"
    results.mkdir()
    data.mkdir()
    (results / "benchmark.csv").write_text("original\n")
    (results / "old_study").mkdir()
    with pytest.raises(R.StudyError):
        R.OutputDir(results, results, data)                    # the results root itself
    with pytest.raises(R.StudyError):
        R.OutputDir(results / "old_study", results, data)      # already exists
    with pytest.raises(R.StudyError):
        R.OutputDir(data / "study", results, data)             # inside DATA_DIR
    out = R.OutputDir(results / "marin_rigor_test", results, data)
    out.create()
    with pytest.raises(R.StudyError):
        out.write_text("benchmark.csv", "x")                   # protected name
    with pytest.raises(R.StudyError):
        out.write_text("../escape.txt", "x")                   # outside the directory
    out.write_text("a.txt", "1")
    with pytest.raises(R.StudyError):
        out.write_text("a.txt", "2")                           # no overwrite
    assert (results / "benchmark.csv").read_text() == "original\n"
    assert (out.path / "a.txt").read_text() == "1"


# ------------------------------------------------------------------ main: no access without permission
def test_dry_run_never_connects_or_writes(env, tmp_path, capsys):
    assert R.main(["--dry-run", "--stage", "all"], connect_factory=never_connect) == 0
    text = capsys.readouterr().out
    assert "DRY RUN" in text and "session 2" in text and "duckdb_threads_3" in text
    assert not (tmp_path / "results").exists()


def test_without_confirm_flag_nothing_connects(env, tmp_path, capsys):
    assert R.main([], connect_factory=never_connect) == 0
    assert "--confirm-real-run was not given" in capsys.readouterr().out
    assert not (tmp_path / "results").exists()


def test_missing_manifest_is_a_clear_non_destructive_failure(env, tmp_path, capsys):
    assert R.main(["--confirm-real-run"], connect_factory=never_connect) == 1
    assert "manifest.json not found" in capsys.readouterr().out
    assert not (tmp_path / "results").exists()


def test_database_from_another_dataset_stops_before_writing(env, tmp_path, capsys):
    write_manifest(tmp_path / "data")
    db = FakeDB(fingerprint="0" * 64)
    assert R.main(["--confirm-real-run", "--sizes", "1"], connect_factory=db.factory) == 1
    assert "different dataset" in capsys.readouterr().out
    assert not (tmp_path / "results").exists()
    assert not any(c[3].startswith("EXPLAIN") for c in db.calls)


# ------------------------------------------------------------------ main: a whole fake run
def test_fake_run_writes_plans_timings_and_protocol(env, tmp_path):
    write_manifest(tmp_path / "data")
    results = tmp_path / "results"
    results.mkdir()
    (results / "benchmark.csv").write_text("original\n")
    out = results / "marin_rigor_test"
    db = FakeDB()
    code = R.main(["--confirm-real-run", "--stage", "all", "--sizes", "1,2", "--sessions", "2", "--runs", "2",
                   "--warmups", "1", "--output-dir", str(out)], connect_factory=db.factory)
    assert code == 0
    assert (results / "benchmark.csv").read_text() == "original\n"
    assert not (out / "benchmark.csv").exists() and not (out / "benchmark_summary.csv").exists()

    plans = read_rows(out / "plan_summary.csv")
    assert len(plans) == 2 * 6 * 3                                 # sizes x variants x queries
    assert (out / "plans" / "pg_2m_Q1_pg_workers_0.json").exists()
    assert (out / "plans" / "duckdb_1m_Q3_duckdb_threads_3.txt").exists()
    t1 = [p for p in plans if p["variant"] == "duckdb_threads_1"][0]
    assert t1["setting"] == "threads" and t1["value"] == "1"
    pg = [p for p in plans if p["engine"] == "postgres"][0]
    assert pg["workers_launched"] == "2" and pg["agg_estimated_rows"] == "200"

    timings = read_rows(out / "rigor_timings.csv")
    assert len(timings) == 2 * 2 * 6 * 3 * 4                       # sessions x sizes x variants x queries x 4
    assert all(0 <= float(r["seconds"]) < 60 for r in timings)     # seconds, not milliseconds
    first_engine = {r["session"]: r["engine"] for r in timings if r["engine_position"] == "1"}
    assert first_engine == {"1": "postgres", "2": "duckdb"}
    assert {r["phase"] for r in timings} == {"first", "warmup", "warm"}

    eq = read_rows(out / "rigor_equivalence.csv")
    assert eq and all(r["status"] == "PASS" for r in eq)
    assert len(eq) == 2 * 2 * 3 * 5                                # every variant but the reference
    sessions = read_rows(out / "rigor_session_medians.csv")
    assert sessions[0]["sessions"] == "2"
    header = (out / "rigor_timing_summary.csv").read_text().splitlines()[0]
    assert "median_s" in header

    protocol = json.loads((out / "protocol.json").read_text())
    assert protocol["status"] == "complete" and protocol["fingerprint"] == FP
    assert len(protocol["schedule"]) == 2 and protocol["caveats"]

    sent = [c[3] for c in db.calls]
    for s in sent:
        R.assert_read_only_sql(s)                                  # everything sent was read-only
    assert "SET max_parallel_workers_per_gather = 0" in sent


def test_variant_with_a_different_result_rejects_the_timings(env, tmp_path):
    write_manifest(tmp_path / "data")
    out = tmp_path / "results" / "marin_rigor_bad"
    db = FakeDB(differ=("duckdb", "duckdb_threads_1"))
    code = R.main(["--confirm-real-run", "--stage", "timings", "--sizes", "1", "--sessions", "1", "--runs", "1",
                   "--warmups", "0", "--output-dir", str(out)], connect_factory=db.factory)
    assert code == 1
    assert (out / "rigor_timings_REJECTED.csv").exists()
    assert not (out / "rigor_timings.csv").exists()
    assert any(r["status"] == "FAIL" for r in read_rows(out / "rigor_equivalence.csv"))
    assert json.loads((out / "protocol.json").read_text())["status"].startswith("failed")


def test_existing_output_dir_is_refused_before_any_database_access(env, tmp_path, capsys):
    write_manifest(tmp_path / "data")
    out = tmp_path / "results" / "marin_rigor_old"
    out.mkdir(parents=True)
    assert R.main(["--confirm-real-run", "--output-dir", str(out)], connect_factory=never_connect) == 1
    assert "already exists" in capsys.readouterr().out


# ------------------------------------------------------------------ real DuckDB, tiny synthetic file
def test_real_duckdb_file_is_opened_read_only(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    path = tmp_path / "taxi_1m.duckdb"
    con = duckdb.connect(str(path))
    con.execute((Path(R.__file__).resolve().parent.parent / "sql" / "schema.sql").read_text())
    con.execute("INSERT INTO zones VALUES (1, 'Manhattan', 'A', 'Yellow Zone'), (2, 'Queens', 'B', 'Boro Zone')")
    con.execute("""INSERT INTO trips VALUES
        (1, TIMESTAMP '2026-04-02 19:00:00', TIMESTAMP '2026-04-02 19:10:00', 1, 2, 1, 1.0, 10.0, 1.0, 12.0),
        (2, TIMESTAMP '2026-04-02 23:30:00', TIMESTAMP '2026-04-02 23:45:00', 2, 1, 1, 2.0, 15.0, 2.0, 18.0)""")
    con.execute(f"INSERT INTO load_meta VALUES ('fingerprint', '{FP}'), ('rows', '2')")
    con.close()

    v = [x for x in R.variants([1], []) if x["variant"] == "duckdb_threads_1"][0]
    ex = R.connect_duckdb(1, v, tmp_path)
    try:
        R.check_dataset(ex, FP, 2)
        assert ex.run("SELECT current_setting('threads')")[0][0] in (1, "1")
        for q, text in R.study_sql().items():
            assert ex.run(text), q
        plan = R.summarize_duckdb_plan(R.duckdb_explain_text(ex.run("EXPLAIN ANALYZE " + R.study_sql()["Q1"])))
        assert any("scan" in op.lower() for op in plan["operators"]), plan
        with pytest.raises(Exception):
            ex._raw_run("DELETE FROM trips")                       # the file itself is read-only
    finally:
        ex.close()
