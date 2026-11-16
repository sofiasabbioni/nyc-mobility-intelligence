# Night taxis in New York: PostgreSQL vs DuckDB

Course project *Computational Infrastructures for Data Science*. One command starts a Dockerized system that loads
NYC yellow taxi trips, compares a row store (PostgreSQL 16) with a column store (DuckDB) and shows the results in a
Streamlit dashboard.

> **Which data?** The databases hold the **cleaned night trips only** (pickup 18:00:00 to 05:59:59, New York time), a *subset*
> of the TLC files. The original monthly files are never modified, and an optional complete combined dataset (all trips, all
> columns) can be built. See `docs/METHODOLOGY.md` and `docs/EXCLUSION_RULES.md`.

## Run it (Windows PowerShell; full guide: `docs/WINDOWS_GUIDE.md`)
```powershell
cd $HOME\Desktop\taxi_project
Copy-Item .env.example .env
# optional: copy your already downloaded files into data\raw (they are used, not downloaded again)
docker compose up --build
```
Open http://localhost:8501 when the dashboard is up. Needs Docker Desktop and about 6 GB of free disk.
Mac/Linux: `cp .env.example .env` then the same `docker compose up --build`.

## What the system does
1. `loader`: uses/downloads the original files, builds the cleaned night-trip files with an exclusion report and validation
   (every source row is either kept or counted under one reason), loads PostgreSQL (schemas `m1`, `m2`, `m4`) and DuckDB
   (`taxi_1m/2m/4m.duckdb`) from the *same* clean files, writes the city tables.
2. `benchmark`: checks that both databases match the clean dataset, runs five workloads with a first / warm-up / warm protocol,
   **accepts timings only if both engines return identical results**, runs an index experiment checked with `EXPLAIN ANALYZE`.
3. `app`: dashboard that reads the result files and tolerates missing or incomplete ones.

Workloads: Q1 average fare/tip by hour, Q2 top 10 pickup zones, Q3 trips per borough (analytical); Q4 200 sequential lookups by
`trip_id` (**application-level latency**, not pure engine time); Q5 all trips of one night by time range (PostgreSQL index test).
Test sizes are 1, 2 and 4 consecutive months, labelled with their **actual row counts**.

## The six questions
1. **Problem.** How do a row-oriented client/server database and a column-oriented embedded database compare on analytical queries over night taxi trips, and how do they scale?
2. **Data.** NYC TLC yellow taxi monthly Parquet files (default 2026-04 to 2026-07) + zone lookup; cleaned night-trip subset for the experiment.
3. **Databases and why.** PostgreSQL (row store, indexes, server) and DuckDB (column store, vectorised, embedded). The workload mixes scans/aggregations, joins, range selection and point lookups, which is where the designs differ.
4. **Structure.** `docs/architecture.md`: four containers, one `docker compose up`, order enforced with healthchecks and `service_completed_successfully`.
5. **What is measured.** Load time, query time (first and warm: median, mean, standard deviation, every run), Q4 latency distribution, storage, peak memory (optional sampler), growth, index effect with plan verification. Definitions: `docs/METHODOLOGY.md` section 8.
6. **Findings.** Fill from your own `results/` files after your run (table below). No numbers are shipped with this project.

## Results table (fill in from your run; nothing is pre-filled)
| Metric (largest test) | PostgreSQL | DuckDB |
|---|---|---|
| Source rows (all trips) / cleaned night rows | | |
| Load time (s) | | |
| Q1, Q2, Q3 warm median (s) | | |
| Q4 warm median, 200 lookups (s) / per-lookup p95 (ms) | | |
| Q5 warm median without / with index (s); index used? | | n/a |
| Storage (MB) | | |
| Peak memory (MB) | | |

Machine: `docs/MACHINE.md`. Settings used: `results/environment.txt`.

## Honest limits
PostgreSQL is a server reached over a connection, DuckDB runs inside the benchmark process; default PostgreSQL settings; one machine;
"first" is not a guaranteed cold start (OS cache not flushed); load time is a single measurement; memory is whole-container from `docker stats`.
Details and what the results can and cannot support: `docs/METHODOLOGY.md` sections 5, 8, 9.

## Files
| Folder | Content |
|---|---|
| `loader/` | `download_data.py`, `preprocess.py`, `build_full_dataset.py`, `load_postgres.py`, `load_duckdb.py`, `city_results.py`, `run_loader.py`, `common.py` (imported by the others) |
| `sql/` | `schema.sql`, `queries.py`, `night.py` (the night window), `equivalence.py`, `stats.py` |
| `benchmark/` | `benchmark.py` |
| `app/` | `dashboard.py`, `results_io.py` |
| `scripts/` | `sample_docker_stats.py`, `summarize_memory.py` (run on the host) |
| `tests/` | automated tests (`python -m pytest tests -q`) |
| `CHANGELOG.md` (root), `docs/` | `METHODOLOGY.md`, `EXCLUSION_RULES.md`, `WINDOWS_GUIDE.md`, `TESTING.md`, `architecture.md`, `MACHINE.md` |
| `data/raw`, `data/full`, `results/` | your original files, optional combined dataset, all outputs |

GitHub (course requirement): commit history from all members is a team task; `.gitignore` already excludes data, results and `.env`.
