import hashlib
import json
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

import common
import preprocess
from synth import bad_rows

NAMES = ["pickup_ts", "dropoff_ts", "pu_location_id", "do_location_id", "passenger_count", "trip_distance",
         "fare_amount", "tip_amount", "total_amount"]


def bad_table(month="2026-04"):
    cols = list(zip(*bad_rows(month)))
    types = [pa.timestamp("us")] * 2 + [pa.int32()] * 2 + [pa.int32(), pa.float64(), pa.float64(), pa.float64(), pa.float64()]
    arrays = [pa.array(c, type=t) for c, t in zip(cols, types)]
    arrays[4] = pa.array([None if v is None else int(v) for v in cols[4]], type=pa.int32())
    return pa.Table.from_arrays(arrays, names=NAMES)


def test_each_reason_is_assigned_once_with_priority():
    t = bad_table()
    start, end = preprocess.month_bounds("2026-04")
    code = preprocess.classify(t, start, end, 18, 6)
    names = {c: n for c, n, _ in preprocess.REASONS}
    counts = Counter(names.get(int(c), "kept") for c in code)
    assert counts == {"pickup_missing": 1, "pickup_outside_source_month": 1, "dropoff_missing": 1,
                      "dropoff_before_pickup": 1, "invalid_zone": 2, "invalid_amount": 2,
                      "outside_night_window": 2, "kept": 4}
    assert len(code) == 14


def test_night_boundaries_18_included_06_excluded():
    t = bad_table()
    start, end = preprocess.month_bounds("2026-04")
    code = preprocess.classify(t, start, end, 18, 6)
    kept_hours = sorted(pc.hour(t["pickup_ts"].filter(pa.array(code == 0))).to_pylist())
    assert kept_hours == [0, 5, 18, 23]


def test_null_passenger_count_is_kept():
    t = bad_table()
    t = t.set_column(4, "passenger_count", pa.array([None] * t.num_rows, type=pa.int32()))
    start, end = preprocess.month_bounds("2026-04")
    assert (preprocess.classify(t, start, end, 18, 6) == 0).sum() == 4


def test_non_wrapping_window():
    t = bad_table()
    start, end = preprocess.month_bounds("2026-04")
    code = preprocess.classify(t, start, end, 0, 6)  # 00:00-05:59
    assert sorted(pc.hour(t["pickup_ts"].filter(pa.array(code == 0))).to_pylist()) == [0, 5]


def run(env_setter=None):
    return preprocess.main()


def test_accounting_and_validation(raw_data):
    assert preprocess.main() == 0
    manifest = common.read_manifest()
    total_source = sum(m["source_rows"] for m in manifest["months"])
    assert total_source == 4 * (3000 + 14)
    for m in manifest["months"]:
        assert m["kept"] + sum(m["excluded"].values()) == m["source_rows"]
        assert m["excluded"]["pickup_missing"] >= 1 and m["excluded"]["invalid_zone"] >= 2
    ids = []
    for m in manifest["months"]:
        ids += pq.read_table(common.clean_dir() / common.trip_file(m["month"]), columns=["trip_id"])["trip_id"].to_pylist()
    assert ids == list(range(1, len(ids) + 1))
    # reports
    q = common.quality_dir()
    assert (q / "exclusions.csv").read_text().splitlines()[0] == "source_month,reason,rows,pct_of_source"
    assert len((q / "exclusions.csv").read_text().splitlines()) == 1 + 4 * 7
    assert "FAIL" not in (q / "validation.csv").read_text()


def test_raw_files_are_never_modified(raw_data):
    raw = raw_data / "raw"
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.iterdir()}
    assert preprocess.main() == 0
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.iterdir()} == before
    assert sorted(p.name for p in raw.iterdir()) == sorted(before)  # no extra files in raw/


def test_rerun_reuses_valid_files(raw_data, capsys):
    assert preprocess.main() == 0
    f = common.clean_dir() / "yellow_tripdata_2026-04.parquet"
    m1 = f.stat().st_mtime_ns
    capsys.readouterr()
    assert preprocess.main() == 0
    assert "reusing them" in capsys.readouterr().out and f.stat().st_mtime_ns == m1


@pytest.mark.parametrize("change", [{"NIGHT_END": "5"}, {"NIGHT_START": "20"}, {"TOTAL_MONTHS": "3", "SIZES": "1,2,3"}])
def test_changed_settings_never_reuse_old_files(raw_data, env, change, capsys):
    assert preprocess.main() == 0
    fp1 = common.read_manifest()["fingerprint"]
    env(**change)
    capsys.readouterr()
    assert preprocess.main() == 0
    out = capsys.readouterr().out
    assert "rebuilding" in out and common.read_manifest()["fingerprint"] != fp1
    cfg = common.get_config()
    for m in common.read_manifest()["months"]:
        assert m["kept"] == pq.read_metadata(common.clean_dir() / common.trip_file(m["month"])).num_rows
    hours = set(pc.hour(pq.read_table(common.clean_dir() / "yellow_tripdata_2026-04.parquet")["pickup_ts"]).to_pylist())
    from night import night_hours
    assert hours <= set(night_hours(cfg.night_start, cfg.night_end))


def test_changed_raw_content_triggers_rebuild(raw_data, capsys):
    assert preprocess.main() == 0
    fp1 = common.read_manifest()["fingerprint"]
    p = raw_data / "raw" / "yellow_tripdata_2026-05.parquet"
    t = pq.read_table(p).slice(0, 2500)   # a different file with the same name
    pq.write_table(t, p)
    capsys.readouterr()
    assert preprocess.main() == 0
    assert common.read_manifest()["fingerprint"] != fp1 and "rebuilding" in capsys.readouterr().out


def test_force_rebuild_and_broken_manifest(raw_data, env, capsys):
    assert preprocess.main() == 0
    env(FORCE_REBUILD="1")
    capsys.readouterr()
    assert preprocess.main() == 0 and "rebuilding" in capsys.readouterr().out
    env(FORCE_REBUILD="0")
    common.manifest_path().write_text("{not json")
    assert preprocess.main() == 0 and common.read_manifest()["fingerprint"]


def test_missing_raw_file_is_reported(raw_data, capsys):
    (raw_data / "raw" / "yellow_tripdata_2026-06.parquet").unlink()
    assert preprocess.main() == 1 and "2026-06" in capsys.readouterr().out


def test_validation_detects_tampering(raw_data):
    assert preprocess.main() == 0
    manifest, cfg = common.read_manifest(), common.get_config()
    f = common.clean_dir() / "yellow_tripdata_2026-05.parquet"
    t = pq.read_table(f)
    pq.write_table(t.slice(0, t.num_rows - 5), f)      # lose 5 rows
    checks = preprocess.validate(manifest, cfg)
    failed = [c for c, ok, _ in checks if not ok]
    assert any("clean rows = kept" in c for c in failed)
    # a clean file with a hole in the ids
    ids = t["trip_id"].to_pylist(); ids[10] += 1000
    pq.write_table(t.set_column(0, "trip_id", pa.array(ids, type=pa.int64())), f)
    assert any("contiguous" in c for c, ok, _ in preprocess.validate(manifest, cfg) if not ok)


def test_fingerprint_is_deterministic_and_sensitive(env):
    cfg = common.get_config()
    raw = [{"name": "a", "sha256": "1"}]
    assert common.fingerprint(cfg, raw, "z") == common.fingerprint(cfg, raw, "z")
    assert common.fingerprint(cfg, raw, "z") != common.fingerprint(cfg, [{"name": "a", "sha256": "2"}], "z")
    assert common.fingerprint(cfg, raw, "z") != common.fingerprint(cfg, raw, "y")
