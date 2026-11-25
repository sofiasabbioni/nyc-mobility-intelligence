"""Compare only Parquet READ strategies, NOT PostgreSQL end-to-end loading.
Usage: python scripts/profile_parquet_reads.py data/raw/yellow_tripdata_2026-04.parquet
"""
import argparse
import statistics
import time
from pathlib import Path

import pyarrow.parquet as pq

parser = argparse.ArgumentParser()
parser.add_argument("path", type=Path)
parser.add_argument("--repeats", type=int, default=3)
args = parser.parse_args()
if not args.path.is_file():
    parser.error(f"Missing file: {args.path}")
if args.repeats < 1:
    parser.error("--repeats must be >= 1")

columns = ["tpep_pickup_datetime", "PULocationID"]
results = {}
for method in ("full_read", "iter_batches"):
    times, counts = [], []
    for run in range(args.repeats):
        start = time.perf_counter()
        if method == "full_read":
            table = pq.read_table(args.path, columns=columns)
            rows = table.num_rows
            del table
        else:
            rows = 0
            pf = pq.ParquetFile(args.path)
            for batch in pf.iter_batches(batch_size=100_000, columns=columns):
                rows += batch.num_rows
        times.append(time.perf_counter() - start)
        counts.append(rows)
    if len(set(counts)) != 1:
        raise RuntimeError(f"Inconsistent counts for {method}: {counts}")
    results[method] = counts[0]
    print(f"{method}: rows={counts[0]}, timings={[round(x, 3) for x in times]}, median_s={statistics.median(times):.3f}")

if results["full_read"] != results["iter_batches"]:
    raise RuntimeError(f"Different row totals: {results}")
print("PASS: identical row counts. NOTE: this is a Parquet read microbenchmark only.")
