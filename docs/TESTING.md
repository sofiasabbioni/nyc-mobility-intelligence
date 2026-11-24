
## Current integrated-project verification (24 November 2026)

The following checks were completed after the original synthetic-data audit.

| Component | Verified result | Scope |
|---|---|---|
| Real NYC taxi data | 15,289,433 original records; 6,658,475 cleaned nighttime trips | April–July 2026 |
| Data quality | 41/41 validation checks passed; zero unaccounted rows | Real four-month dataset |
| Original benchmark | Completed with 18 equivalence and 36 consistency checks recorded | Real-data PostgreSQL/DuckDB experiment |
| Supplemental benchmark | Q1–Q3 four-month execution-plan and repeated-timing experiments completed | Three independent timing sessions per configuration |
| Supplemental result equivalence | 45/45 recorded comparisons passed | Q1: 15; Q2/Q3: 30 |
| Dashboard | Successfully built and launched through Docker Desktop on Windows/WSL2 | Existing real-data outputs |
| Nightlife visualization | 2D and 3D maps rendered successfully | Real taxi-zone geometry and nightlife metrics |
| 3D map tests | 15/15 passed in a temporary Docker container using Python 3.12 | `tests/test_nightlife_3d.py` |
| GitHub Actions | Python unit-test and PostgreSQL integration-test jobs passed on PR #16 | Integrated dashboard changes |
| Dashboard readability | KPI values and benchmark headline cards visually verified | Browser inspection |

The original benchmark and supplemental experimental findings are reported separately in `docs/RESULTS.md` and `docs/BENCHMARK_RIGOR.md`.

### Remaining limitations

- A complete independent reproduction from a fresh checkout on a second machine has not been confirmed.
- Exact performance timings are machine- and configuration-dependent.
- Cold operating-system cache conditions were not established.
- Peak-memory measurement methodology has not been independently validated.
- Passing automated tests does not by itself establish the scientific validity of performance interpretations.

The checks above must not be confused with the original synthetic-data test counts recorded in Section 1.

# Tests performed, actual results, and what is NOT verified

This document distinguishes the original synthetic-data testing from the subsequent real-data experiments and final integration checks.

Sections 1–4 preserve the original automated testing audit conducted in a Linux cloud container (Python 3.13, pyarrow 26.0.1, DuckDB 2.0.0, psycopg 3.3.8, Streamlit 1.67.0, PostgreSQL 16.15).

Those original tests used synthetic data and were not intended to establish real-world performance. Subsequent real-data experiments and final Docker/GitHub verification are documented separately below.

## 1. Automated test suite (`python -m pytest tests -q`)
| Run | Result |
|---|---|
| With PostgreSQL (`TEST_POSTGRES=1`) | **68 passed** in 12.4 s, 0 failed |
| Without PostgreSQL | **63 passed, 5 skipped** in 6.7 s, 0 failed |
| Run from a fresh unzip of the delivered ZIP | same two results as above |

A first run found a real bug (CSV result files written without quoting: a check name containing a comma made
`db_consistency.csv` unreadable). It was fixed in `benchmark/benchmark.py: write_csv` and the suite was rerun. One test also failed because of the test
itself (comparing tables containing NaN, and a settings combination the code correctly rejects); the tests were corrected, not the code, in those two cases.

| Test file | Tests | What it checks |
|---|---|---|
| `test_night_and_queries.py` | 16 | window hours (wrapping and not), labels (`18:00-05:59`), invalid windows rejected, Q5 range and end hour follow `NIGHT_END`, Q1 ordering follows the window, total ordering in Q2/Q3, point ids exist in the smallest size, statistics (mean/median/sample stdev/percentile) |
| `test_equivalence.py` | 4 | result comparison: tolerance, Decimal/float, NaN, NULL, row count, column count, order, first difference reported |
| `test_preprocess.py` | 15 | every exclusion reason is assigned exactly once with the right priority; 18:00 included and 06:00 excluded; NULL passenger_count kept; non-wrapping window; processed + excluded = source (4 months); ids 1..N; reports and 28 exclusion rows; raw files byte-identical; rerun reuses; changed `NIGHT_END`/`NIGHT_START`/`TOTAL_MONTHS` or changed raw content rebuild; `FORCE_REBUILD`; broken manifest; missing raw file; validation detects removed rows and a hole in the ids; fingerprint deterministic and sensitive |
| `test_config_download_full.py` | 9 | settings validation and read-at-call-time; month list across year end; existing files used with the network disabled; damaged file stops the run and is untouched; failed download message and no `.part` left; wrong zone header; full dataset keeps all trips, all columns (including a column present in one month and a type change), original timestamps/order, `source_month`, raw files unchanged; verification detects a modified value |
| `test_databases_and_benchmark.py` | 12 | DuckDB load matches the manifest and stores the fingerprint; stale clean files refused; all queries run and Q5 is non-empty, all 200 lookups return one row; summary statistics and EXPLAIN plan parsing; seeded query order; settings validation; benchmark fails cleanly without a loader. **PostgreSQL (5, need a server):** load + full benchmark (phases first/warmup/warm with the configured counts, 3 engines, all equivalence PASS, EXPLAIN shows no index before and an index node after, row counts, every dashboard CSV has the expected columns); timings **rejected** when DuckDB data are altered; benchmark refuses databases built from another dataset; PostgreSQL loader rerunnable without duplicates; `run_loader` twice with the full dataset, `load_times.csv` recreated |
| `test_dashboard_and_config.py` | 10 | safe CSV reading (missing, empty, wrong header, truncated, extra columns); status rules (running, failed, no equivalence, FAIL); labels with actual rows; dashboard (Streamlit `AppTest`) with empty results, with broken/partial files, with a failed run (timings hidden); `docker-compose.yml` structure (four services, dependency conditions, volumes, port); every compose variable documented in `.env.example`; pinned requirements; schema file |
| `test_scripts.py` | 2 | memory scripts: unit conversion, container-name parsing, peak inside the right window and container, empty when no samples |

## 2. Other checks run
| Check | Result |
|---|---|
| `py_compile` of all Python files (from the unzipped project) | all compile |
| `docker compose config --quiet` (real Docker CLI, no daemon) | valid |
| Container layouts simulated (only the files each Dockerfile copies: `loader/`+`sql/`, `benchmark/`+`sql/` without `loader/`, `dashboard.py`+`results_io.py`) | modules import and the dashboard runs without exception |
| Loader on 4 x 400,000 synthetic source rows (781,796 clean rows) against local PostgreSQL | ran to the end in 8.8 s; a second run reused the clean files (fingerprint) and rebuilt both databases; all validation checks PASS; accounting `unaccounted_rows = 0` for every month |
| Benchmark on the same data, default protocol (5 runs, 2 warm-ups, shuffled order) | ran in 19 s; 18 of 18 equivalence checks and 36 of 36 consistency checks PASS; EXPLAIN ANALYZE showed a sequential scan before the index and an index scan after it, at all three sizes; run status `complete` |
| Optional complete dataset on synthetic files (4 x 120,000 rows, extra column in one month, type change in another) | 480,000 rows = source rows; all per-column checks PASS |
| Dashboard rendered headlessly from the result files of the runs above | no exception; all sections populated |

These runs prove that the **pipeline works and its checks fire**, not how the engines perform on real data. Synthetic timings are not reported and must not be quoted.

## 3. Limitations of the original synthetic-data audit (historical)
The following list records what was not verified during the original Linux-cloud synthetic-data audit. Several items were subsequently verified on the real-data Windows/WSL2 setup, as documented in the current verification section above. This list is retained to preserve the scope of the original audit.

1. **Docker build and `docker compose up`**: no Docker daemon was available. Images, healthcheck timing, the dependency chain at runtime, the bind mount `data\raw` and the named volume together on Windows/WSL2 are untested.
2. **Real TLC files**: not reachable from the test environment. Untested: real column types and NULL patterns, the real number of excluded rows per reason, real file sizes and timings, the combined dataset on real files with columns that appear in some months only (e.g. fees added in later years).
3. **Scale**: the largest test had 781,796 clean rows (about 1.6 million source rows). Memory, time and disk at the real size (millions of rows) are untested.
4. **PostgreSQL in the `postgres:16` image** with default settings (tested on a local 16.15 server); whether the planner uses the `pickup_ts` index on your data and machine is decided by your `explain_q5.csv`.
5. **Memory sampling** with real `docker stats` output on Windows (parsing is unit-tested on example strings only), and the alignment of sample times with benchmark phases.
6. **Python 3.12 / Linux containers**: tests ran on Python 3.13. Dependencies are pinned to the tested versions.
7. **Visual check in a browser** of the dashboard layout (it was exercised programmatically only).
8. **Windows PowerShell commands** in `docs/WINDOWS_GUIDE.md` were not run on Windows.
9. **True cold-cache behaviour** is not tested or claimed (see METHODOLOGY section 5).
10. **Run-to-run and seed variability** on real data: unknown until you repeat the benchmark with another `BENCH_SEED`.

## 4. How to reproduce the original synthetic test runs
```
python -m venv .venv ; .\.venv\Scripts\Activate.ps1       # PowerShell
pip install -r requirements-dev.txt
python -m pytest tests -q                                  # 63 pass, 5 skipped without PostgreSQL
$env:TEST_POSTGRES="1"; $env:POSTGRES_HOST="localhost"; $env:POSTGRES_PORT="5432"   # with a PostgreSQL you can write to
$env:POSTGRES_USER="postgres"; $env:POSTGRES_PASSWORD="..."; $env:POSTGRES_DB="taxi"
python -m pytest tests -q                                  # 68 pass
```
The PostgreSQL tests create and drop the schemas `m1`, `m2` and `m4` in that database: use a scratch database.
