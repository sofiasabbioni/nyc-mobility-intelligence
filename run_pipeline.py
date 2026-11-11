"""Run the whole data pipeline in order and time every step.

This is what the `loader` container runs. Steps:
    1. load_data.py           download the TLC files and load them, raw, into PostgreSQL (taxi_trips)
    2. preprocessing_data_postgress.py  copy the 6 columns / hour window into taxi_trips_night
    3. cleaning_data_postgress.py    clean it in Python -> taxi_trips_night_clean (rejects logged per reason)

If a step fails, the pipeline stops there and the container exits with an error.
The timing summary at the end is the "full run" measurement for the README.

Usage (inside the container, or on your laptop with the POSTGRES_* variables set):
    python run_pipeline.py
"""
import subprocess
import sys
import time

STEPS = [
    ("Download + load raw data", "load_data.py"),
    ("Create filtered night table", "preprocessing_data_postgress.py"),
    ("Clean the night data", "cleaning_data_postgress.py"),
]


def main():
    timings = []
    total_start = time.perf_counter()

    for number, (label, script) in enumerate(STEPS, start=1):
        print(f"\n{'=' * 70}\nSTEP {number}/{len(STEPS)}: {label} ({script})\n{'=' * 70}", flush=True)
        t = time.perf_counter()
        result = subprocess.run([sys.executable, script])
        seconds = round(time.perf_counter() - t, 1)
        timings.append((label, seconds))

        if result.returncode != 0:
            print(f"\nStep '{label}' FAILED (exit code {result.returncode}). Pipeline stopped.", flush=True)
            sys.exit(result.returncode)

    print(f"\n{'=' * 70}\nPIPELINE DONE\n{'=' * 70}")
    for label, seconds in timings:
        print(f"  {label:<32} {seconds:>9.1f} s")
    print(f"  {'Full run':<32} {round(time.perf_counter() - total_start, 1):>9.1f} s")


if __name__ == "__main__":
    main()