# PostgreSQL integration tests in GitHub Actions

Owner: Nicolò (QA). Written on 19 Nov 2026.

The workflow `.github/workflows/python-tests.yml` runs two independent jobs. The first one is the original Python
unit-test job, unchanged. The second one runs the five PostgreSQL integration tests that are otherwise skipped,
against a disposable PostgreSQL 16 database that exists only for the duration of the CI job.

Triggers are unchanged: `pull_request` and `push` on `sofia/nightlife-map-integration` and `main`, plus manual
`workflow_dispatch`.

## The two jobs

| Job | What it runs | PostgreSQL | `TEST_POSTGRES` | Expected result |
| --- | --- | --- | --- | --- |
| `pytest` (existing) | `python -m pytest tests -q` | none | not set | everything passes, 5 tests skipped |
| `postgres-integration` (new) | `python -m pytest tests/test_databases_and_benchmark.py -q -ra` | `postgres:16` service container | `"1"` (this job only) | 12 passed, 0 skipped (confirm in the job log) |

Both jobs use the same Python 3.12 setup and `requirements-dev.txt` installation.

## The five tests that were skipped

In `tests/test_databases_and_benchmark.py` the tests marked `@pg` are skipped unless `TEST_POSTGRES` is exactly `"1"`.
Without it pytest reports `needs PostgreSQL (TEST_POSTGRES=1)`. The module has 12 tests: 7 that always run (DuckDB and
helpers) and these 5:

1. `test_postgres_load_and_full_benchmark`: loads two synthetic dataset sizes into PostgreSQL and DuckDB, runs the full
   benchmark, and checks run phases, engines, equivalence results, the EXPLAIN plan for Q5 with and without the index,
   and the schema of every CSV the dashboard reads.
2. `test_benchmark_rejects_timings_when_results_differ`: alters DuckDB data and checks that the benchmark refuses to
   publish timings.
3. `test_benchmark_rejects_databases_from_another_dataset`: changes a setting so the databases become stale and checks
   that the benchmark refuses to use them.
4. `test_postgres_loader_is_rerunnable`: loading twice replaces the data and does not duplicate rows.
5. `test_run_loader_twice_and_with_full_dataset`: runs the loader twice with the full-dataset option, checks that
   `load_times.csv` is recreated and that rows are not duplicated.

## Temporary database and credentials

| Value | CI setting |
| --- | --- |
| Service image | `postgres:16` |
| Database | `taxi_ci` |
| User | `ci_runner` |
| Password | `ci_only_password` (throwaway test credential, not a secret) |
| Host / port | `127.0.0.1` / `5432` |
| Switch | `TEST_POSTGRES: "1"`, set in the `env` of the `postgres-integration` job only |

These credentials exist only on the ephemeral GitHub-hosted runner. The service container is destroyed when the job
ends. They have nothing to do with the team's real database: do not copy `POSTGRES_PASSWORD` or `.env` into the
workflow.

## Safety

- The PostgreSQL tests run `DROP SCHEMA IF EXISTS ... CASCADE` and `CREATE SCHEMA` for the schemas the tests use
  (`m1`, `m2`, `m4`, see `docs/TESTING.md`). This is expected in CI, where the database is disposable.
- The fixtures are synthetic: `tests/synth.py` generates small Parquet files in pytest temporary folders. No TLC
  download, no real taxi data and no historical benchmark results are used.
- Never set `TEST_POSTGRES=1` on a developer machine that points at a shared or real database. Locally, without a
  scratch PostgreSQL, run `python -m pytest tests -q` and expect the 5 skipped tests.

## What this does and does not prove

Integration coverage here means: the database-loading and benchmark correctness logic is exercised automatically on
synthetic data, against a real PostgreSQL 16 server, on every pull request.

It does not prove:

- a fresh end-to-end Docker run (`docker compose`) on real data files;
- anything about the real full-size dataset;
- benchmark speed: timings measured on a shared CI runner with default PostgreSQL settings are not benchmark results,
  and no performance claims should be made from them.

## Troubleshooting

- **The job cannot connect to PostgreSQL:** check that the service is healthy, that the service and job credentials
  match, that `POSTGRES_HOST` is `127.0.0.1` and that port 5432 is exposed. Never point the job at a real database.
- **The job reports 5 skipped tests:** the job did not receive `TEST_POSTGRES: "1"`, the intended test module did not
  run, or the variable is scoped incorrectly. Inspect the job-level `env` and the pytest summary.
- **GitHub says the YAML is invalid:** check indentation and duplicate keys, and that `postgres-integration` sits
  under `jobs:` at the same indentation as `pytest`.
- **A PostgreSQL test fails:** save the test name and the traceback and report it to the owner of the component
  (loader and SQL). Do not disable, mock away or skip the test.
