"""Peak memory per engine and test size, from results/memory.csv and results/phases.csv.

Run after the benchmark has finished (and after scripts/sample_docker_stats.py was stopped):
    python scripts/summarize_memory.py

PostgreSQL works inside the `db` container, DuckDB inside the `benchmark` container, so each
engine is looked up in its own container during its own time window (phases.csv).
Writes results/memory_summary.csv (engine, months, container, peak_mem_mb, samples).
Memory = whole container as reported by `docker stats` (it includes the Python process of the
benchmark for DuckDB, and PostgreSQL's shared buffers for the database). Sampling every ~0.5 s
can miss very short peaks. If a window has no samples the cell is left empty (never invented).
"""
import csv
from pathlib import Path

RESULTS = Path(__file__).resolve().parent.parent / "results"
CONTAINER = {"postgres": "db", "duckdb": "benchmark"}


def read(name: str) -> list[dict]:
    path = RESULTS / name
    if not path.exists():
        raise SystemExit(f"Missing {path}. Did you run the sampler and the benchmark?")
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def peaks(samples: list[dict], phases: list[dict]) -> list[list]:
    out = []
    for p in phases:
        service = CONTAINER[p["engine"]]
        start, end = float(p["start"]), float(p["end"])
        values = [float(s["mem_mb"]) for s in samples
                  if s["service"] == service and start <= float(s["ts"]) <= end]
        out.append([p["engine"], p["months"], service, f"{max(values):.0f}" if values else "", len(values)])
    return out


def main() -> None:
    rows = peaks(read("memory.csv"), read("phases.csv"))
    for engine, months, service, peak, n in rows:
        print(f"{engine:9s} {months}m  container {service:9s} peak {peak or 'no samples'} MB ({n} samples)")
    with open(RESULTS / "memory_summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["engine", "months", "container", "peak_mem_mb", "samples"])
        w.writerows(rows)
    print(f"\nSaved {RESULTS / 'memory_summary.csv'}")


if __name__ == "__main__":
    main()
