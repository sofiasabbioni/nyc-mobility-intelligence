# CI reviewer handoff (Nicolò)

## Purpose

How a reviewer checks the repository before merging a pull request.

## GitHub Actions workflow

- Workflow file: `.github/workflows/python-tests.yml` (workflow name: `Python unit tests`).
- Unit-test job: `pytest` (no PostgreSQL service; some database tests may be skipped). It runs `python -m pytest tests -q`.
- Integration-test job: `postgres-integration` (disposable PostgreSQL 16 CI service). It starts a `postgres:16` service container and runs `python -m pytest tests/test_databases_and_benchmark.py -q -ra`. `TEST_POSTGRES: "1"` is set only in this job.
- Python version and triggers: both jobs install Python 3.12 with `actions/setup-python@v5`, install `requirements-dev.txt` and have a 25-minute timeout. The workflow runs on `pull_request` and `push` for `main` and `sofia/nightlife-map-integration`. It can also be started by hand with `workflow_dispatch`.

## Review checklist

1. Open the PR → **Checks** and verify both jobs have completed successfully.
   - `Python unit tests / pytest` and `Python unit tests / postgres-integration` must both have a green check. If one of them is missing, the workflow did not run for this PR.
2. Inspect the `pytest` log; record passed/skipped figures *from that run*, not an earlier run.
   - Click the job, expand the step `Run Python unit tests` and read the last line, for example `167 passed, 5 skipped`. The 5 skipped tests are expected (see the next section). Any failed test, or any other skipped test, needs an explanation in the PR.
3. Inspect the `postgres-integration` log; verify PostgreSQL-dependent tests actually ran and were not silently skipped.
   - Expand the step `Run PostgreSQL integration tests`. The last line must show `12 passed` and no skipped test. A line such as `7 passed, 5 skipped` means the PostgreSQL tests did not run, even if the job is green.
4. Confirm no secrets, generated `.env`, large datasets or unintended outputs were added to the PR.
   - Open **Files changed**. `.env`, `data/raw/`, `data/full/` and `results/` are listed in `.gitignore`, and only `.env.example` is tracked. A PR should not add these files, Parquet files or benchmark CSV/JSON results unless the PR is about them.

## Skipped tests and the PostgreSQL suite

- `tests/test_databases_and_benchmark.py` has 12 tests. 5 of them are marked `@pg` and are skipped unless `TEST_POSTGRES` is `"1"`. The skip reason is `needs PostgreSQL (TEST_POSTGRES=1)`.
- The `pytest` job does not set `TEST_POSTGRES`, so these 5 tests are skipped there. This is expected.
- The `postgres-integration` job sets `TEST_POSTGRES: "1"` and runs only this file against PostgreSQL 16, so all 12 tests must pass there. The `-ra` option prints the reason of every skipped test at the end of the log.
- The 5 PostgreSQL tests are:
  - `test_postgres_load_and_full_benchmark`
  - `test_benchmark_rejects_timings_when_results_differ`
  - `test_benchmark_rejects_databases_from_another_dataset`
  - `test_postgres_loader_is_rerunnable`
  - `test_run_loader_twice_and_with_full_dataset`
- More details are in `docs/CI_POSTGRES_TESTING.md`.

## Troubleshooting

- **Dependency installation fails:** open the failed job and expand the step `Install tested dependencies`. The pip error names the package that failed. Check that package in `requirements-dev.txt`, `loader/requirements.txt` or `app/requirements.txt`. Reproduce the error in a new virtual environment with `python -m pip install -r requirements-dev.txt`, then fix the file or tell its owner.
- **PostgreSQL service unavailable:** open the `postgres-integration` job and expand the step `Initialize containers`. Check that the `postgres:16` container became healthy (health check: `pg_isready -U ci_runner -d taxi_ci`). Then check that the job `env` matches the service: host `127.0.0.1`, port `5432`, user `ci_runner`, database `taxi_ci`. Never point the job at a real database.
- **Test assertion fails:** expand the test step. The summary at the end of the log has a `FAILED` line with the test id, and the traceback above it shows the failing line. Copy the test id and run it locally, for example `python -m pytest "tests/test_databases_and_benchmark.py::test_postgres_loader_is_rerunnable" -q`. A `@pg` test also needs a scratch PostgreSQL database and the variables in `docs/CI_POSTGRES_TESTING.md`, because these tests drop and create schemas. Report the failure to the owner of the code. Do not skip or remove the test.

## Branch protection / permissions

- I could not check the branch protection rules. With my account, the page **Settings → Branches** gives a 404 error, so only the repository owner can see or change these settings.
- For this reason, this guide does not say that a review or a passing check is required by GitHub before a merge.
- Before merging, read the merge box at the bottom of the PR. It shows if GitHub blocks the merge, for example because a review or a check is required.
- Even when GitHub does not block the merge, the team guide asks for a review by a teammate and for both jobs to be green before merging.

## Evidence

- PR #12 (`feature/nicolo-postgres-ci` into `sofia/nightlife-map-integration`) added the `postgres-integration` job. Both checks were green, and its merge commit `369cd9b` is part of `main`: https://github.com/sofiasabbioni/nyc-mobility-intelligence/pull/12
- Successful run of the `Python unit tests` workflow, with both jobs green: https://github.com/sofiasabbioni/nyc-mobility-intelligence/actions/runs/45587564814. In this run the `pytest` job showed `167 passed, 5 skipped` and the `postgres-integration` job showed `12 passed`.
