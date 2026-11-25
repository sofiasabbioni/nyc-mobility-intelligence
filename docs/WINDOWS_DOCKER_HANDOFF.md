# Windows and Docker reproduction handoff (Tamas)

## Scope

This handoff covers reproducing the NYC night-taxi project (PostgreSQL 16 vs DuckDB, Streamlit dashboard) on Windows 10/11 with Docker Desktop. It separates quick checks, which need no TLC data and start no containers, from results that only a full `docker compose up --build` run can produce.

**Can be checked before a full data run** (no TLC download, no containers started):

- Required tools on `PATH` (`git`, `python`, `docker`) and the expected repository layout: `scripts/check_prereqs.ps1`.
- Compose configuration accepted: `docker compose config --quiet` reads `docker-compose.yml` and `.env` without starting services. The prerequisite script also runs this when `docker` is found.
- Local settings: `.env` copied from `.env.example` (defaults `START_MONTH=2026-04`, `TOTAL_MONTHS=4`, `SIZES=1,2,4`, `BUILD_FULL_DATASET=0`).
- Nightlife-related Docker wiring: `tests/test_nightlife_docker_config.py`. It needs host Python with `requirements-dev.txt` and uses a synthetic fixture and a mocked zone-shapes download, not real data.
- Free disk space. The README and Windows guide say "about 6 GB". That is a rough planning figure, not a measured minimum.

**Requires a full data run** (`docker compose up --build`, internet access, Docker Desktop running):

- Docker images build, and services start in the enforced order: `db` healthy → `loader` completes successfully → `benchmark` and `app` start.
- The four default TLC monthly Parquet files, the zone lookup and `taxi_zones.zip` are obtained in `data/raw` (downloaded only if missing).
- Data-quality outcome. The reference values for the default configuration are 15,289,433 source records, 6,658,475 cleaned night trips, 41/41 validation checks passed and zero unaccounted rows (README, 15 Nov run; matched by the 24 Nov Mac reproduction).
- Benchmark outcome. The run passes only if the benchmark prints "Benchmark finished: all equivalence and consistency checks passed." The reference run recorded 18 equivalence checks and 36 consistency checks.
- Dataset fingerprint matching `1c08d359491acd5c4940d03049fdd16d1c27e1d5843dbb507af687cd8f3cd8cd` (README, Mac reproduction).
- Dashboard at http://localhost:8501 with all three tabs and the 2D/3D nightlife maps working.
- Timings, database sizes and sampled container memory. These depend on the machine and must be reported separately from the original run and the Mac run.

**Out of scope:** conclusions about engine performance, cold-cache behaviour (the "first" phase is not a guaranteed cold start), and identical timings across machines.

> A full run rebuilds both databases, removes `results/load_times.csv` at loader start and regenerates the benchmark outputs. Back up `results/` before starting it.

## Windows prerequisites

`scripts/check_prereqs.ps1` is a short, read-only PowerShell script. It installs nothing, edits no files and starts no containers. It is not referenced in the README, `docs/WINDOWS_GUIDE.md` or `docs/REPRODUCIBILITY.md`, and no test covers it (`tests/test_scripts.py` covers only the memory scripts). Its folder checks use relative paths, so run it from the repository root (the folder containing `docker-compose.yml`):

```powershell
.\scripts\check_prereqs.ps1
```

If PowerShell refuses to run scripts because of the execution policy, a one-off, process-scoped alternative that does not change system settings is `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_prereqs.ps1`.

### What the script checks, in order

| # | Check | How (from the code) | Success output | Failure output |
|---|---|---|---|---|
| 1 | `git`, `python`, `docker` are on `PATH` | `Get-Command <name> -ErrorAction SilentlyContinue` | `FOUND: <name> at <full path>` | `MISSING: <name>` (yellow) |
| 2 | Repository layout: `docker-compose.yml`, `.env.example`, `requirements-dev.txt`, `app`, `loader`, `benchmark`, `tests` | `Test-Path <name>`, relative to the current folder | `OK: <name>` under the heading `Repository folder checks:` | `MISSING: <name>` (yellow) |
| 3 | Docker Compose CLI version (only if `docker` was found) | `docker compose version` | The Compose version line, e.g. `Docker Compose version v2.x` | The error from `docker` itself |
| 4 | Compose file accepted (only if `docker` was found) | `docker compose config --quiet`, then tests `$LASTEXITCODE` | Nothing; `--quiet` prints no output when the configuration is valid | Compose's own error text, followed by `ERROR: Compose validation failed` (red) |
| 5 | Closing reminder (always) | `Write-Host` | `These checks do NOT prove that database loading or the dashboard ran successfully` | — |

### How to read the result

- The script never calls `exit 1`. A failed check is shown only as a yellow `MISSING` or a red `ERROR` line, so read the output instead of relying on an exit code.
- If `docker` is missing, steps 3 and 4 are skipped without any message. Only step 1's `MISSING: docker` shows the problem.
- A clean result means: three `FOUND` lines, seven `OK` lines, a Compose version, no `ERROR` line, and the closing reminder.

### What the script does not check

- **That the Docker engine is running.** Neither `docker compose version` nor `docker compose config` needs the daemon (`docs/TESTING.md` records `config --quiet` as valid with "real Docker CLI, no daemon"). Run `docker info` separately; it must show server information.
- **Tool versions.** It only checks that each command can be found. For example, the Python version required for the tests is not checked.
- **That `python` is a real interpreter.** On Windows, `python` may resolve to the Microsoft Store app alias under `...\WindowsApps\`. That counts as `FOUND` even if Python is not installed. Confirm with `python --version`.
- **`.env`, `data\raw`, `data\full`, `results`.** None of these are checked. Every variable in `docker-compose.yml` has a `${VAR:-default}` fallback, so `docker compose config` succeeds without a `.env` file.
- **Disk space, memory given to Docker Desktop, WSL 2 backend, internet access, port 8501.** None of these are checked.
- **That `git` and `python` are needed.** The Docker pipeline itself needs neither (`docs/REPRODUCIBILITY.md`). A `MISSING` line for them blocks only host-side tests and the memory scripts.

The header comment on the script's first line names the file `check_prereps.ps1` (a typo); the real file name is `check_prereqs.ps1`.

### Where the detailed setup steps are

- `docs/REPRODUCIBILITY.md`, sections 1–3: WSL 2 (`wsl --status`, `wsl --install`), installing Docker Desktop with `winget`, `docker --version` / `docker compose version` / `docker info`, creating `data\raw`, `data\full` and `results`, copying `.env`, `docker compose config --quiet`, the free-disk check and `docker system df`.
- `docs/WINDOWS_GUIDE.md`: section 0 (requirements: Docker Desktop with at least 6 GB memory if possible, about 6 GB of disk, optional Python 3.11+), section 2 (exact `data\raw` file names to reuse downloaded Parquet files), section 3 (running the pipeline and the expected success messages) and section 6 (host-side test setup).
- Python version for the host tests: `docs/WINDOWS_GUIDE.md` says 3.11+, and `docs/REPRODUCIBILITY.md` uses `py -3.13`. The final local suite in `docs/TESTING.md` (167 passed, 5 skipped) ran on Python 3.12.

## Docker configuration checks

These checks were made by reading the files and by running `docker compose config` in a Linux cloud workspace (Docker Compose CLI v5.7.1, no `.env` present, no containers started). `docker compose config --quiet` exited with code 0. This is **not** a run on the Windows machine (see "Verification performed on your machine").

1. **Start-up order is enforced by healthcheck and completion conditions.**
   File/setting: `docker-compose.yml`, `services.*.depends_on` and `services.db.healthcheck`.
   Evidence:
   - `db` (image `postgres:16`) has a healthcheck `pg_isready -U ${POSTGRES_USER:-postgres} -d ${POSTGRES_DB:-taxi}` (every 3 s, timeout 3 s, 30 retries).
   - `loader` waits for `db: service_healthy`.
   - `benchmark` waits for `db: service_healthy` and `loader: service_completed_successfully`.
   - `app` waits only for `loader: service_completed_successfully`, so the dashboard can open while the benchmark is still running, as the guides describe.
   - The resolved `docker compose config` output shows the same conditions. `tests/test_dashboard_and_config.py::test_compose_file_and_dockerfiles` asserts the four service names, these four conditions and that `db` has a healthcheck.
   - A loader that exits non-zero therefore stops `benchmark` and `app` from starting.

2. **Data and result storage: raw files on the host, working data in volumes, read-only where only read.**
   File/setting: `docker-compose.yml`, `services.*.volumes` and top-level `volumes`.
   Evidence: the mounts per service, as listed in `docker-compose.yml` and in the resolved config:

   | Mount (container path) | `loader` | `benchmark` | `app` |
   |---|---|---|---|
   | `./data/raw` → `/data/raw` (host folder) | read-write | read-only | read-only |
   | `taxi_data` → `/data` (named volume: clean files, DuckDB) | read-write | read-only | — |
   | `./data/full` → `/data/full` (host folder) | read-write | — | — |
   | `./results` → `/results` (host folder) | read-write | read-write | read-only |

   `db` stores PostgreSQL data in the named volume `pgdata`. With `name: taxi_project`, Docker names the volumes `taxi_project_pgdata` and `taxi_project_taxi_data`, which is why `docker compose down` keeps them and only `down -v` deletes them. Two environment values matter for the nightlife map:
   - `loader` sets `WITH_ZONE_SHAPES: "1"`, so `loader/download_data.py` also fetches `taxi_zones.zip` into `data/raw`.
   - `app` sets `DATA_DIR=/data/raw`, which is where `app/nightlife_map.py` looks for `taxi_zones.zip`.

   Raw input files, generated data and results stay on the host or in named volumes and are never built into an image.

3. **Credentials, data and results are kept out of images and Git, and only the dashboard port is published.**
   File/setting: `.dockerignore`, `.gitignore`, `.env.example`, `docker-compose.yml` `ports`, and the three Dockerfiles.
   Evidence:
   - `.dockerignore` excludes `.git`, `.env`, `data/`, `results/`, `tests/`, `__pycache__/` and `*.pyc` from the build context. The `loader` and `benchmark` Dockerfiles copy only their own folder plus `sql/`. `app/Dockerfile` copies five named `.py` files and `.streamlit/`.
   - `.gitignore` excludes `.env`, `.venv/` and the contents of `data/raw`, `data/full` and `results` (keeping only `.gitkeep`).
   - Credentials reach the containers only as environment variables (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`). If `.env` is absent they fall back to `postgres` / `postgres` / `taxi`, which is why `config` succeeds without a `.env`.
   - Only `app` publishes a port (`8501:8501`; the resolved config shows one `published: "8501"`). `db` publishes none, so PostgreSQL is reachable only inside the Compose network. This matches `docs/WINDOWS_GUIDE.md` section 6.

**Related observations (not counted above):**

- All three Python images use the tag `python:3.12-slim` and the database uses `postgres:16`. These are tags, not immutable digests, so a later pull can bring a different patch build (`docs/REPRODUCIBILITY.md`).
- Python packages are pinned exactly (`==`) in `loader/`, `benchmark/` and `app/requirements.txt`, and `pandas==3.1.1` is identical in `loader` and `app`.
- `tests/test_dashboard_and_config.py` also checks that every `${VAR}` used in `docker-compose.yml` is documented in `.env.example`.

## What the automated test verifies

`tests/test_nightlife_docker_config.py` has two test functions. Neither one starts Docker, builds an image or uses the network. Both run on the host with Python and the packages in `requirements-dev.txt` (`pytest`, `pyyaml`, plus the loader and app requirements). `tests/conftest.py` puts `loader/`, `sql/`, `benchmark/`, `app/`, `tests/` and `scripts/` on `sys.path`, which is how the test can `import download_data`.

### 1. `test_nightlife_compose_configuration_and_pandas_pin`

Reads `docker-compose.yml` as plain YAML (`yaml.safe_load`) and `loader/requirements.txt` as text. It asserts:

| Assertion | What it pins down |
|---|---|
| `loader.environment.WITH_ZONE_SHAPES == "1"` | The loader also fetches `taxi_zones.zip`, the geometry used by the nightlife map |
| `loader.environment.POSTGRES_HOST == "db"` | The loader reaches PostgreSQL through the Compose service name, inherited from the shared `x-env` block |
| `"./data/raw:/data/raw" in loader.volumes` | The host `data/raw` folder is mounted into the loader **without** `:ro`, so the loader can write downloaded files there |
| `loader.environment.DATA_DIR == "/data"` | The loader's data root is the `taxi_data` volume; raw files are under `/data/raw` |
| `"./data/raw:/data/raw:ro" in app.volumes` | The app sees the same raw folder, **read-only** |
| `app.environment.DATA_DIR == "/data/raw"` | `app/nightlife_map.py` looks for `DATA_DIR/taxi_zones.zip`, so this points the app at the file the loader wrote |
| `"pandas==3.1.1"` is a line in `loader/requirements.txt` | The loader's pandas pin, which `docs/REPRODUCIBILITY.md` says matches the app's |

### 2. `test_enabled_downloader_requests_only_synthetic_zone_shapes`

Uses the `raw_data` fixture: four synthetic monthly Parquet files (2026-04 to 2026-07, 3,000 rows plus hand-made edge rows each) and a synthetic `taxi_zone_lookup.csv` in a temporary `DATA_DIR/raw`, with all project settings cleared. Then it:

1. asserts that `taxi_zones.zip` does not exist yet;
2. sets `WITH_ZONE_SHAPES=1` (as the loader service does);
3. replaces `urllib.request.urlopen` with a fake that records every request and returns the bytes `b"synthetic taxi-zone archive"`;
4. runs `download_data.main()` and asserts:
   - it returns `0`;
   - exactly **one** request was made: `https://d37ci6vzurychx.cloudfront.net/misc/taxi_zones.zip` with a 120 s timeout;
   - the file written to `raw/taxi_zones.zip` holds exactly the fake bytes.

This shows that when the monthly files and the zone lookup are already present, the downloader uses them as they are and requests only the missing shapes file. That matches the Windows guide's promise that files copied into `data\raw` are not downloaded again.

### What these tests do NOT prove

- **No Docker at all.** The Compose file is read as YAML, not resolved with `docker compose config`, and no images are built or containers started. Variable interpolation, `.env` handling, bind-mount behaviour on Windows/WSL 2 and the start-up order are not exercised. Start-up order is asserted separately, also only as YAML, in `tests/test_dashboard_and_config.py::test_compose_file_and_dockerfiles`.
- **No fresh full pipeline run.** No real TLC data is downloaded, preprocessed or loaded, no databases are built and no benchmark is run. Passing tells you nothing about the 41 validation checks, the 18 equivalence checks or the 36 consistency checks, or about the dataset fingerprint.
- **No real `taxi_zones.zip`.** The "archive" is a short byte string, not a ZIP. `download_data.py` does not validate the shapes file (it checks only the Parquet files and the zone lookup header). A damaged or HTML `taxi_zones.zip` would therefore pass the loader and appear only in the dashboard. There, `app/nightlife_map.py` shows "Map geometry failed, but tables are available: …", or "Missing zone geometry: …" if the file is absent.
- **No dashboard rendering.** The test does not open the app or draw the 2D/3D maps.
- **Only part of the configuration.** It does not check the `benchmark` or `db` services, the `taxi_data`/`results` mounts, port 8501, or the pandas pin in `app/requirements.txt`. Some of these are covered by `tests/test_dashboard_and_config.py`.
- **Not a Windows result by itself.** A pass on another machine says nothing about your Windows setup. Section 5 records what was actually run there.

**Reference run (not on the Windows machine):** in the Linux cloud workspace used to write this document, `python -m pytest tests/test_nightlife_docker_config.py -v` on Python 3.12.3 (pytest 9.2.0, PyYAML 6.0.4, pyarrow 26.0.1, pandas 3.1.1, numpy 2.5.4) reported **2 passed**. The CI workflow `.github/workflows/python-tests.yml` runs the full `tests` folder, including this file, on Ubuntu with Python 3.12.

## Verification performed on your machine

Run by Tamas on Windows in PowerShell, from the repository root (`Test-Path .\docker-compose.yml` → `True`). User names and drive paths below are replaced with placeholders.

- PowerShell prerequisite script:
  - **Attempt 1:** `.\scripts\check_prereqs.ps1` was **blocked**: "`check_prereqs.ps1 cannot be loaded. The file … is not digitally signed. You cannot run this script on the current system.`" (`PSSecurityException`, `UnauthorizedAccess`). `Get-ExecutionPolicy -List` showed `LocalMachine = AllSigned`, with all other scopes (`MachinePolicy`, `UserPolicy`, `Process`, `CurrentUser`) `Undefined`. Under `AllSigned`, every script must be signed, and the repository's script is not. The policy was not set by group policy.
  - **Attempt 2:** `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_prereqs.ps1` (bypass for that one process only; no policy was changed) **completed**:

    ```text
    FOUND: git at <Program Files>\Git\cmd\git.exe
    FOUND: python at <user profile>\AppData\Local\Programs\Python\Python313\python.exe
    FOUND: docker at <user profile>\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe

    Repository folder checks:
    OK: docker-compose.yml
    OK: .env.example
    OK: requirements-dev.txt
    OK: app
    OK: loader
    OK: benchmark
    OK: tests

    Docker Compose version:
    Docker Compose version v5.6.1

    Checking Compose syntax (without starting containers):

    These checks do NOT prove that database loading or the dashboard ran successfully
    ```

    All three tools were found, all seven repository items were present, and no `MISSING` or `ERROR` line was printed. The `python` path is a regular per-user Python 3.13 install, not the Microsoft Store alias. The empty line after "Checking Compose syntax" means `docker compose config --quiet` returned exit code 0, because the script prints `ERROR: Compose validation failed` only on a non-zero exit code.

- `docker compose config` and Docker engine:
  - `Test-Path .\.env` → `False`. No `.env` had been created yet, so the check below used the `${VAR:-default}` values built into `docker-compose.yml`, which are the same as `.env.example`.
  - `docker info --format "Server {{.ServerVersion}} | {{.OperatingSystem}} | CPUs {{.NCPU}} | Memory {{.MemTotal}}"` → `Server 29.9.2 | Docker Desktop | CPUs 16 | Memory 7188905984`. The Docker Desktop engine was running and reachable. It reported 16 CPUs and 7,188,905,984 bytes of memory (≈ 6.70 GiB, ≈ 7.19 GB). This is above the "at least 6 GB if you can" suggestion in `docs/WINDOWS_GUIDE.md`, which is guidance rather than a verified minimum.
  - `docker compose config --quiet; "exit=$LASTEXITCODE"` → `exit=0`. The Compose configuration was accepted. This confirms the prerequisite script's silent pass. It does not show that images build, data downloads or the pipeline completes.

- Relevant Python test:
  - Host Python: `python --version` → `Python 3.13.0`.
  - Environment: `python -m venv .venv`, then `.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`. The venv's Python was called directly because `AllSigned` would also block `.\.venv\Scripts\Activate.ps1`. The pip output was not recorded; the test run below shows the packages were importable. `.venv/` is excluded by `.gitignore`.
  - Command: `.\.venv\Scripts\python.exe -m pytest tests\test_nightlife_docker_config.py -v`
  - Result: **2 passed in 3.91 s**, 0 failed, 0 skipped (`platform win32 -- Python 3.13.0, pytest-9.2.0, pluggy-1.7.0`, plugin `anyio-4.16.0`):

    ```text
    tests/test_nightlife_docker_config.py::test_nightlife_compose_configuration_and_pandas_pin PASSED [ 50%]
    tests/test_nightlife_docker_config.py::test_enabled_downloader_requests_only_synthetic_zone_shapes PASSED [100%]
    ```

  This matches the Linux reference run in section 4 (Python 3.12.3, 2 passed). Within the limits listed in section 4, it confirms that the nightlife Compose settings and the downloader's shapes-only request behave as expected on this Windows host.

**Not run on this machine:** `docker compose up --build` (the full data pipeline), the full test suite (`python -m pytest tests -q`) and the memory sampler. The data-quality, equivalence and consistency outcomes, the dataset fingerprint and the dashboard have therefore **not** been reproduced on this Windows machine by these checks.

## Troubleshooting and limits

Run diagnostics from the repository root. Each "safe next step" below only reads state or affects a single process; none of them deletes data or changes shared configuration.

### Failure symptoms and safe next steps

1. **Prerequisite script is blocked: "`check_prereqs.ps1 cannot be loaded … is not digitally signed`".**
   Seen on this machine (section 5). The script is unsigned, and the execution policy requires signed scripts.
   Safe next step:
   - Run `Get-ExecutionPolicy -List` to see which scope sets the policy.
   - If it comes from `LocalMachine` or `CurrentUser` (here: `LocalMachine = AllSigned`), run the script once with `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_prereqs.ps1`. This affects only that process.
   - If it comes from `MachinePolicy` or `UserPolicy`, it is set by an administrator: do not try to work around it; record the check as blocked.
   - Do not change the machine-wide policy for this project.
   - The same policy also blocks `.\.venv\Scripts\Activate.ps1`. Call `.\.venv\Scripts\python.exe` directly instead.

2. **The prerequisite script passes, but Docker is not usable.**
   `check_prereqs.ps1` runs only `docker compose version` and `docker compose config --quiet`, and neither needs the Docker engine (section 2). A clean script output therefore does not mean Docker Desktop is running. The real failure appears later in `docker compose up` as `error during connect … pipe/dockerDesktopLinuxEngine` (`docs/WINDOWS_GUIDE.md`, Troubleshooting).
   Safe next step: run `docker info` before any `up`. It must print server information (here: `Server 29.9.2 | Docker Desktop`). If it fails, start Docker Desktop, wait until it reports that it is running, and rerun `docker info`.

3. **The dashboard shows "Map geometry failed, but tables are available: …" or "Missing zone geometry: …".**
   `loader/download_data.py` downloads `taxi_zones.zip` when `WITH_ZONE_SHAPES=1`, but never checks that it is a valid archive. It validates only the Parquet files and the zone-lookup header. The section-4 test also writes a non-ZIP byte string and still passes. A damaged or HTML download therefore passes the loader and fails only in `app/nightlife_map.py`, which catches the error and shows the warning. If the file is absent, the app shows the "Missing zone geometry" message instead.
   Safe next step:
   - Check `data\raw\taxi_zones.zip` with `Get-Item .\data\raw\taxi_zones.zip | Select-Object Length`. A tiny file is suspicious.
   - Check that it opens as a ZIP, for example in Explorer.
   - The downloader never overwrites an existing file. So replace only that one file with a fresh copy of `https://d37ci6vzurychx.cloudfront.net/misc/taxi_zones.zip`, or move it aside so the next loader run downloads it again.
   - The tables and the other dashboard tabs are not affected.

4. **The loader stops with `ERROR: could not download …`, or `ERROR: <file> cannot be read as Parquet …`.**
   These messages come from `loader/download_data.py`. A failed step stops the loader, and `Loader finished.` is not printed (`loader/run_loader.py`). `benchmark` and `app` then do not start, because they wait for `loader: service_completed_successfully` (section 3).
   Safe next step:
   - Run `docker compose ps -a` and `docker compose logs --tail 100 loader`, and read the file name and URL in the message.
   - For a download error, check internet access, or download that one file in a browser into `data\raw` under the exact name.
   - For a damaged file, replace only the named file; existing files are never deleted or overwritten automatically.

5. **The benchmark prints `ERROR: … equivalence/consistency check(s) failed, timings NOT accepted`, or `… was loaded from a different dataset`.**
   These messages come from `benchmark/benchmark.py`.
   - The first means the two engines returned different results. The timings from that run must not be reported.
   - The second means the settings changed after the databases were built.

   Safe next step: keep the log and inspect `results\equivalence.csv` and `results\db_consistency.csv`. For "different dataset", rerun `docker compose up` so the loader rebuilds the databases. Report a persistent mismatch to the maintainers instead of rerunning until it passes.

6. **Disk full or very slow.**
   The two Windows documents disagree here. `docs/WINDOWS_GUIDE.md` (Troubleshooting) suggests `docker system prune`, while `docs/REPRODUCIBILITY.md` section 5 advises against broad cleanup commands as a first response, because they affect Docker resources beyond this project.
   Safe next step:
   - Run `docker system df` to see usage.
   - Stop the project with `docker compose down`, which keeps the `pgdata` and `taxi_data` volumes.
   - Use `docker compose down -v` only if you deliberately want to delete this project's databases and clean files.
   - Check Docker Desktop's disk-image setting.
   - Avoid `docker system prune` unless you have checked what else on the machine it would remove.

   The two documents should be aligned in a separate change.

### Limits of this handoff

- On this Windows machine, only the read-only checks in section 5 were run. The full pipeline (`docker compose up --build`), real TLC data, the 41 validation checks, the 18 equivalence and 36 consistency checks, the dataset fingerprint, the dashboard and the 2D/3D maps were **not** reproduced here.
- The test run here was the focused nightlife file only (2 tests), not the full suite.
- The Compose check used the built-in defaults because no `.env` existed. A later `.env` with different values needs `docker compose config --quiet` to be run again.
- Image tags (`python:3.12-slim`, `postgres:16`) are not pinned by digest, so later pulls can differ (section 3).
- Docker Desktop on this machine reported about 6.70 GiB of memory. The project documents give no verified minimum, so whether a full run completes within that allowance is unknown until it is tried.
- A full run rebuilds both databases and regenerates the benchmark outputs. Back up `results/` before running it.
- Any timings from a later Windows run must be reported separately from the original run and the Mac run.

### Do not commit

- `.env` (it holds the database credentials; already excluded by `.gitignore` and `.dockerignore`).
- Anything in `data/raw`, `data/full` or `results/`.
- `.venv/`.
- Machine-specific paths or user names. In this document they are replaced with placeholders such as `<user profile>`.
