"""Record the memory of the containers while the project runs.

Run it on YOUR computer (not in a container), in a second terminal, BEFORE you start
`docker compose up`:

    python scripts/sample_docker_stats.py

Stop it with Ctrl+C when the benchmark has finished. It writes results/memory.csv:
    ts (epoch seconds), service, container, mem_mb, cpu_percent

Then run  python scripts/summarize_memory.py  to get the peak memory per test.
Needs only Python and the `docker` command. Note: Docker reports memory of the
container, so it includes PostgreSQL's own cache but not the operating system's.
"""
import csv
import json
import re
import subprocess
import time
from pathlib import Path

UNITS = {"b": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3, "kb": 1000, "mb": 1000**2, "gb": 1000**3}
OUT = Path(__file__).resolve().parent.parent / "results" / "memory.csv"


def to_mb(text: str) -> float:
    m = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)", text)
    if not m:
        return 0.0
    return float(m.group(1)) * UNITS.get(m.group(2).lower(), 1) / 1024**2


def service_name(container: str) -> str:
    """'taxi_project-db-1' -> 'db'"""
    m = re.match(r"^.*-(db|loader|benchmark|app)-\d+$", container)
    return m.group(1) if m else container


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    new = not OUT.exists()
    print(f"Sampling docker stats into {OUT}. Press Ctrl+C to stop.")
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "service", "container", "mem_mb", "cpu_percent"])
        try:
            while True:
                res = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                    capture_output=True, text=True,
                )
                now = time.time()
                for line in res.stdout.splitlines():
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    name = d.get("Name", "")
                    mem = to_mb(d.get("MemUsage", "0B").split("/")[0])
                    cpu = d.get("CPUPerc", "0%").strip("%")
                    w.writerow([f"{now:.2f}", service_name(name), name, f"{mem:.1f}", cpu])
                f.flush()
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
