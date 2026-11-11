"""Step 4 - Clean it: read taxi_trips_night from PostgreSQL, clean it in Python, and store the
accepted rows in a new table `taxi_trips_night_clean`.

The source table is never changed, so the rejected rows can always be counted again.
Every run is logged in the table `night_cleaning_runs`: source, accepted and rejected rows,
and rejected rows per reason. That log is the data-quality count the project needs.

Cleaning rules. A row is rejected for the FIRST rule it breaks, so the reasons add up:
    1. missing_value          pickup time, dropoff time, pickup zone or dropoff zone is empty
    2. invalid_zone           a zone ID that is not in the taxi_zones table
    3. outside_window         pickup is not inside the observation months (TAXI_MONTHS)
    4. dropoff_before_pickup  dropoff time earlier than pickup time

Deliberately NOT filtered (the project plan says to leave them unless a metric needs it):
    zero distances, zero or negative fares, zone IDs 264 / 265 (Unknown / Outside NYC),
    duplicates (the files have no trip ID, so there is no defensible duplicate key).

Environment variables (same as the other scripts):
    POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD      (required)
    POSTGRES_HOST (default: localhost), POSTGRES_PORT (default: 5432)
    TAXI_MONTHS   (default: 2026-04,2026-05,2026-06,2026-07)  -> the observation window
    SOURCE_TABLE  (default: taxi_trips_night)

The observation window runs from the first day of the earliest month up to (not including)
the first day after the latest month.

Running it again drops and rebuilds taxi_trips_night_clean, so it is safe to repeat.

Usage:
    python clean_night_data.py
"""
import io
import os
import time
from datetime import datetime

import pandas as pd
import psycopg
from psycopg import sql

SOURCE_TABLE = os.environ.get("SOURCE_TABLE", "taxi_trips_night")
TARGET_TABLE = "taxi_trips_night_clean"
LOG_TABLE = "night_cleaning_runs"
MONTHS = sorted(m.strip() for m in os.environ.get("TAXI_MONTHS", "2026-04,2026-05,2026-06,2026-07").split(","))
CHUNK = 200_000   # rows read and cleaned at a time, so memory stays low

COLUMNS = [
    "tpep_pickup_datetime",
    "tpep_dropoff_datetime",
    "pulocationid",
    "dolocationid",
    "trip_distance",
    "fare_amount",
]

REASONS = ["missing_value", "invalid_zone", "outside_window", "dropoff_before_pickup"]


def observation_window(months):
    """First day of the first month, and the first day after the last month."""
    start = datetime.strptime(months[0] + "-01", "%Y-%m-%d")
    year, month = (int(x) for x in months[-1].split("-"))
    year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return start, datetime(year, month, 1)


def apply_rules(df, valid_zones, start, end):
    """Return (clean rows, number of rejected rows per reason)."""
    df = df.copy()
    df["tpep_pickup_datetime"] = pd.to_datetime(df["tpep_pickup_datetime"], errors="coerce")
    df["tpep_dropoff_datetime"] = pd.to_datetime(df["tpep_dropoff_datetime"], errors="coerce")

    reason = pd.Series("", index=df.index, dtype=object)

    def mark(mask, name):
        # only rows that have not already been rejected by an earlier rule
        reason[mask & (reason == "")] = name

    mark(df[["tpep_pickup_datetime", "tpep_dropoff_datetime",
             "pulocationid", "dolocationid"]].isna().any(axis=1), "missing_value")
    mark(~df["pulocationid"].isin(valid_zones) | ~df["dolocationid"].isin(valid_zones),
         "invalid_zone")
    mark((df["tpep_pickup_datetime"] < start) | (df["tpep_pickup_datetime"] >= end),
         "outside_window")
    mark(df["tpep_dropoff_datetime"] < df["tpep_pickup_datetime"], "dropoff_before_pickup")

    rejected = {name: int((reason == name).sum()) for name in REASONS}
    clean = df[reason == ""].copy()
    clean["pulocationid"] = clean["pulocationid"].astype("int64")   # "138", not "138.0"
    clean["dolocationid"] = clean["dolocationid"].astype("int64")
    return clean, rejected


def connect():
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def main():
    t_start = time.perf_counter()
    start, end = observation_window(MONTHS)
    source = sql.Identifier(SOURCE_TABLE)
    target = sql.Identifier(TARGET_TABLE)

    # Two connections: one streams the rows out, the other writes the clean rows in.
    read_conn = connect()
    write_conn = connect()
    print("Connected to PostgreSQL")
    print(f"Observation window: pickups from {start:%Y-%m-%d} up to, not including, {end:%Y-%m-%d}")

    with write_conn.cursor() as cur:
        cur.execute("SELECT locationid FROM taxi_zones")
        valid_zones = {row[0] for row in cur.fetchall()}
        if not valid_zones:
            raise SystemExit("taxi_zones is empty: run load_data.py first.")
        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(source))
        source_total = cur.fetchone()[0]
    write_conn.commit()
    print(f"Rows in {SOURCE_TABLE}: {source_total:,}")

    source_rows = 0
    accepted_rows = 0
    rejected = {name: 0 for name in REASONS}

    # Drop, create and fill the clean table in ONE transaction: if anything fails,
    # nothing is changed and the old clean table (if there is one) stays as it was.
    with write_conn.cursor() as wcur:
        wcur.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(target))
        wcur.execute(sql.SQL("""
            CREATE TABLE {} (
                tpep_pickup_datetime  TIMESTAMP NOT NULL,
                tpep_dropoff_datetime TIMESTAMP NOT NULL,
                pulocationid          INTEGER   NOT NULL,
                dolocationid          INTEGER   NOT NULL,
                trip_distance         DOUBLE PRECISION,
                fare_amount           DOUBLE PRECISION
            )
        """).format(target))

        copy_sql = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv)").format(
            target, sql.SQL(", ").join(sql.Identifier(c) for c in COLUMNS))
        select_sql = sql.SQL("SELECT {} FROM {}").format(
            sql.SQL(", ").join(sql.Identifier(c) for c in COLUMNS), source)

        with wcur.copy(copy_sql) as copy:
            # a named cursor is a server-side cursor: rows are streamed, not loaded at once
            with read_conn.cursor(name="source_cursor") as rcur:
                rcur.itersize = CHUNK
                rcur.execute(select_sql)
                while True:
                    rows = rcur.fetchmany(CHUNK)
                    if not rows:
                        break
                    chunk = pd.DataFrame(rows, columns=COLUMNS)
                    clean, rej = apply_rules(chunk, valid_zones, start, end)

                    buf = io.StringIO()
                    clean.to_csv(buf, header=False, index=False, date_format="%Y-%m-%d %H:%M:%S")
                    copy.write(buf.getvalue())

                    source_rows += len(chunk)
                    accepted_rows += len(clean)
                    for name in REASONS:
                        rejected[name] += rej[name]
                    print(f"  {source_rows:,} / {source_total:,} rows read, {accepted_rows:,} accepted",
                          flush=True)

        rejected_rows = sum(rejected.values())
        seconds = round(time.perf_counter() - t_start, 2)

        # The log table: one row per run
        wcur.execute(sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                run_id SERIAL PRIMARY KEY,
                run_at TIMESTAMP DEFAULT now(),
                source_table TEXT,
                months TEXT,
                source_rows BIGINT,
                accepted_rows BIGINT,
                rejected_rows BIGINT,
                rejected_missing_value BIGINT,
                rejected_invalid_zone BIGINT,
                rejected_outside_window BIGINT,
                rejected_dropoff_before_pickup BIGINT,
                duration_seconds DOUBLE PRECISION
            )
        """).format(sql.Identifier(LOG_TABLE)))
        wcur.execute(sql.SQL("""
            INSERT INTO {} (source_table, months, source_rows, accepted_rows, rejected_rows,
                            rejected_missing_value, rejected_invalid_zone,
                            rejected_outside_window, rejected_dropoff_before_pickup,
                            duration_seconds)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """).format(sql.Identifier(LOG_TABLE)),
            (SOURCE_TABLE, ",".join(MONTHS), source_rows, accepted_rows, rejected_rows,
             rejected["missing_value"], rejected["invalid_zone"],
             rejected["outside_window"], rejected["dropoff_before_pickup"], seconds))

        # Reconciliation checks BEFORE committing: on any problem, nothing is saved.
        wcur.execute(sql.SQL("SELECT count(*) FROM {}").format(target))
        in_target = wcur.fetchone()[0]
        problems = []
        if source_rows != source_total:
            problems.append(f"read {source_rows:,} rows but {SOURCE_TABLE} has {source_total:,}")
        if accepted_rows + rejected_rows != source_rows:
            problems.append("accepted + rejected does not equal the rows read")
        if in_target != accepted_rows:
            problems.append(f"{TARGET_TABLE} has {in_target:,} rows, expected {accepted_rows:,}")
        if problems:
            write_conn.rollback()
            raise SystemExit("Reconciliation failed, nothing was saved: " + "; ".join(problems))

        write_conn.commit()

        wcur.execute("SELECT pg_total_relation_size(%s)", (TARGET_TABLE,))
        target_mb = round(wcur.fetchone()[0] / 1024**2, 1)

    read_conn.close()
    write_conn.close()

    print("\nDone.")
    print(f"  rows read from {SOURCE_TABLE}:  {source_rows:,}")
    print(f"  accepted rows:  {accepted_rows:,}  (now in {TARGET_TABLE})")
    print(f"  rejected rows:  {rejected_rows:,}")
    for name in REASONS:
        print(f"    {name:<24}{rejected[name]:>12,}")
    print("  check:          accepted + rejected = source  ->  OK")
    print(f"  size of {TARGET_TABLE}: {target_mb} MB")
    print(f"  time of this run: {seconds} s")


if __name__ == "__main__":
    main()