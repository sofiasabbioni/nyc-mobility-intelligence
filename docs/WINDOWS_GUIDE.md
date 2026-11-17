# Windows PowerShell guide

All commands are for **PowerShell** (Windows 10/11). Replace the folder if yours is different.

## 0. Once: requirements
1. **Docker Desktop**, started (whale icon steady; Settings > Resources: give it at least 6 GB of memory if you can).
2. About **6 GB** of free disk (clean files, two databases, Docker images).
3. Optional, only to run the tests or the memory sampler: **Python 3.11+** (`python --version`).

## 1. Put the project in a folder
Unzip `taxi_project.zip` so that you have `C:\Users\<you>\Desktop\taxi_project\docker-compose.yml`.
```powershell
cd $HOME\Desktop\taxi_project
Copy-Item .env.example .env
```
Open `.env` only if you want to change settings (night window, number of runs, ...). Defaults are fine.

## 2. Use the Parquet files you already downloaded (no second download)
The loader looks for the original files in the folder `data\raw` of the project and **uses them as they
are**. Copy them there with exactly these names:
```
data\raw\yellow_tripdata_2026-04.parquet
data\raw\yellow_tripdata_2026-05.parquet
data\raw\yellow_tripdata_2026-06.parquet
data\raw\yellow_tripdata_2026-07.parquet
data\raw\taxi_zone_lookup.csv
```
```powershell
Copy-Item "$HOME\Downloads\yellow_tripdata_2026-0*.parquet" .\data\raw\
Copy-Item "$HOME\Downloads\taxi_zone_lookup.csv" .\data\raw\
Get-ChildItem .\data\raw
```
If a name has a suffix such as `(1)`, rename it. If a file is missing, the loader downloads only that one.
A damaged file stops the run with a message; it is never deleted or replaced automatically. Your originals are
never modified (the run records their SHA-256 in `results\data_quality\raw_files.csv`).

## 3. Run everything
```powershell
docker compose up --build
```
First run: builds the images, loads the data, runs the benchmark. Watch for `Loader finished.` and then
`Benchmark finished: all equivalence and consistency checks passed.` When the dashboard container is up, open
**http://localhost:8501** (the page shows partial results while the benchmark is running; refresh at the end).

If the loader prints `ERROR` or a `[FAIL]` check, or the benchmark says timings were NOT accepted, read the message;
nothing is hidden. The files `results\data_quality\validation.csv` and `results\equivalence.csv` have the details.

Useful variants:
```powershell
docker compose up --build -d db                       # only the database
$env:BUILD_FULL_DATASET = "1"; docker compose up --build   # also build the combined dataset in data\full
$env:BENCH_RUNS = "10"; docker compose up              # more measured runs (or edit .env)
docker compose run --rm loader python build_full_dataset.py   # only the combined dataset
```

## 4. Optional: peak memory
Two terminals in the project folder. **Before** `docker compose up`:
```powershell
python scripts\sample_docker_stats.py        # terminal 1, leave it running
```
Start `docker compose up` in terminal 2. When the benchmark has finished, press Ctrl+C in terminal 1 and run:
```powershell
python scripts\summarize_memory.py           # writes results\memory_summary.csv
```
Refresh the dashboard. (If you rerun the benchmark, delete `results\memory.csv` first.)

## 5. Run again, change settings, start from zero
* `docker compose up` again: clean files are **reused** only if the settings and the raw files are unchanged;
  otherwise they are rebuilt (message `rebuilding`). The databases are rebuilt every run; the benchmark restarts.
* Change `NIGHT_START`, `NIGHT_END`, `START_MONTH`, `TOTAL_MONTHS` in `.env`, then `docker compose up`: everything
  that depends on it is rebuilt, nothing stale is used (the benchmark refuses databases that do not match).
* Force a rebuild: `$env:FORCE_REBUILD = "1"; docker compose up`.
* Delete databases and clean files, keep your raw files: `docker compose down -v`.

## 6. Run the tests
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest tests -q
```
This runs 63 tests. 5 more integration tests (loading into PostgreSQL, the full benchmark, rejection of
mismatching results) are **skipped** unless a PostgreSQL server is reachable and you set `TEST_POSTGRES=1`
plus `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` (the compose database does not
publish its port by default). They were run, and passed, against a local PostgreSQL 16 (see `docs/TESTING.md`).

## 7. After the run: what to copy into the report
`results\` has everything (CSV files, `environment.txt`, `explain\*.json`). Fill `docs\MACHINE.md` with your CPU, RAM,
disk type and the Docker memory limit. The numbers in the report must come from your own `results\` files.

## Troubleshooting
| Symptom | Cause / fix |
|---|---|
| `docker : The term 'docker' is not recognized` | Docker Desktop not installed or not started. |
| `error during connect ... pipe/dockerDesktopLinuxEngine` | Start Docker Desktop and wait until it is running. |
| `port is already allocated` (8501) | Another app uses it; stop it or change the port in `docker-compose.yml`. |
| Loader: `... cannot be read as Parquet` | The file in `data\raw` is damaged or an HTML page; download it again and replace it. |
| Loader: `could not download ...` | No internet / TLC site blocked. Download the file in the browser into `data\raw`. |
| Benchmark: `loaded from a different dataset` | Settings changed after the databases were built. Run `docker compose up` again (the loader rebuilds). |
| Benchmark: `timings NOT accepted` | The two engines returned different results or counts. See `results\equivalence.csv`. This is a bug to report, not a result. |
| Disk full / very slow | Docker Desktop disk image is full: `docker system prune`, or increase it in Settings > Resources. |
