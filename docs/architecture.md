# Architecture

```mermaid
flowchart LR
    TLC[(NYC TLC<br/>monthly Parquet)] -->|only if missing| D[download_data.py]
    WIN[your own files in<br/>data/raw] --> RAW
    D --> RAW[/data/raw<br/>ORIGINALS, never modified/]
    RAW --> P[preprocess.py<br/>exclusion rules + night window<br/>trip_id + manifest + validation]
    RAW -. optional .-> F[build_full_dataset.py<br/>all trips, all columns,<br/>+ source_month]
    P --> CLEAN[/data/clean<br/>cleaned NIGHT TRIPS only/]
    CLEAN --> LP[load_postgres.py]
    CLEAN --> LD[load_duckdb.py]
    LP --> PG[(PostgreSQL<br/>schemas m1 m2 m4)]
    LD --> DK[(DuckDB files<br/>1m 2m 4m)]
    PG --> B[benchmark.py<br/>equivalence + consistency gate<br/>first / warm-up / warm runs]
    DK --> B
    B --> R[/results/*.csv, run_status.json/]
    P --> R
    DK --> C[city_results.py] --> R
    R --> A[Streamlit dashboard :8501]
```

Containers: `db` (PostgreSQL 16), `loader` (download, preprocess, load, city tables), `benchmark`
(timings; DuckDB runs inside this container), `app` (dashboard). Order enforced in docker-compose.yml:
loader after `db` is healthy; benchmark after `db` is healthy and the loader has finished successfully;
dashboard after the loader has finished (it shows partial results while the benchmark is still running).

Both engines load the SAME clean files; the benchmark refuses to measure if the databases do not match
the manifest (fingerprint of settings + raw file contents). The dashboard only reads result files.
