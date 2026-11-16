"""The five workloads of the project. The SAME SQL text runs on PostgreSQL and on DuckDB.

Settings are read from the environment when a function is called (NIGHT_START, NIGHT_END,
START_MONTH), so they always agree with the preprocessing settings.

  Q1  average fare and tip by pickup hour       analytical (scan + group by)
  Q2  top 10 pickup zones                       analytical (scan + join + group by)
  Q3  trips per borough                         analytical (scan + join + group by)
  Q4  200 point lookups by trip_id, one after   application-level latency (NOT pure engine time:
      the other                                 it includes 200 client calls, and for PostgreSQL
                                                200 network round trips)
  Q5  all trips of ONE night, by time range     selective range filter (the PostgreSQL index test)

Every query with a result of more than one row has a TOTAL order (ties are broken by an id),
so both engines must return the rows in exactly the same order.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import night  # noqa: E402

TRIP_COLUMNS = ("trip_id, pickup_ts, dropoff_ts, pu_location_id, do_location_id, passenger_count, "
                "trip_distance, fare_amount, tip_amount, total_amount")

DESCRIPTIONS = {
    "Q1": "Average fare and tip by pickup hour",
    "Q2": "Top 10 pickup zones",
    "Q3": "Trips per borough",
    "Q4": "200 sequential point lookups by trip_id (application-level latency)",
    "Q5": "All trips of one night, selected by pickup time range",
}
WORKLOAD = {
    "Q1": "analytical", "Q2": "analytical", "Q3": "analytical",
    "Q4": "lookup_latency", "Q5": "selective_range",
}
ORDER = ["Q1", "Q2", "Q3", "Q4", "Q5"]
N_LOOKUPS = 200


def night_settings() -> tuple[int, int, str]:
    start = int(os.environ.get("NIGHT_START", "18"))
    end = int(os.environ.get("NIGHT_END", "6"))
    night.validate_window(start, end)
    return start, end, os.environ.get("START_MONTH", "2026-04")


def queries() -> dict[str, str]:
    """Q1, Q2, Q3, Q5 as SQL text (Q4 is point_sql, run N_LOOKUPS times)."""
    start, end, start_month = night_settings()
    begin, finish = night.sample_night(start_month, start, end)
    hour_order = night.hour_order_sql("hour", start, end)
    return {
        "Q1": f"""
SELECT hour, trips, avg_fare, avg_tip
FROM (
    SELECT CAST(EXTRACT(hour FROM pickup_ts) AS INTEGER) AS hour,
           COUNT(*)         AS trips,
           AVG(fare_amount) AS avg_fare,
           AVG(tip_amount)  AS avg_tip
    FROM trips
    GROUP BY 1
) AS t
ORDER BY {hour_order}
""",
        "Q2": """
SELECT z.zone AS zone, z.borough AS borough, COUNT(*) AS trips
FROM trips AS t
JOIN zones AS z ON z.location_id = t.pu_location_id
GROUP BY z.location_id, z.zone, z.borough
ORDER BY trips DESC, z.location_id
LIMIT 10
""",
        "Q3": """
SELECT z.borough AS borough, COUNT(*) AS trips
FROM trips AS t
JOIN zones AS z ON z.location_id = t.pu_location_id
GROUP BY z.borough
ORDER BY trips DESC, z.borough ASC NULLS LAST
""",
        "Q5": ("SELECT COUNT(*) AS trips, AVG(fare_amount) AS avg_fare, AVG(tip_amount) AS avg_tip "
               f"FROM trips WHERE pickup_ts >= TIMESTAMP '{begin:%Y-%m-%d %H:%M:%S}' "
               f"AND pickup_ts < TIMESTAMP '{finish:%Y-%m-%d %H:%M:%S}'"),
    }


def q5_range() -> tuple[str, str]:
    start, end, start_month = night_settings()
    begin, finish = night.sample_night(start_month, start, end)
    return f"{begin:%Y-%m-%d %H:%M:%S}", f"{finish:%Y-%m-%d %H:%M:%S}"


def point_ids(smallest_rows: int, n: int = N_LOOKUPS) -> list[int]:
    """Ids spread evenly over the SMALLEST test size, so they exist in every size.
    trip_id runs 1..rows without gaps (validated by preprocess.py)."""
    if smallest_rows < 1:
        raise ValueError("smallest_rows must be at least 1")
    step = max(1, smallest_rows // n)
    return [min(1 + i * step, smallest_rows) for i in range(n)]


def point_sql(trip_id: int) -> str:
    return f"SELECT {TRIP_COLUMNS} FROM trips WHERE trip_id = {int(trip_id)}"
