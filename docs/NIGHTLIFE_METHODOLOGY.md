# Nightlife geography — methodology

Produced by `loader/nightlife_metrics.py`. Used by the map (Matteo) and integrated in the existing
Streamlit app (Sofia). This exporter does **not** change the benchmark pipeline (`preprocess.py`,
databases, Q1–Q5): it reads the original monthly TLC files on its own.

## Source and period

- Input: `data/raw/yellow_tripdata_YYYY-MM.parquet` for the configured months (default `START_MONTH=2026-04`,
  `TOTAL_MONTHS=4`, so April–July 2026) and `data/raw/taxi_zone_lookup.csv`. Raw files are only read, never changed.
- Columns used: `tpep_pickup_datetime`, `tpep_dropoff_datetime`, `PULocationID`, `DOLocationID`.
- Files are read in batches of 500,000 rows with PyArrow (only these 4 columns), so memory stays low.

## Time

- TLC timestamps are New York local clock time without a time zone and are used as they are. If a file
  ever contains time-zone-aware values, they are converted to `America/New_York` first.
- Night: hour ≥ 18 or hour < 6 (18:00:00 included, 06:00:00 excluded). Fixed by the data contract,
  not by the `NIGHT_START`/`NIGHT_END` benchmark settings.
- `night_date` = the date of the evening. Events at 00:00–05:59 belong to the previous day
  (Saturday 01:00 is Friday night).
- Weekend nights: `night_date` is a Friday or a Saturday (even if the event is on Saturday or Sunday
  morning). All other nights are weekday nights.

## Independent events

Every trip produces two independent events, each classified by **its own** timestamp and zone:

| Event | Time | Zone |
|---|---|---|
| pickup | `tpep_pickup_datetime` | `PULocationID` |
| drop-off | `tpep_dropoff_datetime` | `DOLocationID` |

So a trip gives 0, 1 or 2 night events. Example: 17:55 → 18:15 counts only as an evening arrival.
The old pipeline selected trips by pickup time first and lost these arrivals.

## KPIs (zone_metrics.csv)

| Column | Definition |
|---|---|
| `evening_arrivals` | drop-offs 18:00–23:59 |
| `late_departures` | pickups 00:00–04:59 |
| `late_arrivals` | drop-offs 00:00–04:59 |
| `avg_*` | count ÷ `nights` of that night type (not ÷ trips) |
| `nightlife_score` | `late_departures / late_arrivals`; **empty cell** when `late_arrivals = 0` (never infinity, never an invented 0) |
| `enough_trips` | `True` if `late_departures + late_arrivals ≥ 100` and `late_arrivals ≥ 1` (threshold: `--min-events` or env `MIN_TRIPS`, same default as the dashboard) |
| `excluded_reason` | one value per row, in this priority: `unknown zone` (borough `Unknown`/`N/A` in the lookup, IDs 264–265), `airport` (IDs 1 Newark, 132 JFK, 138 LaGuardia), `low volume` (`enough_trips = False`), otherwise empty |

05:00–05:59 is counted in `hourly.csv` (night activity) but not in the late KPIs.

`night_type` is `weekday`, `weekend` or `all`; `all` is computed once from the events
(= weekday + weekend), so nothing is counted twice. `hourly.csv` has every zone × night type × night
hour (18–23, 0–5), zeros included.

## Complete nights (denominator)

The monthly files are organised by **pickup** month: they contain every pickup in
[first day of the first month 00:00, first day after the last month 00:00). Night D lasts from
D 18:00 to D+1 06:00, so only nights fully inside that interval are complete:

- first complete night: the first day of the first month (the night before it misses its evening);
- last complete night: two days before the end (the last night of the last month continues into a
  month that was not loaded).

For April–July 2026: nights from 2026-04-01 to 2026-07-30, i.e. 121 nights (87 weekday, 34 weekend).
Events of incomplete nights are not counted; their number is recorded in `validation.json`.
`nights` is the same for every zone of a given night type. A night with no taxi data at all
would still be counted as a night (none is expected in the TLC files).

## Invalid rows

Trip rules, same names and order as `preprocess.py` (the first that applies wins); a trip that fails
one gives no event: `pickup_missing`, `pickup_outside_source_month`, `dropoff_missing`,
`dropoff_before_pickup`. Amount rules (fares, distance) are **not** applied: they say nothing about
where and when a taxi stopped.

Zone rule, per event: a location ID that is null, < 1 or not in `taxi_zone_lookup.csv` gives no
event for that side only (the other side of the trip can still count).

All counts (raw rows, valid trips, invalid trips by reason, invalid zone events, events counted,
events outside complete nights, excluded zones) are in `results/nightlife/validation.json`.

## Known limits

- Yellow taxis only: not all NYC travel (no green taxis, ride-hailing apps, subway, walking).
- A taxi zone is a TLC area, not an exact address; a high score is a mobility signal, not proof
  of bar or club visits.
- Generated CSVs are in `results/`, which Git ignores: they are not committed.

## Run

```
python loader/nightlife_metrics.py                 # months from .env
python loader/nightlife_metrics.py --help
python -m pytest tests/test_nightlife_metrics.py -q
```
