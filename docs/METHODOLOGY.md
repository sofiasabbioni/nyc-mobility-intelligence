# Methodology

Project: *Computational Infrastructures for Data Science*. System: a Dockerized pipeline that loads
NYC yellow taxi trips into a row store (PostgreSQL 16) and a column store (DuckDB) and compares them.
This document states what is measured, how, and what the results can and cannot support. It contains
**no measurements**: all numbers must come from your own `results/` files.

## 1. Research question and scope
*For analytical queries over the night trips of New York yellow taxis, how do a row-oriented client/server
database (PostgreSQL) and an embedded column-oriented database (DuckDB) compare in loading time, query time,
storage, memory and growth with data size, and what does an index change in PostgreSQL?*

Scope limits, stated up front:
* One machine, one run configuration. No claim about other hardware, versions or data.
* Default PostgreSQL configuration (no tuning); DuckDB with default settings (threads and memory limit are
  recorded in `results/environment.txt`).
* The comparison is **system against system**, not storage format against storage format: PostgreSQL is reached through a
  network connection, DuckDB runs inside the benchmark process. Part of any difference is architecture.

## 2. Data
* Source: NYC TLC yellow taxi trip records, monthly Parquet files (`START_MONTH`, `TOTAL_MONTHS`, default 2026-04 to
  2026-07) and `taxi_zone_lookup.csv`.
* **Original files** (`data/raw`): never modified. Their size, SHA-256, row and column counts are recorded
  (`results/data_quality/raw_files.csv`). Files you already have are used without downloading again.
* **Optional complete dataset** (`data/full`, `BUILD_FULL_DATASET=1`): every trip, every original column, original
  timestamps and row order, plus `source_month`. Verified per month and column (non-null count, min, max) and by
  row count against the sources (`full_dataset_check.csv`). It is *not* used by the databases.
* **Cleaned night-trip dataset** (`data/clean`): the data of the experiment. A **subset** of the original data. It must never be
  described as the complete dataset; all tables, charts and captions say "cleaned night trips".
* Night window: pickup hour in [18, 6), New York local time, i.e. 18:00:00 to 05:59:59 (configurable, validated).
* Exclusion rules: `docs/EXCLUSION_RULES.md`. Rows excluded are reported by reason and source month. The accounting identity
  *kept + excluded = source rows* is checked for every month and in total, as is *clean rows = kept*.

### Integrity checks (all automatic, all in `results/data_quality/validation.csv`)
source rows equal the Parquet metadata; kept + excluded = source; `trip_id` unique, contiguous, increasing by 1,
continuing across months; pickup inside its month (date boundaries); dropoff >= pickup; zones >= 1; amounts >= 0;
every pickup hour inside the night window. These checks are written with DuckDB SQL, independently of the pyarrow code
that produced the files. Afterwards, for each test size, **PostgreSQL and DuckDB are compared** with each other and with the
manifest: row count, min/max `trip_id`, distinct ids, `SUM(fare_amount)`, `SUM(total_amount)`, min/max pickup, rows outside
the night hours (`results/db_consistency.csv`).

### Reproducibility of the clean data
`clean/manifest.json` stores a fingerprint: SHA-256 of {rules version, start month, number of months, night window,
SHA-256 of every raw file and of the zone file}. If any of them changes the clean files are rebuilt; both databases store the
fingerprint (`load_meta` table) and the benchmark refuses to run if it does not match the manifest. Old results are
deleted at the start of every benchmark run.

## 3. Test sizes
Sizes are **1, 2 and 4 consecutive months** (`SIZES`). Months do not have equal numbers of trips, so these are *not* exactly
25%, 50% and 100% of the rows. All charts and tables are labelled with the **actual row counts**
(`results/datasets.csv`, with the percentage of the largest test's rows). Smaller sizes are prefixes of the larger one.

## 4. Workloads
| ID | Workload | Class | What it exercises |
|---|---|---|---|
| Q1 | average fare and tip by pickup hour | analytical | full scan of 3 columns, hash aggregation, 12 groups |
| Q2 | top 10 pickup zones | analytical | scan, join with 265-row table, aggregation, top-N |
| Q3 | trips per borough | analytical | scan, join, aggregation |
| Q4 | 200 lookups by `trip_id` | **application-level latency** | 200 sequential statements from the client (PK index in both engines) |
| Q5 | all trips of one night (range on `pickup_ts`) | selective range | 1 of ~120 nights per 4 months; used for the PostgreSQL index experiment |

* The same SQL text runs on both engines. Every multi-row result has a **total ordering** (ties broken by id, `NULLS LAST`), so results
  must be identical row by row.
* **Q4 is not a pure engine measurement.** Each lookup includes Python call overhead, and for PostgreSQL a network round trip. It
  is reported as total time for 200 lookups and as per-lookup latency (mean, median, p95, p99, max; `lookup_latency.csv`), in its own
  section, and is never merged into the analytical averages. The 200 ids are spread evenly over the smallest test size so that
  they exist in every size.
* Q5 is a fixed night (the 15th of the first month, from `NIGHT_START` for as many hours as the window lasts, so its end follows
  `NIGHT_END`). The benchmark refuses a run where it selects no trips.

## 5. Measurement protocol
For each engine and test size, on a fresh connection, for each query in a **seeded shuffled order** (`BENCH_SEED`; the same order for both
engines at a given size; recorded as `query_order`; `BENCH_ORDER=fixed` gives Q1..Q5):

1. one **first** execution;
2. `BENCH_WARMUPS` (default 2) **warm-up** executions;
3. `BENCH_RUNS` (default **5**, configurable) **warm** executions.

Every execution is saved (`benchmark.csv`: engine, months, rows, query, phase, run, query_order, seconds). `benchmark_summary.csv` gives, per
engine/size/query/phase: n, mean, median, sample standard deviation (n-1; empty when n = 1), min, max. The dashboard plots the
median with min-max bars and tables the rest. "First" and "warm" are never averaged together.

### Cold and warm: what "first" does and does not mean
"First" is the first execution of that query on a new connection. **It is not a guaranteed cold start**: the operating-system page cache is
not flushed (a container cannot do that portably), PostgreSQL's shared buffers survive between connections, and queries that ran earlier
in the shuffled order may already have loaded the table pages. Therefore "first" values are reported only as *first-execution* values and
compared mainly within the same query position. "Warm" values follow warm-up executions of the same query on the same connection and are
the primary comparison. A true cold-cache study would need a host-level cache flush and a restart of the database between runs; it is
out of scope and is not claimed.

### Cache and order effects (controlled or documented)
* Query order is seeded and recorded; the same order is used for both engines at a given size.
* Engines are run one after the other (PostgreSQL for all sizes, then DuckDB), never concurrently; the dashboard only reads files and does not query
  databases, so it cannot disturb a run.
* The equivalence check uses the result of the **first timed** execution, so no extra untimed pass warms the caches before the first run.
* Remaining threats: PostgreSQL phase precedes the DuckDB phase (thermal state, other host activity); first runs of the first query in the order absorb
  the warming of the table for the others; PostgreSQL's cache carries over between sizes within the PostgreSQL phase; host noise. These are visible in the
  dispersion columns and must be discussed, not hidden. Repeat the whole benchmark with another `BENCH_SEED` to check that conclusions do not depend on order.

## 6. Correctness gate: equivalence before timings
Timings are written to `benchmark.csv` **only if** all of the following hold; otherwise they go to `rejected_timings.csv`, the exit code is 1 and the dashboard
shows no timings:
* databases carry the fingerprint and row count of the manifest;
* for every size and query, the first result on PostgreSQL equals the first result on DuckDB, row by row and in order (floats with relative tolerance 1e-9, because
  summation order differs; Q4 compares all 200 full rows; the indexed Q5 is compared too) - `equivalence.csv`;
* Q5 selects at least one trip;
* every row-level consistency check (section 2) passes.

## 7. Index experiment (PostgreSQL only)
Baseline PostgreSQL has primary keys only (so Q4 is index-backed in both engines). For each size, after the baseline runs: `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` of Q5 without
the index; `CREATE INDEX idx_trips_pickup_ts ON trips (pickup_ts)` + `ANALYZE` (build time recorded); `EXPLAIN ANALYZE` again; then the same protocol for Q5 as engine
`postgres_indexed`. The plan JSON is stored (`results/explain/`) and `explain_q5.csv` records the plan nodes and whether any index node is present. **Timings
are described as an index effect only if the plan with the index uses it**; the dashboard warns otherwise. The index is dropped afterwards.
DuckDB is not indexed beyond its primary key (it relies on its own min/max statistics per row group, which we do not control).

## 8. Definitions of what is measured
| Quantity | Definition | Where |
|---|---|---|
| **Loading time** | wall-clock seconds, identical definition for both engines, from "files verified" to "data durable and statistics ready": create tables with primary keys + load zones + load all trips + `ANALYZE` (PostgreSQL) / `CHECKPOINT` (DuckDB) + commit. Excludes removing the previous copy and the verification queries that follow. One measurement per size (no repetition): treat differences with caution. PostgreSQL's figure includes decoding Parquet in Python and serialising to CSV for `COPY`; DuckDB reads Parquet natively. | `load_times.csv` |
| **Query time** | wall-clock seconds of execute + fetch of all rows in the benchmark process (`perf_counter`). PostgreSQL includes the client/server round trip; DuckDB is in-process. | `benchmark.csv` |
| **Q4 latency** | seconds for 200 sequential lookups, and per-lookup milliseconds. Application-level, not engine-only. | `benchmark.csv`, `lookup_latency.csv` |
| **Storage** | PostgreSQL: `pg_total_relation_size('trips') + pg_total_relation_size('zones')` (heap, TOAST, indexes), plus heap and index parts separately; with the extra index also. DuckDB: size of the database file after `CHECKPOINT`. Parquet: sum of the file sizes of the same months (original = all trips and all columns; clean = night trips, 10 columns). Original and databases are **not** comparable like for like. | `disk_sizes.csv` |
| **Peak memory** | maximum of `docker stats` memory samples (about every 0.5 s) of the whole container during the engine's time window (`phases.csv`): `db` container for PostgreSQL, `benchmark` container for DuckDB (it includes the Python process and its fetched results). Samples can miss short peaks; shared buffers and page cache effects are part of the number. Empty if there are no samples. Not measured by the benchmark itself. | `memory_summary.csv` |
| **Growth** | the same quantities for the three sizes against the **actual row counts**; no extrapolation beyond the tested range. | all |

## 9. What the results can support
* Descriptive statements such as "on this machine, in this run, the warm median of Q2 on the largest test was X s in PostgreSQL and Y s in DuckDB (ratio Z)".
* Statements about growth between the tested sizes, with the dispersion shown.
* Statements about the effect of the PostgreSQL index **if** the plan verifies its use.

What they cannot support: general statements that one system is "faster" or "better"; statements about other hardware, data sizes or configurations; cold-start performance;
pure engine speed for Q4; storage compression claims against the original files; any statement about trips outside the night window.
The conclusion section of the report should be written from the actual `results/` files, with the limitations of sections 1 and 5 repeated.

## 10. Suggested expectation (hypothesis, not a result)
Column stores are designed for scans and aggregations over few columns, row stores for selective access through indexes and transactional workloads. This motivates
the hypothesis that DuckDB is faster on Q1-Q3 and that the PostgreSQL index helps Q5. The experiment is built to be able to contradict it.
