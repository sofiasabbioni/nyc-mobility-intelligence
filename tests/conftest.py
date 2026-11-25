import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for sub in ("loader", "sql", "benchmark", "app", "tests", "scripts"):
    sys.path.insert(0, str(ROOT / sub))

SETTING_VARS = ["START_MONTH", "TOTAL_MONTHS", "NIGHT_START", "NIGHT_END", "SIZES", "DATA_DIR", "RESULTS_DIR",
                "BENCH_RUNS", "BENCH_WARMUPS", "BENCH_SEED", "BENCH_ORDER", "FORCE_REBUILD", "BUILD_FULL_DATASET",
                "MONTHS", "POSTGRES_SCHEMA"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Clean settings, with DATA_DIR and RESULTS_DIR inside tmp_path. Returns a setter."""
    for v in SETTING_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RESULTS_DIR", str(tmp_path / "results"))

    def setter(**kw):
        for k, v in kw.items():
            monkeypatch.setenv(k, str(v))
    return setter


@pytest.fixture
def raw_data(env, tmp_path):
    """Four small synthetic monthly files + zones in DATA_DIR/raw."""
    from synth import make_raw
    make_raw(tmp_path / "data" / "raw", rows=3000)
    return tmp_path / "data"
