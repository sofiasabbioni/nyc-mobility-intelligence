# Exclusion rules of the cleaned night-trip dataset

The databases do **not** contain the original data. They contain a **cleaned subset**: valid trips
whose pickup is in the night window. The original files are never changed, and the optional
combined dataset (`build_full_dataset.py`) keeps every trip.

Every source row is counted in **exactly one** bucket: `kept`, or the **first** reason below that
applies (priority order). Therefore `kept + sum(excluded) = source rows`, per month, and this is
verified (`results/data_quality/validation.csv`).

| Priority | Reason (as in `exclusions.csv`) | Rule |
|---|---|---|
| 1 | `pickup_missing` | `tpep_pickup_datetime` is NULL |
| 2 | `pickup_outside_source_month` | pickup is not inside the calendar month of the file it comes from (`[first day 00:00, first day of next month 00:00)`) |
| 3 | `dropoff_missing` | `tpep_dropoff_datetime` is NULL |
| 4 | `dropoff_before_pickup` | dropoff is earlier than pickup |
| 5 | `invalid_zone` | `PULocationID` or `DOLocationID` is NULL or smaller than 1 |
| 6 | `invalid_amount` | `trip_distance`, `fare_amount`, `tip_amount` or `total_amount` is NULL, NaN or negative |
| 7 | `outside_night_window` | the trip is otherwise valid but the hour of its pickup is not in the night window |

Not an exclusion reason: `passenger_count` NULL (kept as NULL), zero-distance trips, zero fares, very long
trips, outliers. We apply **no** outlier rule: that would be an analysis choice, not a validity rule.

## Night window

Hour of the pickup time, New York local time (the TLC stores local time, no conversion is done).
`NIGHT_START=18`, `NIGHT_END=6` means hours 18, 19, ..., 23, 0, 1, ..., 5: from **18:00:00 to 05:59:59**,
12 hours. `NIGHT_END` is exclusive, so a pickup at 06:00:00 is **not** a night trip and one at 18:00:00 is.
A trip belongs to the night of its **pickup**, even if the ride ends after 06:00.

## Identifiers

`trip_id` = 1, 2, 3, ... assigned after cleaning, in month order and then in the row order of the source
file. It is not a TLC field. Both databases load the same clean files, so the same trip has the same id.
It is validated to be unique, without gaps and increasing by 1 (per file and across files).

## Where the numbers are

`results/data_quality/`: `raw_files.csv` (size, SHA-256, rows, columns of every original file),
`exclusion_rules.csv`, `exclusions.csv` (rows by reason and source month), `preprocess_summary.csv`
(source, kept, excluded, unaccounted per month; `unaccounted_rows` must be 0), `validation.csv`
(every check with PASS/FAIL). The same tables are shown in the dashboard tab "Data quality".
