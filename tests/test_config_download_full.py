import urllib.request

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import build_full_dataset
import common
import download_data
from synth import make_raw


def test_config_validation(env):
    env(SIZES="1,2,9", TOTAL_MONTHS="4")
    with pytest.raises(ValueError):
        common.get_config()
    env(SIZES="2,1")
    with pytest.raises(ValueError):
        common.get_config()
    env(SIZES="1,2,4", START_MONTH="2026-13")
    with pytest.raises(ValueError):
        common.get_config()
    env(START_MONTH="2026-04", NIGHT_START="6", NIGHT_END="6")
    with pytest.raises(ValueError):
        common.get_config()


def test_settings_are_read_at_call_time(env):
    env(NIGHT_END="5")
    assert common.get_config().night_end == 5
    env(NIGHT_END="7")
    assert common.get_config().night_end == 7


def test_month_list_crosses_year_end(env):
    assert common.month_list(4, "2026-11") == ["2026-11", "2026-12", "2027-01", "2027-02"]


def test_existing_files_are_used_without_network(raw_data, monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("network must not be used")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    sizes_before = {p.name: p.stat().st_mtime_ns for p in (raw_data / "raw").iterdir()}
    assert download_data.main() == 0
    out = capsys.readouterr().out
    assert out.count("using it, no download") == 5
    assert {p.name: p.stat().st_mtime_ns for p in (raw_data / "raw").iterdir()} == sizes_before


def test_damaged_existing_file_stops_and_is_not_touched(raw_data, monkeypatch, capsys):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no net")))
    bad = raw_data / "raw" / "yellow_tripdata_2026-05.parquet"
    bad.write_bytes(b"not a parquet file")
    assert download_data.main() == 1
    assert "cannot be read" in capsys.readouterr().out and bad.read_bytes() == b"not a parquet file"


def test_missing_file_download_failure_is_clear(raw_data, monkeypatch, capsys):
    (raw_data / "raw" / "yellow_tripdata_2026-07.parquet").unlink()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert download_data.main() == 1
    out = capsys.readouterr().out
    assert "could not download yellow_tripdata_2026-07.parquet" in out and "put it in" in out
    assert not list((raw_data / "raw").glob("*.part"))


def test_wrong_zone_header(raw_data):
    (raw_data / "raw" / "taxi_zone_lookup.csv").write_text("a,b,c\n1,2,3\n")
    assert download_data.main() == 1


def test_full_dataset_keeps_every_trip_and_column(raw_data, env):
    # month 2 gets an extra column and a different type for passenger_count
    p = raw_data / "raw" / "yellow_tripdata_2026-05.parquet"
    t = pq.read_table(p)
    t = t.append_column("cbd_congestion_fee", pa.array([0.75] * t.num_rows, type=pa.float64()))
    t = t.set_column(t.column_names.index("passenger_count"), "passenger_count",
                     pa.array([None if v is None else int(v) for v in t["passenger_count"].to_pylist()], type=pa.int64()))
    pq.write_table(t, p)
    originals = {f.name: pq.read_table(f) for f in (raw_data / "raw").glob("*.parquet")}
    hashes = {f.name: common.sha256_file(f) for f in (raw_data / "raw").glob("*.parquet")}
    assert build_full_dataset.main() == 0
    out = next((raw_data / "full").glob("*.parquet"))
    full = pq.read_table(out)
    assert full.num_rows == sum(o.num_rows for o in originals.values())
    assert set(full["source_month"].to_pylist()) == {"2026-04", "2026-05", "2026-06", "2026-07"}
    assert "cbd_congestion_fee" in full.column_names and full["cbd_congestion_fee"].null_count == full.num_rows - originals["yellow_tripdata_2026-05.parquet"].num_rows
    # every original timestamp, in the original order, is unchanged
    for month in ("2026-04", "2026-05", "2026-06", "2026-07"):
        mask = pa.compute.equal(full["source_month"], month)
        orig = originals[f"yellow_tripdata_{month}.parquet"]
        assert full.filter(mask)["tpep_pickup_datetime"].to_pylist() == orig["tpep_pickup_datetime"].to_pylist()
        assert full.filter(mask)["tpep_dropoff_datetime"].to_pylist() == orig["tpep_dropoff_datetime"].to_pylist()
    assert "FAIL" not in (common.quality_dir() / "full_dataset_check.csv").read_text()
    # raw files unchanged
    assert {f.name: common.sha256_file(f) for f in (raw_data / "raw").glob("*.parquet")} == hashes


def test_full_dataset_verification_detects_a_changed_value(raw_data, env):
    assert build_full_dataset.main() == 0
    out = next((raw_data / "full").glob("*.parquet"))
    t = pq.read_table(out)
    fares = t["fare_amount"].to_pylist(); fares[0] = (fares[0] or 0) + 99.0
    pq.write_table(t.set_column(t.column_names.index("fare_amount"), "fare_amount", pa.array(fares)), out)
    paths = {m: raw_data / "raw" / f"yellow_tripdata_{m}.parquet" for m in ("2026-04", "2026-05", "2026-06", "2026-07")}
    uni, schemas = build_full_dataset.unified_schema(list(paths.values()))
    rows = build_full_dataset.verify(out, paths, schemas, uni)
    assert any(r[2] == "FAIL" for r in rows)
