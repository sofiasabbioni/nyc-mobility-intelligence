import json
from pathlib import Path

import pandas as pd
import pytest
import yaml
from streamlit.testing.v1 import AppTest

import results_io as rio

ROOT = Path(__file__).resolve().parent.parent


def test_read_csv_safe_variants(tmp_path):
    assert rio.read_csv_safe(tmp_path, "benchmark.csv")[0] is None                   # missing
    (tmp_path / "benchmark.csv").write_text("")                                      # empty file
    assert "cannot be read" in rio.read_csv_safe(tmp_path, "benchmark.csv")[1]
    (tmp_path / "benchmark.csv").write_text("engine,months\n")                       # header only, wrong schema
    assert "lacks columns" in rio.read_csv_safe(tmp_path, "benchmark.csv")[1]
    (tmp_path / "load_times.csv").write_text("engine,months,rows,seconds\n")         # right schema, no rows
    assert "empty" in rio.read_csv_safe(tmp_path, "load_times.csv")[1]
    (tmp_path / "load_times.csv").write_text("engine,months,rows,seconds\n\"broken")  # truncated mid-line
    df, problem = rio.read_csv_safe(tmp_path, "load_times.csv")
    assert df is None or problem is None
    (tmp_path / "load_times.csv").write_text("engine,months,rows,seconds,fingerprint\nduckdb,1,5,0.1,x\n")
    assert rio.read_csv_safe(tmp_path, "load_times.csv")[1] is None                  # extra columns are fine


def write_status(path, state, **kw):
    path.mkdir(exist_ok=True, parents=True)
    (path / "run_status.json").write_text(json.dumps({"state": state, **kw}))


def test_timings_status_rules(tmp_path):
    assert not rio.timings_status(tmp_path)[0]
    write_status(tmp_path, "running")
    assert not rio.timings_status(tmp_path)[0] and "still running" in rio.timings_status(tmp_path)[1]
    write_status(tmp_path, "failed", reason="x")
    assert not rio.timings_status(tmp_path)[0]
    write_status(tmp_path, "complete")
    assert not rio.timings_status(tmp_path)[0]                       # no equivalence file
    eq = "months,query,engine_a,engine_b,rows_compared,status,detail\n1,Q1,postgres,duckdb,3,PASS,identical\n"
    (tmp_path / "equivalence.csv").write_text(eq)
    assert rio.timings_status(tmp_path)[0]
    (tmp_path / "equivalence.csv").write_text(eq + "1,Q2,postgres,duckdb,3,FAIL,x\n")
    assert not rio.timings_status(tmp_path)[0]


def test_size_labels_use_actual_rows():
    ds = pd.DataFrame({"months": [1, 4], "rows": [1_234_567, 5_000_000], "pct_of_largest_rows": [24.69, 100.0]})
    labels = rio.size_labels(ds, [1, 4, 2])
    assert "1,234,567 rows" in labels[1] and "25% of rows" in labels[1] and labels[2] == "2 mo"


def app(results: Path):
    import os
    os.environ["RESULTS_DIR"] = str(results)
    return AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=60).run()


def test_dashboard_with_empty_results(tmp_path):
    at = app(tmp_path)
    assert not at.exception and len(at.info) >= 3


def test_dashboard_with_incomplete_and_broken_files(tmp_path):
    (tmp_path / "city").mkdir()
    (tmp_path / "city" / "hourly.csv").write_text("hour,trips\n18,5\n")                 # wrong schema
    (tmp_path / "benchmark_summary.csv").write_text("engine,months\nduckdb,1\n")          # wrong schema
    (tmp_path / "load_times.csv").write_text("")                                          # empty
    (tmp_path / "disk_sizes.csv").write_text("what,months,bytes\npostgres,1,100\n")       # partial but valid
    write_status(tmp_path, "running")
    at = app(tmp_path)
    assert not at.exception


def test_dashboard_hides_timings_when_equivalence_failed(tmp_path):
    write_status(tmp_path, "failed", reason="equivalence or consistency check failed")
    (tmp_path / "benchmark_summary.csv").write_text(
        "engine,months,rows,query,workload,phase,n,mean_s,median_s,stdev_s,min_s,max_s\n"
        "duckdb,1,10,Q1,analytical,warm,5,0.1,0.1,0.0,0.1,0.1\n")
    at = app(tmp_path)
    assert not at.exception and not at.selectbox and any("did not complete" in w.value for w in at.warning)


def test_compose_file_and_dockerfiles():
    c = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    s = c["services"]
    assert set(s) == {"db", "loader", "benchmark", "app"}
    assert s["loader"]["depends_on"]["db"]["condition"] == "service_healthy"
    assert s["benchmark"]["depends_on"]["loader"]["condition"] == "service_completed_successfully"
    assert s["benchmark"]["depends_on"]["db"]["condition"] == "service_healthy"
    assert s["app"]["depends_on"]["loader"]["condition"] == "service_completed_successfully"
    assert "healthcheck" in s["db"]
    for svc in ("loader", "benchmark"):                    # same data volume and the host raw folder
        vols = " ".join(s[svc]["volumes"])
        assert "taxi_data:/data" in vols and "./data/raw:/data/raw" in vols and "./results:/results" in vols
    assert any(v.startswith("./data/raw:/data/raw") for v in s["loader"]["volumes"])
    assert "8501:8501" in s["app"]["ports"]
    assert set(c["volumes"]) == {"pgdata", "taxi_data"}
    for name, df in (("loader", "loader/Dockerfile"), ("benchmark", "benchmark/Dockerfile")):
        text = (ROOT / df).read_text()
        assert "COPY sql/ sql/" in text and "requirements.txt" in text
    assert (ROOT / "app" / "results_io.py").exists() and "results_io.py" in (ROOT / "app" / "Dockerfile").read_text()


def test_every_compose_variable_is_documented_in_env_example():
    import re
    text = (ROOT / "docker-compose.yml").read_text()
    used = set(re.findall(r"\$\{([A-Z_]+)", text))
    documented = {l.split("=")[0].strip().lstrip("# ") for l in (ROOT / ".env.example").read_text().splitlines() if "=" in l}
    assert used <= documented, used - documented


def test_requirements_are_pinned():
    for f in ("loader", "benchmark", "app"):
        for line in (ROOT / f / "requirements.txt").read_text().splitlines():
            assert "==" in line, (f, line)


def test_schema_sql_matches_loaders():
    sql = (ROOT / "sql" / "schema.sql").read_text()
    for table in ("zones", "trips", "load_meta"):
        assert f"CREATE TABLE {table}" in sql
    assert "trip_id         BIGINT PRIMARY KEY" in sql
