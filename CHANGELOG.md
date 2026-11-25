# Changelog: final UI improvements (readability + 3D nightlife map)

- **KPI truncation fixed** (`app/theme.py`, CSS only): metric values are no longer ellipsised ("15,289,..." ->
  "15,289,433"). The card is a CSS size container; the value scales with the card width (never above the previous
  2rem) and wraps at spaces. Affects the four Data quality KPIs, the three Key findings cards and the other metrics.
  Key findings cards share one height. No value, rounding rule or calculation changed.
- **3D nightlife map** (`app/nightlife_3d.py`, `app/nightlife_map.py`): new "Map view" selector (2D default / 3D).
  The 2D figure is unchanged (shared `colour_scale` helper returns exactly the values it used inline).
  `pydeck==0.9.3` added to `app/requirements.txt`; `app/Dockerfile` copies the new module.
- Tests: `tests/test_nightlife_3d.py` (15 new).

---

# Changelog: version 2 (audit corrections) against the first delivered project

Format: audit item -> what changed -> file and functions. "New" = new file; "rewritten" = new design, same purpose.

## Data integrity
| Audit item | Change | Files / functions |
|---|---|---|
| Keep the four original files unchanged | Raw files are only read. Their SHA-256, size, rows, columns are recorded. Docker mounts the host folder `data/raw`. Test proves hashes are identical after preprocess and after building the full dataset. | `loader/preprocess.py`: `describe_raw`, `write_reports` (raw_files.csv); `docker-compose.yml` (bind `./data/raw`); `tests/test_preprocess.py::test_raw_files_are_never_modified` |
| Optional full combined dataset with `source_month` | New script. All trips, all original columns and timestamps, original row order, union of columns, widest common type (listed), `source_month` added. Verified per month and column (non-null count, min, max) and by row count. Enabled with `BUILD_FULL_DATASET=1` or run alone. Not used by the databases. | New `loader/build_full_dataset.py`: `unified_schema`, `to_unified`, `verify`, `main`; `loader/run_loader.py: main` |
| Separate cleaned night-trip dataset | Kept as `data/clean`, now always named "cleaned night trips (a subset)" in output, dashboard and docs. | `loader/preprocess.py: main`; `app/dashboard.py`; README |
| Document every exclusion rule; report exclusions by reason and month | Seven mutually exclusive reasons in priority order; per month and reason counts and percentages. | `loader/preprocess.py`: `REASONS`, `classify` (rewritten), `clean_month`, `write_reports`; New `docs/EXCLUSION_RULES.md`; output `results/data_quality/exclusions.csv`, `exclusion_rules.csv`, `preprocess_summary.csv` |
| processed + excluded = source | Checked per month and in total (also against Parquet metadata). Failure stops the loader (exit 1). | `loader/preprocess.py: validate`, `write_validation`; `validation.csv` |
| Validate trip IDs, date boundaries, PostgreSQL/DuckDB row consistency | IDs: unique, contiguous, +1 in file order, continuing across months. Pickup inside month `[first, next first)`. Dropoff >= pickup, zones >= 1, amounts >= 0, hours in window (independent DuckDB SQL). After loading: ids, count, sums, min/max pickup compared across engines and with the manifest. | `loader/preprocess.py: validate`; `loader/load_postgres.py: load`, `loader/load_duckdb.py: load` (count, id range, `load_meta`); `benchmark/benchmark.py`: `CHECK_SQL`, `consistency` -> `db_consistency.csv` |
| Never call the night subset the complete dataset | Wording changed in README, dashboard (header and captions, storage chart legend), docs, console output. | `app/dashboard.py`; `README.md`; `loader/preprocess.py: main` |

## Benchmark correctness
| Audit item | Change | Files / functions |
|---|---|---|
| Verify result equivalence before accepting timings | The first timed result of each query is compared on both engines (order matters; floats rel. 1e-9; Q4 compares all 200 rows; indexed Q5 too). Timings are written only if all pass; otherwise `rejected_timings.csv` and exit 1; the dashboard hides timings. | New `sql/equivalence.py: compare_results`, `values_equal`; `benchmark/benchmark.py: main`; `app/results_io.py: timings_status`; `equivalence.csv` |
| Total ordering of results | Q2 groups by `location_id` and orders by `trips DESC, location_id`; Q3 `ORDER BY trips DESC, borough ASC NULLS LAST` (PostgreSQL and DuckDB order NULLs differently by default); Q4 selects explicit columns. | `sql/queries.py: queries`, `point_sql` |
| Scaling labels with actual row counts | `rows` column in every timing row; `datasets.csv`; labels like "2 mo - 1,234,567 rows (50% of rows)". "25/50/100%" removed. | `benchmark/benchmark.py: main`; `app/results_io.py: size_labels`; `app/dashboard.py` |
| Controlled warm-up; cold vs warm | Protocol per query: 1 "first", `BENCH_WARMUPS` warm-ups, `BENCH_RUNS` "warm"; phase stored; reported separately. "First" is explicitly *not* a guaranteed cold start. | `benchmark/benchmark.py`: `protocol`, `execute`; `benchmark.csv` column `phase`; METHODOLOGY section 5 |
| At least five configurable runs | `BENCH_RUNS` default 5 (was 3), `BENCH_WARMUPS`, `BENCH_SEED`, `BENCH_ORDER`; validated. | `benchmark/benchmark.py: settings`; `.env.example`; `docker-compose.yml` |
| Median, mean, stdev, individual runs | Every run kept in `benchmark.csv`; `benchmark_summary.csv` has n, mean, median, sample stdev, min, max; dashboard shows median with min-max and tables. | New `sql/stats.py: describe`, `percentile`; `benchmark/benchmark.py: summarize`; `app/dashboard.py` |
| Cache and query-order effects | Seeded shuffled query order, identical for both engines, recorded as `query_order`; engines never run concurrently; no untimed pre-pass before the first run; remaining threats documented. | `benchmark/benchmark.py: query_order`; METHODOLOGY section 5 |
| Verify index use with EXPLAIN ANALYZE | `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` of Q5 before and after the index; plan JSON saved; index node detection; dashboard warns if not used. | `benchmark/benchmark.py: explain_q5`, `plan_nodes`, `index_used`; `explain_q5.csv`, `results/explain/*.json` |
| Clear definitions of load, query, memory, storage | Load time now defined identically for both engines (timer covers schema creation through ANALYZE/CHECKPOINT; dropping the old copy and verification excluded). Definitions written down. | `loader/load_postgres.py: load`, `loader/load_duckdb.py: load`; METHODOLOGY section 8 |
| 200 lookups are application-level latency | Q4 reported in its own dashboard section with per-lookup latency (mean, median, p95, p99, max) and a caveat; never merged with analytical averages; point ids spread over the smallest size (previously fixed `1 + i*997`). | `benchmark/benchmark.py: execute`, `lookup_summary`; `sql/queries.py: point_ids`; `lookup_latency.csv` |
| Storage detail | Added PostgreSQL heap and index sizes and Parquet raw/clean sums. | `benchmark/benchmark.py: main` (`disk_sizes.csv`) |
| Environment | PostgreSQL settings and DuckDB threads/memory recorded. | `benchmark/benchmark.py: environment` |

## Implementation
| Audit item | Change | Files / functions |
|---|---|---|
| Night boundary labels; no hardcoded Q5 end | One definition of the window (`night.py`). Label "18:00-05:59 (12 hours)" (last included minute). Q5 start/end derive from `NIGHT_START`/`NIGHT_END` and the window length; Q1 ordering generalised (non-wrapping windows too). Window validated (1 to 23 hours). | New `sql/night.py`; `sql/queries.py: queries`, `q5_range`; `loader/common.py: get_config`; `app/dashboard.py` |
| Changed settings must not silently reuse old files | Manifest with fingerprint (rules version, settings, SHA-256 of every raw file and the zones file). Mismatch -> rebuild with a message. Databases store the fingerprint (`load_meta`); the benchmark refuses mismatching databases. `FORCE_REBUILD=1`. Previous version skipped any existing clean file. Settings are read when called, not frozen at import. | `loader/common.py`: `get_config`, `fingerprint`, `RULES_VERSION`, `read_manifest`; `loader/preprocess.py: main`; `sql/schema.sql` (`load_meta`); `benchmark/benchmark.py: main` |
| Dashboard handles incomplete result files | Safe readers (missing, empty, truncated, wrong columns); every section guarded; status banner (running/failed/complete); timings hidden unless complete and equivalent. `run_status.json`. | New `app/results_io.py: read_csv_safe`, `read_json_safe`, `timings_status`; `app/dashboard.py` (rewritten); `app/Dockerfile` |
| Docker dependencies, CSV schemas, SQL, config, rerun | Exact pinned dependency versions; `loader` and `benchmark` images copy `sql/`; benchmark waits for healthy db and finished loader; CSV writer with quoting (a real bug found by the tests: unquoted commas in check names); schema file no longer uses `IF NOT EXISTS` because schemas are dropped and rebuilt; databases rebuilt each run, `load_times.csv` recreated; benchmark deletes its old outputs first; `.env.example` documents every variable used by compose. | `docker-compose.yml`; `loader/Dockerfile`, `benchmark/Dockerfile`, `app/Dockerfile`; `*/requirements.txt`; `benchmark/benchmark.py: write_csv`; `loader/load_postgres.py: load`; `loader/run_loader.py` |
| Use already downloaded Windows files | Existing files are used with no network call and never changed; damaged ones stop the run without being deleted; zone header validated; Windows guide. | `loader/download_data.py`: `ensure_file`, `check_trip_file`, `main`; `docker-compose.yml` (`./data/raw:/data/raw`); `docs/WINDOWS_GUIDE.md` |
| Automated unit and integration tests | 68 tests (63 without PostgreSQL). | New `tests/` (see `docs/TESTING.md`), `requirements-dev.txt` |

## Other changes
* `loader/common.py`: `Config` dataclass, validation of `SIZES`, `START_MONTH`, window; helpers `write_csv`, `write_json`, `sha256_file`.
* `loader/city_results.py`: writes `city/meta.json`; uses shared queries.
* `scripts/summarize_memory.py`: `peaks()` function, sample counts, empty cell when no samples.
* `docs/architecture.md`, `README.md` rewritten; new `docs/METHODOLOGY.md`, `docs/EXCLUSION_RULES.md`, `docs/WINDOWS_GUIDE.md`, `docs/TESTING.md`.
* Query set: the five workloads are the same as before (Q1-Q5); only ordering, Q5 window and Q4 id spread changed.
* Data layout: `/data/raw` is now a host bind mount (`data\raw`), the rest of `/data` stays in a Docker volume.

## Not changed (by request)
PostgreSQL-DuckDB comparison; four-container Docker architecture (db, loader, benchmark, app); one-command start; Streamlit dashboard; schemas per test size (`m1`, `m2`, `m4`).
