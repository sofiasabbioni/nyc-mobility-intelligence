"""Tests for the nightlife step inside the loader (run_loader.py).

The nightlife exporter (nightlife_metrics.py) used to be run by hand. It is now step 4 of the
loader, so anyone who starts the project gets results/nightlife/ automatically. These tests check
that this integration behaves correctly: the export runs after the raw files are available and
before the databases are built, it follows the project settings (months and folders), a failure
stops the loader instead of ending with a false "Loader finished.", and a rerun replaces the three
files instead of adding rows to them. The calculations themselves are tested separately, in
test_nightlife_metrics.py.

The tests use the small synthetic files of synth.py, NOT the real TLC data, so they take a few
seconds and work on any computer. They also need no database: the PostgreSQL and DuckDB loaders and
city_results are replaced by stand-ins that load nothing and only record that they were called, and
in which order. For this reason, unlike the database tests, these tests are never skipped.
"""
import json
import sys
import urllib.request

import pandas as pd
import pytest

import nightlife_metrics
import run_loader


@pytest.fixture
def calls(monkeypatch):
    """Replace every step by a recorder. Returns the list of calls, in order."""
    seen = []

    def record(name, result=0):
        def step(*args, **kwargs):
            seen.append(name)
            return result
        return step

    monkeypatch.setattr(run_loader.download_data, "main", record("download"))
    monkeypatch.setattr(run_loader.preprocess, "main", record("preprocess"))
    monkeypatch.setattr(run_loader.build_full_dataset, "main", record("full"))
    monkeypatch.setattr(run_loader.nightlife_metrics, "main", record("nightlife"))
    monkeypatch.setattr(run_loader.load_postgres, "load", lambda n, schema: seen.append(f"postgres {n}"))
    monkeypatch.setattr(run_loader.load_duckdb, "load", lambda n: seen.append(f"duckdb {n}"))
    monkeypatch.setattr(run_loader.city_results, "main", record("city"))
    return seen


@pytest.fixture
def no_databases(monkeypatch):
    """Real download / preprocess / nightlife steps, but no PostgreSQL, DuckDB or city tables."""
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network must not be used")))
    monkeypatch.setattr(run_loader.load_postgres, "load", lambda n, schema: seen.append(f"postgres {n}"))
    monkeypatch.setattr(run_loader.load_duckdb, "load", lambda n: seen.append(f"duckdb {n}"))
    monkeypatch.setattr(run_loader.city_results, "main", lambda: seen.append("city") or 0)
    return seen


def test_nightlife_runs_after_inputs_and_before_databases(env, tmp_path, calls):
    env(SIZES="1,2")
    assert run_loader.main() == 0
    assert calls == ["download", "preprocess", "nightlife",
                     "postgres 1", "duckdb 1", "postgres 2", "duckdb 2", "city"]


def test_nightlife_runs_after_the_optional_full_dataset(env, calls):
    env(SIZES="1", BUILD_FULL_DATASET="1")
    assert run_loader.main() == 0
    assert calls[:4] == ["download", "preprocess", "full", "nightlife"]


def test_exporter_ignores_the_callers_command_line(env, monkeypatch):
    received = []
    monkeypatch.setattr(nightlife_metrics, "main", lambda argv=None: received.append(argv) or 0)
    monkeypatch.setattr(sys, "argv", ["run_loader.py", "--not-a-nightlife-flag"])
    assert run_loader.nightlife_step() == 0
    assert received == [[]]


@pytest.mark.parametrize("failure", ["returns 1", "raises"])
def test_exporter_failure_stops_the_loader(env, monkeypatch, capsys, calls, failure):
    def broken(argv=None):
        calls.append("nightlife")
        if failure == "raises":
            raise FileNotFoundError("missing input file(s): taxi_zone_lookup.csv")
        return 1
    monkeypatch.setattr(run_loader.nightlife_metrics, "main", broken)
    env(SIZES="1")
    if failure == "raises":
        with pytest.raises(FileNotFoundError):
            run_loader.main()
    else:
        assert run_loader.main() == 1
    assert calls == ["download", "preprocess", "nightlife"]          # no database is touched
    assert "Loader finished." not in capsys.readouterr().out


def test_loader_writes_the_three_files_with_the_agreed_columns(raw_data, env, no_databases):
    env(SIZES="1,2,4")
    assert run_loader.main() == 0
    out = raw_data.parent / "results" / "nightlife"
    zm = pd.read_csv(out / "zone_metrics.csv")
    hr = pd.read_csv(out / "hourly.csv")
    assert list(zm.columns) == nightlife_metrics.ZONE_METRICS_COLUMNS
    assert list(hr.columns) == nightlife_metrics.HOURLY_COLUMNS
    assert sorted(hr["hour"].unique()) == [0, 1, 2, 3, 4, 5, 18, 19, 20, 21, 22, 23]
    v = json.loads((out / "validation.json").read_text())
    assert v["months"] == ["2026-04", "2026-05", "2026-06", "2026-07"]
    assert v["complete_nights"]["all"] == 121
    assert no_databases == ["postgres 1", "duckdb 1", "postgres 2", "duckdb 2",
                            "postgres 4", "duckdb 4", "city"]


def test_loader_honours_months_and_folders(raw_data, env, no_databases, tmp_path):
    other = tmp_path / "elsewhere"
    env(START_MONTH="2026-05", TOTAL_MONTHS="2", SIZES="1,2", RESULTS_DIR=str(other))
    assert run_loader.main() == 0
    v = json.loads((other / "nightlife" / "validation.json").read_text())
    assert v["months"] == ["2026-05", "2026-06"]
    assert v["complete_nights"]["first_night_date"] == "2026-05-01"
    assert v["complete_nights"]["last_night_date"] == "2026-06-29"
    assert [f["file"] for f in v["input_files"]] == ["yellow_tripdata_2026-05.parquet",
                                                    "yellow_tripdata_2026-06.parquet"]
    assert not (raw_data.parent / "results" / "nightlife").exists()


def test_loader_rerun_replaces_the_files(raw_data, env, no_databases):
    env(SIZES="1")
    out = raw_data.parent / "results" / "nightlife"
    assert run_loader.main() == 0
    first = {f: (out / f).read_bytes() for f in ("zone_metrics.csv", "hourly.csv")}
    assert run_loader.main() == 0
    second = {f: (out / f).read_bytes() for f in ("zone_metrics.csv", "hourly.csv")}
    assert first == second
    assert len(pd.read_csv(out / "zone_metrics.csv")) == 265 * 3       # not appended


def test_nightlife_reads_the_raw_files_not_the_clean_subset(raw_data, env, no_databases):
    env(SIZES="1")
    assert run_loader.main() == 0
    v = json.loads((raw_data.parent / "results" / "nightlife" / "validation.json").read_text())
    raw_rows = sum(f["rows"] for f in v["input_files"])
    clean_rows = sum(m["kept"] for m in json.loads((raw_data / "clean" / "manifest.json").read_text())["months"])
    assert v["raw_rows"] == raw_rows > clean_rows                     # every source row was read
