"""OPTIONAL: combine the original monthly files into ONE complete dataset.

    BUILD_FULL_DATASET=1 docker compose up      (or)      docker compose run --rm loader python build_full_dataset.py

Output: DATA_DIR/full/yellow_tripdata_<first>_to_<last>_all_trips.parquet
  - EVERY trip of every month, in the original row order, with ALL original columns and the
    original timestamps, plus one added column: source_month ('YYYY-MM').
  - No cleaning, no night filter. The original files are only read.
  - If the months have different columns, the union is used; a column missing in a month is
    NULL there. If a column has different types in different months, the widest common type is
    used (e.g. int -> double); this is listed in results/data_quality/full_dataset_schema.csv.
The databases and the benchmark do NOT use this file: they use the cleaned night-trip subset.

Verification (results/data_quality/full_dataset_check.csv): for every month and every original
column the non-null count, minimum and maximum of the combined file are compared with the
source file, and the row count per source_month must equal the source rows.
"""
from __future__ import annotations

import os
import sys

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from common import get_config, month_list, quality_dir, raw_dir, sha256_file, trip_file, write_csv, write_json


def unified_schema(paths) -> tuple[pa.Schema, dict]:
    schemas = [pq.read_schema(p) for p in paths]
    uni = pa.unify_schemas(schemas, promote_options="permissive")
    return uni, {p.name: s for p, s in zip(paths, schemas)}


def to_unified(table: pa.Table, uni: pa.Schema, month: str) -> pa.Table:
    cols = []
    for field in uni:
        if field.name in table.column_names:
            cols.append(table[field.name].cast(field.type, safe=True))
        else:
            cols.append(pa.nulls(table.num_rows, type=field.type))
    cols.append(pa.array([month] * table.num_rows, type=pa.string()))
    return pa.Table.from_arrays(cols, schema=uni.append(pa.field("source_month", pa.string())))


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and isinstance(b, float) and a != a and b != b:
        return True
    return a == b


def verify(out_path, paths_by_month: dict, schemas: dict, uni: pa.Schema) -> list[list]:
    con = duckdb.connect()
    rows = []
    big = f"read_parquet('{out_path.as_posix()}')"
    for month, p in paths_by_month.items():
        src = f"read_parquet('{p.as_posix()}')"
        n_src = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
        n_out = con.execute(f"SELECT count(*) FROM {big} WHERE source_month = '{month}'").fetchone()[0]
        rows.append([month, "(row count)", "PASS" if n_src == n_out else "FAIL", f"source {n_src}, combined {n_out}"])
        for name in schemas[p.name].names:
            q = f'SELECT count("{name}"), min("{name}"), max("{name}") FROM '
            a = con.execute(q + src).fetchone()
            b = con.execute(q + f"{big} WHERE source_month = '{month}'").fetchone()
            ok = all(_same(x, y) for x, y in zip(a, b))
            rows.append([month, name, "PASS" if ok else "FAIL", f"source {a}, combined {b}"])
    con.close()
    return rows


def main() -> int:
    cfg = get_config()
    months = month_list(cfg.total_months, cfg.start_month)
    raw = raw_dir()
    paths = [raw / trip_file(m) for m in months]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        print("Missing raw files, run download_data.py first:\n  " + "\n  ".join(missing))
        return 1
    out_dir = cfg.data_dir / "full"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"yellow_tripdata_{months[0]}_to_{months[-1]}_all_trips.parquet"
    src_info = {p.name: {"bytes": p.stat().st_size, "sha256": sha256_file(p)} for p in paths}
    info_path = out_dir / "full_dataset.json"

    uni, schemas = unified_schema(paths)
    tmp = out.with_suffix(".parquet.part")
    total = 0
    with pq.ParquetWriter(tmp, uni.append(pa.field("source_month", pa.string())), compression="snappy") as w:
        for m, p in zip(months, paths):
            n = 0
            for batch in pq.ParquetFile(p).iter_batches(batch_size=500_000):
                w.write_table(to_unified(pa.Table.from_batches([batch]), uni, m))
                n += batch.num_rows
            total += n
            print(f"{m}: {n:,} rows written")
    os.replace(tmp, out)

    schema_rows = []
    for field in uni:
        types = {name: str(s.field(field.name).type) if field.name in s.names else "(missing)"
                 for name, s in schemas.items()}
        changed = len({t for t in types.values() if t != "(missing)"}) > 1 or "(missing)" in types.values()
        schema_rows.append([field.name, str(field.type), "yes" if changed else "no",
                            "; ".join(f"{k}={v}" for k, v in types.items())])
    schema_rows.append(["source_month", "string", "added", "added by build_full_dataset.py"])
    write_csv(quality_dir() / "full_dataset_schema.csv",
              ["column", "type_in_combined_file", "differs_between_months", "type_per_source_file"], schema_rows)

    result = verify(out, dict(zip(months, paths)), schemas, uni)
    write_csv(quality_dir() / "full_dataset_check.csv", ["source_month", "column", "status", "detail"], result)
    source_total = sum(pq.read_metadata(p).num_rows for p in paths)
    combined_total = pq.read_metadata(out).num_rows
    ok = all(r[2] == "PASS" for r in result) and source_total == combined_total == total
    write_json(info_path, {"output": out.name, "rows": combined_total, "source_rows": source_total,
                           "sources": src_info, "verified": ok})
    print(f"\ncombined file: {out} ({combined_total:,} rows; source rows {source_total:,})")
    print("verification:", "PASS" if ok else "FAIL", f"({sum(r[2] == 'FAIL' for r in result)} failed checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
