import pytest

import night
import queries
from stats import describe, percentile


def test_label_uses_last_included_minute():
    assert night.label(18, 6) == "18:00-05:59 (12 hours)"
    assert night.label(0, 6) == "00:00-05:59 (6 hours)"
    assert night.label(20, 24) == "20:00-23:59 (4 hours)"
    assert night.label(22, 0) == "22:00-23:59 (2 hours)"


def test_hours_wrap_and_no_wrap():
    assert night.night_hours(18, 6) == [18, 19, 20, 21, 22, 23, 0, 1, 2, 3, 4, 5]
    assert night.night_hours(0, 6) == [0, 1, 2, 3, 4, 5]
    assert not night.in_night(6, 18, 6) and night.in_night(5, 18, 6) and night.in_night(18, 18, 6)


@pytest.mark.parametrize("start,end", [(6, 6), (0, 24), (24, 6), (18, 25), (-1, 5)])
def test_invalid_windows(start, end):
    with pytest.raises(ValueError):
        night.validate_window(start, end)


def test_hour_order_sql():
    assert night.hour_order_sql("hour", 0, 6) == "hour"
    assert "CASE WHEN hour >= 18" in night.hour_order_sql("hour", 18, 6)


@pytest.mark.parametrize("start,end,expected_end", [
    (18, 6, "2026-04-16 06:00:00"), (20, 4, "2026-04-16 04:00:00"), (0, 6, "2026-04-15 06:00:00"),
    (22, 24, "2026-04-16 00:00:00")])
def test_q5_end_follows_settings(monkeypatch, start, end, expected_end):
    monkeypatch.setenv("NIGHT_START", str(start)); monkeypatch.setenv("NIGHT_END", str(end))
    monkeypatch.setenv("START_MONTH", "2026-04")
    first, last = queries.q5_range()
    assert first == f"2026-04-15 {start:02d}:00:00" and last == expected_end
    assert last in queries.queries()["Q5"]


def test_queries_follow_night_window(monkeypatch):
    monkeypatch.setenv("NIGHT_START", "20"); monkeypatch.setenv("NIGHT_END", "4")
    assert "hour >= 20" in queries.queries()["Q1"] and "+ 4" in queries.queries()["Q1"]


def test_all_multirow_queries_have_total_order():
    q = queries.queries()
    assert "ORDER BY trips DESC, z.location_id" in q["Q2"]
    assert "z.borough ASC NULLS LAST" in q["Q3"]


def test_point_ids_exist_in_smallest_size():
    ids = queries.point_ids(1_000_000)
    assert len(ids) == 200 and ids[0] == 1 and max(ids) <= 1_000_000 and len(set(ids)) == 200
    small = queries.point_ids(50)
    assert max(small) <= 50 and min(small) >= 1


def test_stats():
    d = describe([1.0, 2.0, 3.0, 10.0])
    assert d["n"] == 4 and d["median"] == 2.5 and d["mean"] == 4.0 and d["min"] == 1 and d["max"] == 10
    assert d["stdev"] == pytest.approx(4.0824829, rel=1e-6)
    assert describe([5.0])["stdev"] is None
    assert percentile([1, 2, 3, 4, 5], 50) == 3 and percentile([1, 2, 3, 4], 100) == 4
    with pytest.raises(ValueError):
        describe([])
