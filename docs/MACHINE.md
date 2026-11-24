# Machine used for the benchmark results

This document describes the Windows host and Docker environment used for the NYC Mobility Intelligence experiments. **Host specifications and Docker availability were inspected on 21 November 2026**. The original benchmark ran on 15 November 2026; the supplementary benchmark-rigor experiments ran on 20 November 2026. Settings observed later must not automatically be assumed to have been identical during the original run.

## Host computer

| Item | Verified value |
|---|---|
| Manufacturer and model | HP — HP Laptop 15s-eq0xxx |
| CPU | AMD Ryzen 5 3500U with Radeon Vega Mobile Gfx |
| CPU cores / logical processors | 4 physical cores / 8 logical processors |
| Installed physical RAM | 5.9 GB reported by Windows |
| Storage | KIOXIA KBG40ZNV256G SSD |
| Operating system | Microsoft Windows 11 Home, version 10.0.26200 |
| Container runtime | Docker Desktop using WSL2 (`docker-desktop` default WSL distribution) |
| Docker Engine version | 29.9.3 (observed 21 November 2026) |
| CPUs reported to Docker | 8 logical CPUs (observed 21 November 2026) |
| Memory reported to Docker | 3,006,963,712 bytes (approximately 2.80 GiB; observed 21 November 2026) |

**Memory clarification:** The host's 5.9 GB of installed physical RAM is not the same as the approximately 2.80 GiB of memory reported as available to Docker. `docker info` reports memory visible to the Docker daemon; it does not by itself prove the exact Docker Desktop memory-limit setting used for the earlier benchmark runs.

## Experiment dates

| Experiment | Date | Evidence |
|---|---|---|
| Original PostgreSQL vs DuckDB benchmark | 15 November 2026 | `results/run_status.json` (`state: complete`) |
| Supplementary Q1–Q3 query-plan and repeated-timing study | 20 November 2026 | `protocol.json` files in the individual `results/marin_*` experiment folders |

## Versions and configuration recorded by the supplementary study

The following values are taken from the **20 November 2026 supplementary study protocol**, rather than inferred from the current Windows environment:

| Item | Recorded value |
|---|---|
| Container platform | Linux under Microsoft WSL2, x86-64 |
| Python | 3.12.16 |
| PostgreSQL | 16.16 |
| DuckDB | v2.0.0 |
| CPU count visible to the study | 8 |
| DuckDB default threads | 8 |
| DuckDB `memory_limit` | 2.2 GiB |
| PostgreSQL default `max_parallel_workers_per_gather` | 2 |
| PostgreSQL default `shared_buffers` | 128 MB |
| PostgreSQL default `work_mem` | 4 MB |
| PostgreSQL JIT | Enabled |

The study also varied DuckDB's thread setting (1 and 3) and PostgreSQL's `max_parallel_workers_per_gather` (0 and 2). These are *configuration limits*, not guarantees of actual CPU use; the observed PostgreSQL worker counts were determined from execution plans.

## Interpretation and reproducibility notes

- PostgreSQL is a client/server DBMS, while DuckDB executes embedded in the benchmark process. The results describe these particular configured systems and workloads, not a universal performance ranking.
- Caches were not cleared between runs. A first execution in a new connection is **not** equivalent to a cold operating-system cache.
- The timing experiments are sensitive to available CPU, memory, contention and background activity. When reproducing the benchmark on another computer, compare query outputs and data-quality counts for correctness; report elapsed times separately by machine.
- The original benchmark's `results/environment.txt`, where available, is the preferred source for checking the software configuration at the time of the 15 November run. The 20 November protocol and the 21 November system inspection should not silently replace missing historical details.
