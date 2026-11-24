# Benchmark rigor: query plans, parallelism and repeatability for Q1-Q3

Author: Marin Tomasović · Branch: `feature/marin-benchmark-rigor` · Tool: `benchmark/rigor_study.py` ·
Tests: `tests/test_benchmark_rigor.py`

**Status: the study tool is built and tested on fake and synthetic data. The study itself has NOT been run on
the real databases yet.** Every statement about causes below is a hypothesis until that run is done, with
Sofia's approval, on the machine of the original benchmark.

This is a supplement to `benchmark/benchmark.py`. It does not change `benchmark.py`, the SQL in
`sql/queries.py`, the equivalence gate, Q4, Q5 or any existing result file.

## (a) The original configuration and what its results can and cannot say

The main benchmark (`docs/METHODOLOGY.md`) runs the same Q1-Q5 SQL on PostgreSQL 16 and DuckDB for 1, 2 and 4
months; for each size it runs PostgreSQL first, then DuckDB, in a seeded query order, with 1 first, 2 warm-up and
5 warm executions, and accepts timings only if both engines return identical results.

Its limits, relevant to Q1-Q3:

- **One session.** The 5 warm runs are taken in one benchmark session, so they show run-to-run spread inside a
  session, not between sessions.
- **Fixed engine order.** PostgreSQL always runs before DuckDB, so order effects (cache, thermal state, other host
  activity) are not separated from engine effects.
- **Parallelism is not controlled.** Each engine uses its default. On Marin's local run of the main benchmark
  (19 Nov 2026, Apple M4, Docker Desktop with 10 CPUs and 7.7 GB), `results/environment.txt` records PostgreSQL
  `max_parallel_workers_per_gather = 2`, `work_mem = 4MB`, `shared_buffers = 128MB`, and DuckDB
  `threads = 10`. PostgreSQL can therefore use at most 3 processes per query (2 workers + the leader), while
  DuckDB may use 10 threads.
- **No plans for Q1-Q3.** `EXPLAIN ANALYZE` is recorded only for Q5 (the index experiment).
- **System-level comparison.** PostgreSQL is reached over a client/server connection; DuckDB runs inside the
  benchmark's Python process.

For context only, the warm medians of Q1-Q3 at 4 months (6,658,475 cleaned night trips) in Marin's local
19 Nov run of the main benchmark (status `complete`, 18/18 equivalence checks PASS):

| Query | PostgreSQL warm median (ms) | DuckDB warm median (ms) | Ratio |
| --- | --- | --- | --- |
| Q1 average fare and tip by hour | 811.5 | 7.1 | 114x |
| Q2 top 10 pickup zones | 336.9 | 18.5 | 18x |
| Q3 trips per borough | 351.8 | 6.9 | 51x |

These come from a **different machine** than the team's 15 November benchmark (where a Q1 ratio of about 85x was
reported), so the two must not be combined as one experiment. The gap is largest for Q1, which is the query the
Q1-planner hypothesis is about.

## (b) Hypothesis 1: the PostgreSQL planner misjudges Q1

Q1 groups by an expression, `CAST(EXTRACT(hour FROM pickup_ts) AS INTEGER)`. PostgreSQL has no statistics on an
expression unless extended statistics or an expression index exist, so it may estimate many more groups than the 12
night hours that exist. An external audit suggested this could lead to a worse aggregate strategy or fewer workers.

**What would confirm it:** in the PostgreSQL plan for Q1, the aggregate node's estimated rows (`Plan Rows`) far
above the actual groups (12), together with a strategy or worker count that differs from Q2/Q3, whose grouping keys
are plain columns.

**What would refute it:** estimates close to 12, or the same strategy and workers as Q2/Q3, with the time spent
mainly in the scan.

The tool records, per plan: estimated vs actual rows of the top aggregate and their ratio, every aggregate's
strategy and partial mode, `Workers Planned` and `Workers Launched`, scan nodes, planning, execution and JIT time.
Note that PostgreSQL's JIT is on by default; its time is part of the execution and is recorded separately.

## (c) Confounders: parallelism, order and caching

- **Parallelism.** The study adds labelled variants beside the engine defaults: DuckDB `threads` 1 and 3, and
  PostgreSQL `max_parallel_workers_per_gather` 0 and 2 (all session-local, set when the connection opens). With
  DuckDB at 3 threads and PostgreSQL at 2 workers, both may use up to 3 CPUs. **This does not prove equal CPU use:**
  the PostgreSQL planner may launch fewer workers than allowed, which is why actual workers are read from the plans.
  On the 19 Nov local configuration, `pg_workers_2` equals PostgreSQL's default.
- **Order.** Each timing session has its own seeded query order (seed + session number), and the engine order
  alternates: session 1 PostgreSQL first, session 2 DuckDB first, session 3 PostgreSQL first.
- **Caching.** "First" means the first execution on a new connection. It is **not** a cold-cache measurement: the
  operating-system page cache is not flushed and PostgreSQL's shared buffers survive between connections. Warm
  executions are the main comparison.
- **Between-session variation.** The tool reports the warm median of each session and the range between sessions as a
  percentage of the lowest median.

## (d) Protocol for the real run (after Sofia's approval)

1. **Where:** preferably on the machine of the 15 November benchmark, so the results stay comparable to it. Absolute
   times from different machines are never combined.
2. **Preconditions:** the `db` service is running with schemas `m1`, `m2`, `m4`; the DuckDB files and
   `clean/manifest.json` exist in the data volume. The tool checks fingerprint and row count of every schema and file
   before it runs, and stops if anything does not match. It never creates, loads or repairs data.
3. **Check first, no database access.** The benchmark image must contain the new file, so build that one image
   first (this only builds; it starts nothing):
   ```
   docker compose build benchmark
   docker compose run --rm --no-deps benchmark python rigor_study.py --help
   docker compose run --rm --no-deps benchmark python rigor_study.py --dry-run --stage all
   ```
   `--no-deps` makes sure the loader is not started. The normal benchmark command (`python benchmark.py`), the full
   `docker compose up` and `benchmark.main()` are never used for this study.
4. **Stage A, baseline and variant plans** (54 `EXPLAIN ANALYZE` executions at the default settings):
   ```
   docker compose run --rm --no-deps benchmark python rigor_study.py --confirm-real-run --stage plans
   ```
5. **Stages B and C, timings** (3 sessions; 1,296 executions at the default settings):
   ```
   docker compose run --rm --no-deps benchmark python rigor_study.py --confirm-real-run --stage timings --output-dir /results/marin_rigor_timings_YYYYMMDD
   ```
6. **Outputs** go to a new folder, by default `/results/marin_rigor_YYYYMMDD`; an existing folder is refused, and the
   main benchmark's files are never written. Each run writes `protocol.json` (settings, schedule, engine settings per
   variant, versions, caveats, status).

| File | Content |
| --- | --- |
| `plans/pg_<size>m_<query>_<variant>.json` | full PostgreSQL plan |
| `plans/duckdb_<size>m_<query>_<variant>.txt` | full DuckDB `EXPLAIN ANALYZE` output |
| `plan_summary.csv` | one row per plan: execution time, node types, workers planned/launched, aggregate estimate vs actual |
| `rigor_timings.csv` | every execution: session, seed, engine position, variant, size, query, phase, seconds |
| `rigor_timing_summary.csv` | n, mean, median, standard deviation, min, max per engine, variant, size, query, phase |
| `rigor_session_medians.csv` | warm median per session and the range between sessions |
| `rigor_equivalence.csv` | every variant's first result compared with DuckDB default (`sql/equivalence.py`) |
| `protocol.json` | settings, schedule, environment, caveats, status |

If any variant returns a different result, the timings are written only to `rigor_timings_REJECTED.csv` and the run
exits with an error, as in the main benchmark.

### Safety controls in the tool

- `--help` and `--dry-run` never connect and never import the database drivers; without `--confirm-real-run` the
  tool prints the plan and stops.
- DuckDB files are opened with `read_only=True`. PostgreSQL sessions are opened with
  `default_transaction_read_only=on`, which is checked with `SHOW` before any query.
- Every statement passes a guard that allows only `SELECT` / `WITH`, `EXPLAIN` of those, `SHOW`, and setting
  `max_parallel_workers_per_gather`, `search_path` or `threads`; any write keyword or a second statement is refused.
- No `CREATE STATISTICS` or other planner-statistics change is made. A statistics variant would need an isolated copy
  of the database and separate approval.


## (e) Measured findings

**Status: Completed for the four-month dataset on 20 November 2026.**

The supplementary benchmark was executed on the real NYC taxi dataset containing 6,658,475 cleaned nighttime trips.

The study investigated Q1, Q2 and Q3 through query-plan analysis and repeated timing experiments.

The reported timings are the median of three independent session medians. Each session used two warm-up executions and five measured warm executions per configuration.

These results are separate from the original benchmark performed on 15 November 2026.

### Repeated timing results — Four months

| Query | PostgreSQL default (ms) | DuckDB default (ms) | DuckDB speedup |
|---|---:|---:|---:|
| Q1 | 4,327.25 | 43.36 | 99.8x |
| Q2 | 1,349.55 | 105.04 | 12.8x |
| Q3 | 1,415.58 | 31.91 | 44.4x |

All 45 recorded equivalence comparisons passed: 15 for Q1 and 30 for Q2/Q3.

### PostgreSQL execution-plan findings

| Query | Estimated groups | Actual groups | Workers launched |
|---|---:|---:|---:|
| Q1 | 2,503,684 | 12 | 0 (no parallel plan) |
| Q2 | 265 | 261 | 2 |
| Q3 | 8 | 8 | 2 |

PostgreSQL substantially overestimated the aggregation cardinality for Q1, while its estimates for Q2 and Q3 were accurate.

PostgreSQL launched two parallel workers for Q2 and Q3 but did not use parallel workers for Q1.

These observations indicate query-specific differences in planner behavior.

However, the study did not experimentally correct PostgreSQL's Q1 cardinality estimate. Consequently, the measured results do not establish how much of Q1's slowdown was caused by the estimation error.

### Controlled parallelism

The experiments also investigated:
- PostgreSQL with zero additional workers.
- PostgreSQL with up to two additional workers.
- DuckDB with one thread.
- DuckDB with three threads.
- Both engines under their default configurations.

DuckDB maintained a substantial advantage across the tested Q1-Q3 configurations.

Configured thread and worker limits do not necessarily imply equal actual CPU utilization.

### Scope and limitations

- These supplementary results cover the four-month dataset only. The complete supplementary study was not run at one and two months.
- PostgreSQL operates as a client/server database, whereas DuckDB runs inside the benchmarking process.
- The first execution on a connection does not guarantee a cold operating-system cache.
- Three independent sessions were collected; the observed speedups are not universal performance guarantees.
- DuckDB exhibited between-session timing variability, particularly for Q2.
- JIT and planner-statistics interventions were not conducted.
- Q4 and Q5 remain outside the scope of this supplementary study.

### Evidence

The corresponding result directories contain the recorded execution plans, timing summaries, equivalence checks and experimental protocols.

- `results/marin_q1_4m_plans_20261120/`
- `results/marin_q1_4m_timings_20261120/`
- `results/marin_q2q3_4m_plans_20261120/`
- `results/marin_q2q3_4m_timings_20261120/`

See `docs/RESULTS.md` for the comparison with the original benchmark and additional interpretation.

### How to word the results

| Avoid | Prefer |
| --- | --- |
| "DuckDB is 85x faster because it is columnar" | "In our measured configuration Q1 was X times faster on DuckDB; the plans suggest Y" |
| "Equal threads prove a fair comparison" | "Configured parallelism was partly controlled; actual workers were recorded" |
| "Three runs prove general superiority" | "Across three sessions on this machine, medians and spread were ..." |

## (f) Tests, limitations and next steps

**Tests.** `tests/test_benchmark_rigor.py` uses fake connections and, if DuckDB is installed, one tiny synthetic
DuckDB file. It checks that the SQL comes from `sql/queries.py`, plan parsing (with and without workers), the seeded
and alternating schedule, recorded settings, timing units, refused output collisions, the read-only guard, the
read-only PostgreSQL session check, that `--dry-run` never connects, clear failures for a missing manifest or a
different dataset, and that a variant with a different result rejects the timings. It asserts no performance
numbers.

**Limitations.**

- The DuckDB plan summary is best effort (operators and total time read from the text output); the full text is
  always saved.
- The variants change one setting each; they do not cover memory settings (`work_mem`) or JIT.
- PostgreSQL timings include the client/server round trip and fetching the rows into Python; DuckDB's are in-process.
- Peak memory is not measured by this tool (see `scripts/sample_docker_stats.py`, another workstream).
- Q4 (application-level lookup latency) and Q5 (index experiment) are separate workloads and not part of this study.

**Study completion and possible future work.**

The four-month Q1-Q3 execution-plan and repeated-timing studies have been completed and are reported in section (e).

The following extensions were not performed:

1. Repeat the supplementary experiments at one- and two-month dataset sizes.
2. Investigate PostgreSQL's Q1 planner-statistics behavior using an isolated database.
3. Measure the effect of enabling or disabling PostgreSQL JIT.
4. Investigate alternative PostgreSQL index structures for Q5.

Any experiment that modifies database statistics or indexes must use an isolated database and must not overwrite the original benchmark evidence.