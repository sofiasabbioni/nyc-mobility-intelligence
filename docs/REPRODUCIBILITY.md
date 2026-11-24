# Reproducibility and first run on Windows

This supplement is for a fresh Windows 10/11 PC using PowerShell. It is based on the current
`docker-compose.yml`, `.env.example`, `loader/run_loader.py`, `README.md`, and `docs/WINDOWS_GUIDE.md`.
It describes expected behavior; it does not claim that Docker was run on your PC.

## What “reproducible” means here

The default run uses four consecutive TLC yellow taxi months, April through July 2026, and the zone lookup. The loader downloads a missing raw file, but uses an existing file as-is and does not replace or modify it. The Compose loader also enables the optional official `taxi_zones.zip` download into `data/raw`; the app reads that ZIP on demand. The loader requirements pin `pandas==3.1.1`, matching the app's pin. It cleans the night-trip subset (pickup 18:00 to before 06:00, New York time), checks the data, and loads PostgreSQL and DuckDB. The optional complete dataset is off by default.


The nightlife metrics exporter is integrated into the automated pipeline. `loader/run_loader.py` invokes `nightlife_metrics.main([])` after preprocessing, producing the zone-level, hourly, and validation outputs in `results/nightlife/`.

The real-data nightlife outputs have been generated and successfully displayed in the Streamlit dashboard on the original project machine. This confirms that the integrated workflow has worked in that environment.

However, successful execution on the original machine and passing automated tests do not establish independent reproducibility. A complete fresh-machine reproduction has not yet been independently verified.


The clean-data manifest fingerprints the relevant settings and raw-file contents. Clean files can be reused when those inputs are unchanged; the PostgreSQL and DuckDB databases are rebuilt on every loader run. `load_times.csv` is removed at loader startup, and the benchmark runs again. Keep copies of any results you need to retain before a later run.

This is not a guarantee of identical elapsed times or bit-for-bit identical environments. Timings depend on the PC, Docker/WSL resources, disk, host activity, and cache state. The Compose file uses image tags such as `postgres:16` and `python:3.12-slim`, not immutable image digests, so the exact image patch/build can change when images are refreshed. A seeded benchmark order helps repeat the query ordering; it does not eliminate machine and cache variability. The benchmark's “first” phase is not a guaranteed cold-cache measurement.

## 1. Install and confirm Docker

WSL 2 is the recommended Docker Desktop backend. Check it in PowerShell:

```powershell
wsl --status
```

If WSL is not installed, open PowerShell **as Administrator**, run the command below, and restart Windows
if prompted:

```powershell
wsl --install
```

After restarting, open ordinary PowerShell and run `wsl --status` again. If needed, set WSL 2 as the default from an elevated PowerShell:

```powershell
wsl --set-default-version 2
```

Install Docker Desktop using `winget` if it is available:

```powershell
winget install --id Docker.DockerDesktop -e
```

Finish installer prompts, launch Docker Desktop, and wait until it reports that Docker is running. Confirm that its WSL 2 based engine is enabled. In a new ordinary PowerShell window:

```powershell
docker --version
docker compose version
docker info
```

The first two commands should print version information. `docker info` should show client and server information. A daemon/pipe connection error means Docker Desktop is not ready or running; do not continue until `docker info` can reach the server.

Python and Git are not needed to run the Docker pipeline. Docker Desktop, a running Docker engine, and internet access for image and data downloads are needed. The Windows guide suggests giving Docker Desktop at least 6 GB of memory if available; this is guidance, not a verified minimum for every machine or dataset.

## 2. Open the project root and prepare local folders

Extract the project so that `docker-compose.yml` is at the top level. For the example folder used in the Windows guide:

```powershell
Set-Location "$HOME\Desktop\taxi_project"
Get-Location
Test-Path .\docker-compose.yml
```

`Get-Location` should show the project folder and `Test-Path` should print `True`. If it prints `False`, change to the folder that actually contains `docker-compose.yml`.

Create the directories used as Compose bind mounts and copy the default settings only if no `.env` exists:

```powershell
New-Item -ItemType Directory -Force .\data\raw, .\data\full, .\results | Out-Null
if (-not (Test-Path .\.env)) { Copy-Item .\.env.example .\.env }
```

No output is expected. `.env` is local configuration and contains database credentials. Do not share it. Defaults in `.env.example` are:

* `START_MONTH=2026-04`, `TOTAL_MONTHS=4`, `SIZES=1,2,4`
* `NIGHT_START=18`, `NIGHT_END=6`
* `BENCH_RUNS=5`, `BENCH_WARMUPS=2`, seeded shuffled query order
* `BUILD_FULL_DATASET=0`, `FORCE_REBUILD=0`

Validate Compose configuration before starting services:

```powershell
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw "Compose configuration is invalid." }
Write-Host "Compose configuration is valid."
```

Expected on success: `Compose configuration is valid.` The `config` command validates configuration; it does not prove that the engine can start containers, download the source data, or finish a pipeline run.

To check the nightlife-related settings without starting services or downloading TLC data:

```powershell
Select-String '^pandas==3\.1\.1$' .\loader\requirements.txt
python -m pytest tests\test_nightlife_docker_config.py -q
```

The focused test checks the Compose loader/app raw-data settings and uses a mocked shapes response with the synthetic raw-data fixture.

## 3. Check disk space and know the main costs

Check Windows C: free space:

```powershell
Get-PSDrive -Name C | Select-Object Name, @{Name='FreeGB'; Expression={[math]::Round($_.Free / 1GB, 1)}}
```

The README and Windows guide say **about 6 GB free**. Treat that as a rough planning estimate, not a measured minimum, maximum, or guarantee. Docker images, source Parquet files, generated databases, and Docker's virtual disk consume space; the actual real-data size and Windows peak use have not been verified.
Docker Desktop's virtual disk may be stored/configured separately from the project directory. Keep more free space than the estimate if possible. Setting `BUILD_FULL_DATASET=1` creates an additional all-trips, all-columns dataset and uses more disk.

After Docker is running, `docker system df` reports Docker-managed image/container/volume usage:

```powershell
docker system df
```

Its output is a usage report, not a promise of how much free space the full run will need. The first run also needs internet access to obtain Docker images and any missing TLC files; time varies with download speed, disk, memory, and CPU. No reliable Windows completion time has been measured. The benchmark's default workload includes a first execution, two warm-ups, and five measured runs for multiple queries and the 1-, 2-, and 4-month sizes. Increasing `BENCH_RUNS` or enabling the complete dataset takes longer.

## 4. Start and recognize a successful run

From the project root, run:

```powershell
docker compose up --build
```

This runs in the foreground. Compose builds the loader, benchmark, and app images, starts PostgreSQL, then runs the loader. The benchmark and app wait for successful loader completion. With no raw files already present, the loader downloads the four default monthly Parquet files and zone lookup; with existing raw files it reports that they were found and uses them. It prints row counts and a total source-row count before preprocessing/loading.

Expected success messages include:

```text
Loader finished.
Benchmark finished: all equivalence and consistency checks passed.
```

Open <http://localhost:8501> in a browser. The dashboard can show partial results while the benchmark is running; refresh it when the benchmark finishes. Docker build and download progress text varies, so do not expect every log line to match exactly.

After completion, this command can show the service states:

```powershell
docker compose ps -a
```

The database and app should be running. `loader` and `benchmark` are run-to-completion services, so `Exited (0)` for those services is expected after successful completion. A nonzero exit or an `ERROR` / `[FAIL]` message is not a successful run; inspect the logs and result reports before rerunning.

### These success signals are different

* `docker compose config --quiet` succeeding means only that Compose configuration was accepted.
* `Loader finished.` means that the loader's steps completed. It is not a pytest result.
* `Benchmark finished: all equivalence and consistency checks passed.` means this run passed the benchmark's data/result checks and accepted its timings. It is not a general claim that one database is faster.
* `python -m pytest tests -q` is a separate automated test suite. It tests code paths with test fixtures, not the real TLC dataset or Windows Docker runtime.

`docs/TESTING.md` reports past test runs in a Linux cloud environment using synthetic data: 63 passed and 5 skipped without PostgreSQL, and 68 passed with a separate local PostgreSQL 16 server. Those are reported historical results, not tests run on this fresh Windows PC. A skipped test did not run and must not be counted as passed. The Windows guide also notes that Compose does not publish the PostgreSQL port to the host by default, so its database is not automatically available for the host-side PostgreSQL integration tests.

If you separately install Python 3.13 and want to run the local suite, these commands avoid PowerShell virtual-environment activation:

```powershell
py -3.13 --version
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
```

The documented no-PostgreSQL outcome is 63 passed and 5 skipped; your output is the result for your run. This document-editing task did not run pytest or Docker Compose.

## 5. Stop, preserve, or remove this project's stored data

Run these commands from the same project root:

```powershell
docker compose down
```

`docker compose down` stops and removes this project's containers and Compose network. It **keeps** the named volumes `pgdata` and `taxi_data`, so PostgreSQL's stored database and the clean/DuckDB data remain for the next run. It does not remove the built images or the Windows folders `data\raw`, `data\full`, or `results`.

To intentionally remove this project's named volumes too, the supported spelling is lowercase `-v`:

```powershell
docker compose down -v
```

`down -v` also deletes this Compose project's named PostgreSQL and taxi-data volumes. The next `up` must initialize/reload those databases and clean data. 
It still does not delete the bind-mounted Windows folders `data\raw`, `data\full`, and `results`; preserve or back up result files separately if you need them. 
Capital `-V` is not the documented Compose flag; use lowercase `-v` (or `--volumes`). 
Do not use the volume removal option unless you intend to discard the stored database and DuckDB/clean files.

`down` does not stop Docker Desktop itself. It also does not delete Docker images. Avoid broad cleanup commands such as `docker system prune` as a first response to disk pressure because they can affect Docker resources beyond this project.

## 6. Safe troubleshooting

Run diagnostic commands from the project root:

```powershell
docker compose ps -a
docker compose logs --tail 100 loader benchmark app
docker system df
```

* **`docker` is not recognized:** install Docker Desktop, reopen PowerShell, and check `docker --version`.
* **Cannot connect to `dockerDesktopLinuxEngine`:** start Docker Desktop and wait for it to report ready; verify with `docker info`.
* **Compose configuration error:** confirm the current directory contains `docker-compose.yml` and that `.env.example` was copied to `.env`. Run `docker compose config --quiet` again.
* **Loader cannot download a file:** check internet access and the displayed URL. The loader downloads missing files, but will not overwrite a file that already exists. If an existing Parquet file is damaged, verify the named file and replace only that file with a valid download before retrying.
* **Loader validation or benchmark equivalence fails:** keep the log and inspect `results\data_quality\validation.csv` and `results\equivalence.csv`. Do not report rejected timings as valid results; report a persistent mismatch to the project maintainers.
* **Port 8501 is already in use:** close the other application using that port and retry. Do not make a local unreviewed change to the shared Compose file.
* **Disk is nearly full or the run is very slow:** inspect `docker system df`, stop this project with `docker compose down`, and free space or review Docker Desktop's disk settings. Use `docker compose down -v` only if you deliberately want to delete this project's named data volumes. Do not delete the raw input files or results unless you have chosen to do so and have backups where needed.

## Known limitations

The Windows guide and `docs/TESTING.md` state that Windows PowerShell commands, Docker build/start behavior on Windows/WSL2, real TLC file sizes and processing scale, and Windows memory sampling have not been verified in the documented test environment. The automated test suite and synthetic pipeline checks are useful evidence about code paths, not proof that this full real-data run will work or finish within a particular time or disk budget on every PC. Record your own machine settings and use only your own `results/` files for performance conclusions.
