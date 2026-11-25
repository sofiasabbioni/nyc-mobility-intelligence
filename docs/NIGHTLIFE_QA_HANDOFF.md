# Nightlife metrics — QA handoff

## Scope

This document checks how the nightlife metrics are calculated in `loader/nightlife_metrics.py` (the same script is also called by `loader/run_loader.py`) and whether the tests cover the tricky cases. The code was compared with `docs/NIGHTLIFE_METHODOLOGY.md`, three edge cases were picked from the existing tests, and the tests were run locally.

The last section explains how to read the results, since a map built on taxi data can easily be taken to say more than it does.

## Data contract verified in code

- Input: monthly NYC TLC yellow-taxi Parquet files (`data/raw/yellow_tripdata_YYYY-MM.parquet`) and the taxi-zone lookup (`data/raw/taxi_zone_lookup.csv`). Only four columns are read: `tpep_pickup_datetime`, `tpep_dropoff_datetime`, `PULocationID`, `DOLocationID`.
- Night interval: 18:00 included to 06:00 excluded (NYC local clock time).
- Pickup and drop-off are independent events: a ride can contribute 0, 1, or 2 night events.
- Night date attributes events after midnight to the previous evening. Weekend nights are those whose night date is a Friday or a Saturday.

## Edge cases verified

The three cases below come from the small synthetic fixture in `tests/test_nightlife_metrics.py`. The trips are hand-made, not real TLC data, and 2026-04-03 is a Friday.

1. **A trip that crosses midnight** — A taxi picks someone up on Friday 2026-04-03 at 23:50 in zone 30 and drops them off on Saturday at 00:20 in zone 40. Expected result: even though the calendar date changes, both events are counted on the same Friday night, which is a `weekend` night. Zone 30 gets 1 pickup at hour 23, zone 40 gets 1 drop-off at hour 0, and because 00:20 falls in the late window, zone 40 also gets `late_arrivals = 1`. Existing test: `test_trip_across_midnight_counts_both_on_friday_night` (`tests/test_nightlife_metrics.py`).

2. **A pickup just before the night ends** — A taxi leaves zone 50 on Saturday at 05:55 and arrives in zone 60 at 06:10. Expected result: the pickup is still night activity, so it appears in `hourly.csv` (zone 50, hour 5), but it is not a late departure, because the late KPIs only cover 00:00–04:59 (`late_departures = 0`). The drop-off at 06:10 is after the night window, so zone 60 gets no drop-off at all. Existing test: `test_0555_pickup_is_hourly_but_not_late_kpi` (`tests/test_nightlife_metrics.py`).

3. **A zone with nothing to divide by** — Zone 20 only receives one evening drop-off, from a trip that was picked up at 17:55 (before the night starts, so only the arrival counts), and it has no late arrivals. Since `nightlife_score` is `late_departures / late_arrivals`, the score cannot be computed. Expected result: the cell is left empty, rather than showing 0 or infinity, and the zone is marked `enough_trips = False` with `excluded_reason = "low volume"`. Existing test: `test_zero_denominator_is_blank_and_low_volume` (`tests/test_nightlife_metrics.py`).

## How to validate

- Read `tests/test_nightlife_metrics.py` and `tests/test_nightlife_pipeline.py`.
- Command: `python -m pytest tests/test_nightlife_metrics.py tests/test_nightlife_pipeline.py -q`
- Outcome: **25 passed** in 11.9 s (run locally on macOS, Python 3.12, in a `.venv` created from `requirements-dev.txt`).

## Interpretation and limits

These metrics should be read as a taxi-based proxy for nightlife. They show where and when yellow taxis pick people up and drop them off at night, zone by zone, and how weekday nights compare with weekend nights. That is useful for spotting which areas are busy after dark, but it is only one slice of how people move around the city.

They do not measure how many people are out, the population of a zone, or nightlife activity as a whole. Only yellow taxis are included, so green taxis, ride-hailing apps, the subway and walking are all missing. A taxi zone is a TLC area, not an exact address, and a drop-off tells us where a trip ended, not why. Zones flagged `low volume`, `airport` or `unknown zone` should not be ranked against the others.

The main interpretive limitation concerns the score itself. A high `nightlife_score` only means that more taxis leave a zone than arrive there late at night. This can also happen near transport hubs or in areas where many people finish night shifts, so the score on its own is a mobility signal, not proof that a zone has bars or clubs.

## Handoff

The exporter writes its output to `results/nightlife/`, which Git ignores, so these files are never committed. The map (`app/nightlife_map.py`) reads the two CSVs:

- `zone_metrics.csv` — one row per zone and `night_type` (`weekday`, `weekend`, `all`). Columns: `zone_id`, `zone`, `borough`, `night_type`, `nights`, `evening_arrivals`, `late_departures`, `late_arrivals`, `avg_evening_arrivals`, `avg_late_departures`, `avg_late_arrivals`, `nightlife_score`, `enough_trips`, `excluded_reason`.
- `hourly.csv` — every zone × night type × night hour (18–23, 0–5), zeros included. Columns: `zone_id`, `night_type`, `hour`, `pickups`, `dropoffs`.

A third file, `validation.json`, sits in the same folder and records a summary of the run: the months processed, the number of complete nights and how many rows were excluded and why. The map does not read it.
