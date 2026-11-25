"""Tests of loader/nightlife_metrics.py on tiny hand-made files (synthetic, NOT real data)."""
import csv
import datetime as dt
import json

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import nightlife_metrics as nm

T = dt.datetime


def write_zones(raw):
    special = {1: ("EWR", "Newark Airport"), 132: ("Queens", "JFK Airport"), 138: ("Queens", "LaGuardia Airport"),
               264: ("Unknown", "N/A"), 265: ("N/A", "Outside of NYC")}
    with open(raw / "taxi_zone_lookup.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["LocationID", "Borough", "Zone", "service_zone"])
        for i in range(1, 266):
            borough, zone = special.get(i, ("Manhattan", f"Zone {i}"))
            w.writerow([i, borough, zone, "Yellow Zone"])


def write_month(raw, month, trips):
    cols = list(zip(*trips)) if trips else [[], [], [], []]
    table = pa.Table.from_arrays(
        [pa.array(cols[0], pa.timestamp("us")), pa.array(cols[1], pa.timestamp("us")),
         pa.array(cols[2], pa.int32()), pa.array(cols[3], pa.int32())],
        names=nm.COLUMNS)
    pq.write_table(table, raw / f"yellow_tripdata_{month}.parquet")


# (pickup, dropoff, PULocationID, DOLocationID). 2026-04-03 is a Friday.
TRIPS = [
    (T(2026, 4, 3, 17, 55), T(2026, 4, 3, 18, 15), 10, 20),   # arrival only (pickup before 18:00)
    (T(2026, 4, 3, 23, 50), T(2026, 4, 4, 0, 20), 30, 40),    # both events, same Friday night
    (T(2026, 4, 4, 5, 55), T(2026, 4, 4, 6, 10), 50, 60),     # pickup only, hour 5: not a late KPI
    (T(2026, 4, 4, 12, 0), T(2026, 4, 4, 12, 25), 70, 80),    # neither event
    (T(2026, 4, 4, 1, 0), T(2026, 4, 4, 1, 20), 40, 30),      # late departure from 40, late arrival in 30
    (T(2026, 4, 7, 1, 0), T(2026, 4, 7, 1, 20), 90, 91),      # Tuesday 01:00 -> Monday night: weekday
    (T(2026, 4, 1, 2, 0), T(2026, 4, 1, 2, 30), 100, 101),    # night of Mar 31: incomplete, not counted
    (T(2026, 4, 30, 22, 0), T(2026, 4, 30, 22, 30), 102, 103),  # night of Apr 30: incomplete, not counted
    (T(2026, 4, 10, 22, 0), T(2026, 4, 10, 21, 0), 110, 111), # dropoff before pickup: no event
    (None, T(2026, 4, 10, 22, 0), 112, 113),                  # pickup missing: no event
    (T(2026, 3, 31, 22, 0), T(2026, 3, 31, 22, 30), 114, 115),  # pickup outside the file month
    (T(2026, 4, 10, 22, 0), T(2026, 4, 10, 22, 30), 999, 0),  # zones not in the lookup: no event
    (T(2026, 4, 10, 1, 0), T(2026, 4, 10, 1, 30), 132, 264),  # airport and unknown zone
]


@pytest.fixture
def out(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    write_zones(raw)
    write_month(raw, "2026-04", TRIPS)
    summary = nm.build_nightlife_metrics(raw, ["2026-04"], tmp_path / "out", min_events=100)
    zm = pd.read_csv(tmp_path / "out" / "zone_metrics.csv")
    hr = pd.read_csv(tmp_path / "out" / "hourly.csv")
    return zm, hr, summary, tmp_path


def row(zm, zone, night_type):
    r = zm[(zm["zone_id"] == zone) & (zm["night_type"] == night_type)]
    assert len(r) == 1
    return r.iloc[0]


def hour(hr, zone, night_type, h):
    r = hr[(hr["zone_id"] == zone) & (hr["night_type"] == night_type) & (hr["hour"] == h)]
    assert len(r) == 1
    return r.iloc[0]


def test_exact_columns(out):
    zm, hr, _, tmp = out
    assert list(zm.columns) == nm.ZONE_METRICS_COLUMNS
    assert list(hr.columns) == nm.HOURLY_COLUMNS
    assert set(zm["night_type"]) == {"weekday", "weekend", "all"}
    assert len(zm) == 265 * 3
    assert sorted(hr["hour"].unique()) == [0, 1, 2, 3, 4, 5, 18, 19, 20, 21, 22, 23]


def test_arrival_only_when_pickup_before_18(out):
    zm, hr, _, _ = out
    assert row(zm, 20, "weekend")["evening_arrivals"] == 1
    assert hour(hr, 20, "weekend", 18)["dropoffs"] == 1
    assert hr[hr["zone_id"] == 10][["pickups", "dropoffs"]].sum().sum() == 0


def test_trip_across_midnight_counts_both_on_friday_night(out):
    zm, hr, _, _ = out
    assert hour(hr, 30, "weekend", 23)["pickups"] == 1
    assert hour(hr, 40, "weekend", 0)["dropoffs"] == 1
    assert row(zm, 40, "weekend")["late_arrivals"] == 1


def test_0555_pickup_is_hourly_but_not_late_kpi(out):
    zm, hr, _, _ = out
    assert hour(hr, 50, "weekend", 5)["pickups"] == 1
    assert row(zm, 50, "weekend")["late_departures"] == 0
    assert hr[hr["zone_id"] == 60]["dropoffs"].sum() == 0      # 06:10 is not night


def test_daytime_trip_gives_nothing(out):
    _, hr, _, _ = out
    assert hr[hr["zone_id"].isin([70, 80])][["pickups", "dropoffs"]].sum().sum() == 0


def test_pickup_and_dropoff_zones_are_independent(out):
    zm, _, _, _ = out
    z40, z30 = row(zm, 40, "weekend"), row(zm, 30, "weekend")
    assert (z40["late_departures"], z40["late_arrivals"]) == (1, 1)
    assert (z30["late_departures"], z30["late_arrivals"]) == (0, 1)
    assert z40["nightlife_score"] == 1.0 and z30["nightlife_score"] == 0.0


def test_after_midnight_belongs_to_previous_evening(out):
    zm, _, _, _ = out
    assert row(zm, 90, "weekday")["late_departures"] == 1      # Tuesday 01:00 = Monday night
    assert row(zm, 90, "weekend")["late_departures"] == 0


def test_zero_denominator_is_blank_and_low_volume(out):
    zm, _, _, _ = out
    r = row(zm, 20, "weekend")
    assert pd.isna(r["nightlife_score"])
    assert not r["enough_trips"]
    assert r["excluded_reason"] == "low volume"


def test_all_is_weekday_plus_weekend(out):
    zm, hr, _, _ = out
    for col in ("evening_arrivals", "late_departures", "late_arrivals"):
        parts = zm[zm["night_type"] != "all"].groupby("zone_id")[col].sum()
        assert parts.equals(zm[zm["night_type"] == "all"].set_index("zone_id")[col])
    for col in ("pickups", "dropoffs"):
        parts = hr[hr["night_type"] != "all"].groupby(["zone_id", "hour"])[col].sum()
        assert parts.sort_index().equals(hr[hr["night_type"] == "all"].set_index(["zone_id", "hour"])[col].sort_index())


def test_nights_and_averages(out):
    zm, _, s, _ = out
    c = s["complete_nights"]
    assert (c["first_night_date"], c["last_night_date"]) == ("2026-04-01", "2026-04-29")
    assert c["all"] == 29 and c["weekday"] + c["weekend"] == 29 and c["weekend"] == 8
    r = row(zm, 40, "weekend")
    assert r["nights"] == 8 and r["avg_late_arrivals"] == round(1 / 8, 4)
    assert row(zm, 40, "all")["nights"] == 29


def test_incomplete_nights_and_invalid_rows_are_not_counted(out):
    zm, _, s, _ = out
    assert row(zm, 101, "all")["late_arrivals"] == 0           # night of Mar 31
    assert row(zm, 103, "all")["evening_arrivals"] == 0        # night of Apr 30
    assert s["night_events_outside_complete_nights"] == {"pickup": 2, "dropoff": 2}
    assert s["invalid_trips"] == {"pickup_missing": 1, "pickup_outside_source_month": 1,
                                  "dropoff_missing": 0, "dropoff_before_pickup": 1}
    assert s["invalid_zone_events"] == {"pickup": 1, "dropoff": 1}
    assert s["raw_rows"] == len(TRIPS)


def test_excluded_reasons(out):
    zm, _, _, _ = out
    assert row(zm, 132, "all")["excluded_reason"] == "airport"
    assert row(zm, 264, "all")["excluded_reason"] == "unknown zone"
    assert row(zm, 265, "all")["excluded_reason"] == "unknown zone"
    assert set(zm["excluded_reason"].dropna()) <= {"unknown zone", "airport", "low volume"}


def test_enough_trips_threshold(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    write_zones(raw)
    write_month(raw, "2026-04", TRIPS)
    nm.build_nightlife_metrics(raw, ["2026-04"], tmp_path / "out", min_events=2)
    r = row(pd.read_csv(tmp_path / "out" / "zone_metrics.csv"), 40, "weekend")
    assert r["enough_trips"] and pd.isna(r["excluded_reason"])


def test_rerun_replaces_outputs(out):
    _, _, _, tmp = out
    before = [(tmp / "out" / f).read_bytes() for f in ("zone_metrics.csv", "hourly.csv")]
    nm.build_nightlife_metrics(tmp / "raw", ["2026-04"], tmp / "out", min_events=100)
    after = [(tmp / "out" / f).read_bytes() for f in ("zone_metrics.csv", "hourly.csv")]
    assert before == after


def test_missing_input_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        nm.build_nightlife_metrics(tmp_path, ["2026-04"], tmp_path / "out")


def test_runs_on_the_synthetic_four_months(raw_data, tmp_path):
    s = nm.build_nightlife_metrics(raw_data / "raw", ["2026-04", "2026-05", "2026-06", "2026-07"],
                                   tmp_path / "nl")
    assert s["complete_nights"]["all"] == (dt.date(2026, 7, 30) - dt.date(2026, 4, 1)).days + 1
    assert s["night_events_counted"]["pickup"] > 0 and s["night_events_counted"]["dropoff"] > 0
    json.loads((tmp_path / "nl" / "validation.json").read_text())
    hr = pd.read_csv(tmp_path / "nl" / "hourly.csv")
    assert hr[hr["night_type"] == "all"]["pickups"].sum() == s["night_events_counted"]["pickup"]
    assert hr[hr["night_type"] == "all"]["dropoffs"].sum() == s["night_events_counted"]["dropoff"]
