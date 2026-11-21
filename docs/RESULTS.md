# Results and interpretation

Project: *Night taxis in New York: PostgreSQL vs DuckDB* — Computational Infrastructures for Data Science.

This report contains **two distinct experimental series**. Their timings should not be pooled: the original project benchmark ran on **15 November 2026**, while the supplemental Q1–Q3 study ran on **20 November 2026** using a different measurement protocol.

## 1. Dataset and correctness

The baseline uses NYC TLC yellow taxi data from April–July 2026 and a cleaning window of **18:00–05:59:59, New York local time**. PostgreSQL and DuckDB contain the **cleaned nighttime subset**, not all source trips.

| Dataset size | Cleaned nighttime trips | Percentage of four-month set |
|---|---:|---:|
| 1 month | 1,628,153 | 24.45% |
| 2 months | 3,390,368 | 50.92% |
| 4 months | 6,658,475 | 100.00% |

The original `run_status.json` reports `state: complete`, 18 equivalence checks, and 36 consistency checks. Its data fingerprint is `1c08d359491acd5c4940d03049fdd16d1c27e1d5843dbb507af687cd8f3cd8cd`.
The supplied ZIP does not include the individual `equivalence.csv`, `db_consistency.csv`, or `data_quality` files; use those if a row-level audit or an original-source-row count is required.

## 2. Original benchmark: query timing

Date: **15 November 2026**. For each engine, query, and dataset size, the benchmark measured one first execution, two warmups, and five warm executions. The table below uses the **median of five warm executions** on the four-month dataset. All figures are wall-clock execute-and-fetch times in the benchmark process.

| Query | Workload | PostgreSQL (ms) | DuckDB (ms) | Observed result |
|---|---|---:|---:|---|
| Q1 | Hourly mean fare and tip | 4,134.22 | 48.77 | DuckDB 84.77× faster |
| Q2 | Top 10 pickup zones | 1,369.67 | 112.65 | DuckDB 12.16× faster |
| Q3 | Trips per borough | 1,530.84 | 36.65 | DuckDB 41.77× faster |
| Q4 | 200 sequential point lookups | 213.33 | 610.23 | PostgreSQL 2.86× faster |
| Q5 | One-night time-range filter | 417.31 | 6.71 | DuckDB 62.24× faster |

**Interpretation.** DuckDB outperformed PostgreSQL on the scan/aggregation and time-range queries Q1, Q2, Q3, and Q5. PostgreSQL outperformed DuckDB on Q4. These observations describe the configured systems and this workload; they are not universal rankings of database designs.

### Q4: application-level point lookups

Each Q4 repetition executes **200 sequential `trip_id` lookups**, and each engine has a primary-key index. The Q4 totals above are *not* per-lookup latencies and include Python/driver overhead (and network round trips for PostgreSQL). The five warm repetitions contain **1,000 lookups per engine**, which are pooled in `lookup_latency.csv`:

| Four-month Q4 (1,000 pooled measured warm lookups) | PostgreSQL | DuckDB |
|---|---:|---:|
| Median individual lookup | 0.942 ms | 2.853 ms |
| 95th-percentile individual lookup | 1.725 ms | 4.513 ms |

Do not merge Q4's client-side times into an analytical-query average.

## 3. Loading time and storage

| Dataset | PostgreSQL load (s) | DuckDB load (s) | PostgreSQL database (MB) | DuckDB database (MB) |
|---|---:|---:|---:|---:|
| 1 month | 26.563 | 4.454 | 201.47 | 54.01 |
| 2 months | 42.749 | 5.916 | 419.43 | 97.27 |
| 4 months | 85.580 | 12.068 | 823.91 | 197.14 |

*Storage values use decimal MB (1 MB = 1,000,000 bytes).* On the four-month data, DuckDB's observed loading time is **7.09× shorter**, and PostgreSQL's database is **4.18× larger** by the project's database-size definitions.

Loading was measured **once per engine and size**, so it is descriptive, not a statistically robust estimate. PostgreSQL uses Python/CSV serialization and `COPY`; DuckDB reads Parquet natively. These are *pipeline* comparisons, not pure storage-engine measures. Database size includes the PostgreSQL heap and indexes versus DuckDB's checkpointed file; raw Parquet is a different comparison basis.

The supplied results do **not** include `memory_summary.csv`; no validated peak-memory figure is reported here.

## 4. PostgreSQL Q5 index experiment

| Four-month Q5 variant | Warm median | Plan evidence |
|---|---:|---|
| PostgreSQL baseline, no `pickup_ts` index | 417.31 ms | Sequential scan (with gather) |
| PostgreSQL with `pickup_ts` index | 49.25 ms | Index scan used |
| DuckDB baseline | 6.71 ms | No equivalent additional index created |

The PostgreSQL time-range index reduced the measured Q5 latency **8.47×**. The `explain_q5.csv` record confirms PostgreSQL used the added index in the four-month plan. This demonstrates that a targeted index can materially improve the row-store result for selective reads.

The index increased PostgreSQL's recorded database size from **823.91 MB** to **938.20 MB** (additional **114.29 MB**). The index is removed after the experiment under the documented benchmark procedure.

## 5. Supplementary Q1–Q3 study (separate protocol)

Date: **20 November 2026**, four-month dataset. The tool `benchmark/rigor_study.py` ran **three independent sessions**, each with two warmups and five measured warm executions per configuration. The following entries are the **median of the three session medians**, not the original benchmark medians. All 45 cross-configuration equivalence checks in the uploaded supplemental Q1/Q2/Q3 timing files report PASS (15 for Q1, 30 for Q2/Q3).

| Query | PostgreSQL default (ms) | DuckDB default (ms) | PG / DuckDB ratio |
|---|---:|---:|---:|
| Q1 | 4,327.25 | 43.36 | 99.8× |
| Q2 | 1,349.55 | 105.04 | 12.8× |
| Q3 | 1,415.58 | 31.91 | 44.4× |

### Query plans and parallelism

Four-month `EXPLAIN ANALYZE` evidence, default PostgreSQL configuration:

| Query | Estimated aggregate groups | Actual aggregate groups | Workers launched |
|---|---:|---:|---:|
| Q1 | 2,503,684 | 12 | None reported (no parallel plan) |
| Q2 | 265 | 261 | 2 |
| Q3 | 8 | 8 | 2 |

Q1 groups by an expression based on the pickup hour; PostgreSQL's estimated number of groups is far above the observed 12. The planner instead estimates Q2 and Q3 accurately and uses parallel workers. This suggests a **query-specific estimation problem**, not a general absence of parallel execution. However, the experiment did **not** change the statistics and measure the resulting plan, so it **does not prove how much of Q1's slowdown is caused by the estimation error**.

The supplemental timings also tested PostgreSQL's limits of zero and up to two extra workers, and DuckDB with one and three threads. Thread caps are not equal to measured CPU usage; PostgreSQL may launch fewer workers than allowed. Q1 still showed a large DuckDB advantage when DuckDB was limited to a single thread. DuckDB's short runtimes showed some between-session variation, particularly on Q2.

## 6. Limits and reproducibility

- PostgreSQL is a networked client/server database; DuckDB executes inside the benchmarking Python process. The findings compare **configured systems**, not just storage formats.
- “First” is the first query execution on a connection, **not** a clean-cache measurement. The OS page cache is not flushed.
- Original warm times come from five executions in one benchmark run. Supplemental values use three independent sessions; they cannot be pooled directly.
- Loading times have a single observation per engine/size, and no validated peak-memory value was included in the uploaded originals.
- PostgreSQL's Q1 group misestimation is associated with the observed plan, but is **not causally isolated** from JIT, executor, disk, or cache effects.
- SQL correctness and row counts are verified by the project's own checks; report evidence from `run_status.json`, the equivalence files, the manifest, and plan artifacts. Full `data_quality/` and individual original equivalence CSVs were not included in Sofia's uploaded ZIP.
- Reproduction on another machine should compare data counts and query outputs for equality, but timings must be reported per machine rather than merged.

## 7. Result provenance

**Original 15 November benchmark:** `results/benchmark_summary.csv`, `load_times.csv`, `disk_sizes.csv`, `lookup_latency.csv`, `explain_q5.csv`, `datasets.csv`, `run_status.json` (Sofia's supplied result archive).

**20 November supplemental study:** `Marin_Q1_4m_Plans.zip`, `Marin_Q1_4m_Timings.zip`, `Marin_Q2Q3_4m_Plans.zip`, `Marin_Q2Q3_4m_Timings.zip`; the corresponding output folders contain `plan_summary.csv`, `rigor_session_medians.csv`, `rigor_equivalence.csv`, and `protocol.json`.

Further implementation/protocol details: `docs/METHODOLOGY.md` and `docs/BENCHMARK_RIGOR.md`. The machine used for the official result should be recorded in `docs/MACHINE.md`, using the real computer specifications and original `results/environment.txt` rather than assumptions.
