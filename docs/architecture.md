# Architecture
```mermaid
flowchart LR
    TLC[(NYC TLC monthly Parquet)] -->|only if missing| D[download_data.py]
    WIN[Your own files in data/raw] --> RAW
    D --> RAW[/data/raw - original files/]

    RAW --> P[preprocess.py - exclusion rules, night window, validation]
    RAW -. optional .-> F[build_full_dataset.py - all trips]

    P --> CLEAN[/data/clean - cleaned night trips/]

    CLEAN --> LP[load_postgres.py]
    CLEAN --> LD[load_duckdb.py]

    LP --> PG[(PostgreSQL - m1, m2, m4)]
    LD --> DK[(DuckDB - 1m, 2m, 4m)]

    PG --> B[benchmark.py - equivalence and timings]
    DK --> B

    B --> R[/Benchmark results and run_status.json/]
    P --> R
    DK --> C[city_results.py]
    C --> R

    RAW --> NM[nightlife_metrics.py - event-level pickups and dropoffs]
    P -. exporter invoked after preprocessing .-> NM
    NM --> NR[/results/nightlife - zone_metrics.csv, hourly.csv, validation.json/]

    RAW --> Z[/taxi_zones.zip - geographic boundaries/]

    R --> A[Streamlit dashboard]
    NR --> A
    Z --> A

    A --> T1[Mobility charts]
    A --> T2[Data quality]
    A --> T3[PostgreSQL vs DuckDB]
    A --> MAP[NYC nightlife visualization]

    MAP --> M2[2D geographic map]
    MAP --> M3[3D extruded taxi zones]
```

## Nightlife analysis and visualization

The nightlife metrics exporter is integrated into `loader/run_loader.py` and executes after preprocessing.

Unlike the database benchmark, which uses cleaned nighttime trips, the nightlife exporter works with original trip records to classify pickup and drop-off events independently according to their timestamps.

It produces three outputs under `results/nightlife/`:

- `zone_metrics.csv`: zone-level nightlife activity metrics.
- `hourly.csv`: hourly pickup and drop-off patterns.
- `validation.json`: nightlife validation information.

The Streamlit dashboard combines these outputs with NYC taxi-zone geographic boundaries to provide interactive 2D and 3D nightlife maps.

In the 3D view, taxi zones are extruded according to the selected nightlife metric. Their heights represent taxi activity, not real building heights.

Both maps use the existing nightlife filters. The dashboard reads generated results without modifying the underlying datasets or benchmark measurements.

## Containers and execution order

Containers: `db` (PostgreSQL 16), `loader` (download, preprocess, load, city tables), `benchmark`
(timings; DuckDB runs inside this container), `app` (dashboard). Order enforced in docker-compose.yml:
loader after `db` is healthy; benchmark after `db` is healthy and the loader has finished successfully;
dashboard after the loader has finished (it shows partial results while the benchmark is still running).

Both engines load the SAME clean files; the benchmark refuses to measure if the databases do not match
the manifest (fingerprint of settings + raw file contents). The dashboard only reads result files.
