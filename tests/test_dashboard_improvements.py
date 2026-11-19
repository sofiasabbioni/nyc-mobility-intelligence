"""Tests for the dashboard improvements: matched speed ratios, key findings, quality KPIs, map ranking.
Synthetic rows only; no database, no full dataset."""
import json
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

import results_io as rio
from nightlife_map import top_zones

ROOT = Path(__file__).resolve().parent.parent
SUMMARY_COLS = "engine,months,rows,query,workload,phase,n,mean_s,median_s,stdev_s,min_s,max_s"


def summary_rows(rows):
    """rows: (engine, months, query, phase, median_s)"""
    return pd.DataFrame([{"engine": e, "months": m, "query": q, "phase": p, "median_s": v}
                         for e, m, q, p, v in rows])


def test_ratio_and_winner_from_matched_warm_medians():
    s = summary_rows([("postgres", 1, "Q1", "warm", 2.0), ("duckdb", 1, "Q1", "warm", 0.5),
                      ("postgres", 1, "Q2", "warm", 0.2), ("duckdb", 1, "Q2", "warm", 0.4),
                      ("postgres", 1, "Q1", "first", 9.0), ("duckdb", 1, "Q1", "first", 1.0)])
    sp = rio.speedup_table(s, "warm").set_index("query")
    assert sp.loc["Q1", "ratio"] == 4.0 and sp.loc["Q1", "winner"] == "DuckDB" and sp.loc["Q1", "factor"] == 4.0
    assert sp.loc["Q2", "ratio"] == 0.5 and sp.loc["Q2", "winner"] == "PostgreSQL" and sp.loc["Q2", "factor"] == 2.0
    assert rio.speedup_table(s, "first").set_index("query").loc["Q1", "ratio"] == 9.0   # phases never mixed


def test_ratio_skips_zero_missing_index_and_q4():
    s = summary_rows([("postgres", 1, "Q1", "warm", 1.0), ("duckdb", 1, "Q1", "warm", 0.0),      # zero
                      ("postgres", 2, "Q1", "warm", 1.0),                                          # no DuckDB row
                      ("postgres_indexed", 1, "Q5", "warm", 0.1), ("duckdb", 1, "Q5", "warm", 0.2),  # index engine
                      ("postgres", 1, "Q4", "warm", 1.0), ("duckdb", 1, "Q4", "warm", 2.0)])       # Q4 not analytical
    assert rio.speedup_table(s).empty
    assert rio.speedup_table(None).empty


def test_key_findings_use_largest_size_and_verified_inputs():
    s = summary_rows([("postgres", 1, "Q1", "warm", 1.0), ("duckdb", 1, "Q1", "warm", 0.1),
                      ("postgres", 2, "Q1", "warm", 3.0), ("duckdb", 2, "Q1", "warm", 0.1),
                      ("postgres", 2, "Q5", "warm", 1.0), ("postgres_indexed", 2, "Q5", "warm", 0.25)])
    lookup = pd.DataFrame([{"engine": "postgres", "months": 2, "phase": "warm", "lookups": 200, "median_ms": 0.2},
                           {"engine": "duckdb", "months": 2, "phase": "warm", "lookups": 200, "median_ms": 0.8}])
    used = pd.DataFrame([{"months": 2, "state": "with_index", "index_used": True}])
    f = rio.key_findings(s, lookup, used)
    assert [x["title"].split(" (")[0] for x in f] == ["Largest analytical gap", "Q4 point lookups",
                                                      "Q5 with index on pickup_ts"]
    assert "2 mo" in f[0]["title"] and "30.0x" in f[0]["value"]
    assert "4.0x" in f[1]["value"] and "ms" in f[1]["detail"] and "not an SQL query time" in f[1]["detail"]
    not_used = pd.DataFrame([{"months": 2, "state": "with_index", "index_used": False}])
    assert len(rio.key_findings(s, lookup, not_used)) == 2                    # no index claim without the plan
    slow_pg = lookup.assign(median_ms=[0.9, 0.3])
    assert len(rio.key_findings(s, slow_pg, used)) == 2                       # Q4 card only if PostgreSQL wins
    assert rio.key_findings(None) == []


def test_quality_kpis_sum_months_and_keep_identity():
    summ = pd.DataFrame({"source_month": ["2026-04", "2026-05"], "source_rows": [100, 250],
                         "night_clean_rows": [30, 70], "excluded_rows": [70, 180], "unaccounted_rows": [0, 0]})
    k = rio.quality_kpis(summ)
    assert (k["source"], k["kept"], k["excluded"], k["unaccounted"]) == (350, 100, 250, 0)
    assert k["balanced"] and k["source"] == k["kept"] + k["excluded"]
    bad = summ.assign(excluded_rows=[60, 180], unaccounted_rows=[10, 0])
    assert not rio.quality_kpis(bad)["balanced"]
    assert rio.quality_kpis(None) is None


def test_map_ranking_shows_volume_without_changing_order():
    data = pd.DataFrame([
        {"zone": "A", "borough": "Manhattan", "nightlife_score": 1.5, "avg_late_departures": 30.0, "avg_late_arrivals": 20.0},
        {"zone": "B", "borough": "Brooklyn", "nightlife_score": 2.5, "avg_late_departures": 5.0, "avg_late_arrivals": 2.0},
        {"zone": "C", "borough": "Queens", "nightlife_score": 0.5, "avg_late_departures": 1.0, "avg_late_arrivals": 2.0}])
    t = top_zones(data, "nightlife_score", n=2)
    assert t["zone"].tolist() == ["B", "A"]
    assert {"Late departures / night", "Late arrivals / night"} <= set(t.columns)
    assert list(top_zones(data, "avg_late_departures").columns) == ["zone", "borough", "Late departures / night"]


def write_complete_run(path: Path, equivalence_status="PASS", state="complete"):
    (path / "run_status.json").write_text(json.dumps({"state": state, "runs": 5, "warmups": 2}))
    (path / "equivalence.csv").write_text("months,query,engine_a,engine_b,rows_compared,status,detail\n"
                                          f"1,Q1,postgres,duckdb,3,{equivalence_status},x\n")
    lines = [SUMMARY_COLS]
    for q in ("Q1", "Q2", "Q3", "Q5"):
        for e, v in (("postgres", 1.0), ("duckdb", 0.05), ("postgres_indexed", 0.5)):
            for ph in ("warm", "first"):
                lines.append(f"{e},1,1000,{q},analytical,{ph},5,{v},{v},0.01,{v},{v}")
    (path / "benchmark_summary.csv").write_text("\n".join(lines) + "\n")
    (path / "data_quality").mkdir(exist_ok=True)
    (path / "data_quality" / "preprocess_summary.csv").write_text(
        "source_month,source_rows,night_clean_rows,excluded_rows,unaccounted_rows\n2026-04,100,40,60,0\n")
    (path / "city").mkdir(exist_ok=True)
    (path / "city" / "hourly.csv").write_text("hour,trips,avg_fare,avg_tip\n18,10,15.5,2.1\n23,8,17.0,2.5\n0,5,18.2,2.9\n")


def run_app(results: Path):
    import os
    os.environ["RESULTS_DIR"] = str(results)
    return AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=60).run()


def test_dashboard_shows_findings_and_kpis_for_a_valid_run(tmp_path):
    write_complete_run(tmp_path)
    at = run_app(tmp_path)
    assert not at.exception
    assert "Key findings of this run" in [h.value for h in at.subheader]
    labels = [m.label for m in at.metric]
    assert "Source rows (original files)" in labels and "Excluded rows" in labels


def test_dashboard_hides_findings_when_run_invalid(tmp_path):
    for kw in ({"equivalence_status": "FAIL"}, {"state": "running"}):
        write_complete_run(tmp_path, **kw)
        at = run_app(tmp_path)
        assert not at.exception
        assert "Key findings of this run" not in [h.value for h in at.subheader]
