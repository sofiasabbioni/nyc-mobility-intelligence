# Loader evaluation: Parquet read strategies

Author: Marin Tomasović · Branch: `feature/marin-loader-evaluation` · Date: 17 November 2026

## What this is (and is not)

This is a **Parquet read microbenchmark**. It compares two ways of reading the same TLC
Parquet file with pyarrow:

- `full_read`: `pq.read_table(...)` reads the selected columns of the whole file at once;
- `iter_batches`: `pq.ParquetFile(...).iter_batches(batch_size=100_000)` reads the same
  columns in batches of 100,000 rows.

It measures **only the time to read the file**. It does not measure writing to PostgreSQL,
COPY, schema or index creation, ANALYZE, or the end-to-end pipeline. No conclusion about
full PostgreSQL loading speed follows from these numbers.

Script: `scripts/profile_parquet_reads.py`

## Test setup

| Item | Value |
|---|---|
| Machine | Apple M4, 16 GB RAM |
| OS | macOS 27.0.1 (26A434) |
| Python | 3.12.16 (project `.venv`) |
| pyarrow | 26.0.1 (pinned in `loader/requirements.txt`) |
| File | `data/raw/yellow_tripdata_2026-04.parquet` (official TLC file, 62 MB, downloaded 17 Nov 2026) |
| Rows in file | 3,831,240 |
| Columns read | `tpep_pickup_datetime`, `PULocationID` |
| Batch size (`iter_batches`) | 100,000 rows |
| Repetitions | 3 per method, same file, same machine, same session |
| Run order | all `full_read` runs first, then all `iter_batches` runs |

Command:

```bash
.venv/bin/python scripts/profile_parquet_reads.py data/raw/yellow_tripdata_2026-04.parquet --repeats 3
```

## Results

| Method | Rows | Run 1 (s) | Run 2 (s) | Run 3 (s) | Median (s) |
|---|---|---|---|---|---|
| `full_read` | 3,831,240 | 0.310 | 0.016 | 0.015 | **0.016** |
| `iter_batches` | 3,831,240 | 0.029 | 0.027 | 0.027 | **0.027** |

Row counts: identical for every run and for both methods (script output: `PASS`).

## Interpretation

- On this machine, for these two columns, reading the whole file at once was **faster**
  than reading it in batches (median 0.016 s vs 0.027 s). Both are very fast: reading
  two columns of 3.8 million rows takes a few hundredths of a second.
- Batching adds a small overhead per batch (about 39 batches of 100,000 rows here), so it
  is not expected to make reading faster. Its purpose is to keep **memory** low, because
  only one batch is in memory at a time.
- The first `full_read` run (0.310 s) is much slower than the other runs. The most likely
  reason is a cold start (file not yet in the operating system's cache, first use of the
  library). Because `full_read` ran first, it absorbed this cost, and `iter_batches` ran with
  a warm cache. The median is used so that this one run does not dominate.

## Limitations

- **Memory was not measured.** The main expected benefit of batching (lower peak RAM) is
  therefore not evaluated here.
- Only 2 of the file's columns were read. With all columns, the full read needs much more
  memory, and the time difference may change.
- Times are in milliseconds, so small background activity on the laptop can affect them.
- One file, one machine, 3 repetitions, fixed run order.

## Relation to the team loader

`loader/load_postgres.py` already reads the clean Parquet files with
`iter_batches` (200,000 rows) and sends them to PostgreSQL with `COPY`, and
`loader/preprocess.py` also reads in batches (500,000 rows). The approach of the older
`faster-loader` branch (batched reading + `COPY`) is therefore already part of the team
pipeline. This benchmark does not change that loader.

## How a future controlled loader test could work

To evaluate full PostgreSQL loading fairly (to be agreed with Sofia first):

- use the same cleaned dataset, the same months and the same machine for every variant;
- load into an isolated PostgreSQL schema or database, never the one used by the dashboard;
- keep schema, primary keys and settings identical, and change only one thing at a time
  (for example the batch size);
- measure separately: Parquet reading, CSV conversion, `COPY`, and `ANALYZE`/`COMMIT`;
- record peak memory of the loader and database containers (`scripts/sample_docker_stats.py`);
- repeat each run 3 times, report the mean (and median), and check that row counts match.
