"""Step 3 - Filter it: create the table `taxi_trips_night` inside PostgreSQL.

Copies only the 6 columns we need, and only the pickups inside the hour window,
from the raw table `taxi_trips` (filled by load_data.py). Everything runs inside the
database (INSERT ... SELECT), so no rows travel through Python and memory stays flat.

The raw table is NOT changed: it keeps all hours, so night vs. day comparisons
(night share, etc.) are still possible later.

Safe to re-run: the table is dropped and rebuilt each time, so the result always
matches the current filter and rows are never duplicated.

Environment variables (same as the other scripts):
    POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD      (required)
    POSTGRES_HOST (default: localhost), POSTGRES_PORT (default: 5432)
    NIGHT_START_HOUR (default: 20)   -> pickups from 20:00 ...
    NIGHT_END_HOUR   (default: 6)    -> ... until 05:59   (test filter, final criteria TBD)

Usage:
    python create_night_table.py
"""
import os
import time

import psycopg

START_HOUR = int(os.environ.get("NIGHT_START_HOUR", 20))
END_HOUR = int(os.environ.get("NIGHT_END_HOUR", 6))

SOURCE_TABLE = "taxi_trips"
TARGET_TABLE = "taxi_trips_night"


def hour_condition():
    """SQL condition on the pickup hour. A window like 20 -> 6 wraps around midnight."""
    hour = "EXTRACT(HOUR FROM tpep_pickup_datetime)"
    if START_HOUR > END_HOUR:
        return f"({hour} >= %(start)s OR {hour} < %(end)s)"
    return f"({hour} >= %(start)s AND {hour} < %(end)s)"


def main():
    conn = psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )
    cursor = conn.cursor()
    params = {"start": START_HOUR, "end": END_HOUR}
    print(f"Pickups from {START_HOUR:02d}:00 to {END_HOUR:02d}:00 -> table {TARGET_TABLE}")

    # How many rows should end up in the new table?
    cursor.execute(f"SELECT count(*) FROM {SOURCE_TABLE} WHERE {hour_condition()}", params)
    expected = cursor.fetchone()[0]
    print(f"Matching rows in {SOURCE_TABLE}: {expected:,}")

    t = time.perf_counter()

    # Drop, create and fill in ONE transaction: if anything fails, the old table is kept.
    cursor.execute(f"DROP TABLE IF EXISTS {TARGET_TABLE}")
    cursor.execute(f'''
        CREATE TABLE {TARGET_TABLE} (
            tpep_pickup_datetime  TIMESTAMP,     -- New York local time, as in the file
            tpep_dropoff_datetime TIMESTAMP,
            pulocationid          INTEGER,
            dolocationid          INTEGER,
            trip_distance         DOUBLE PRECISION,
            fare_amount           DOUBLE PRECISION
        )
    ''')
    cursor.execute(f'''
        INSERT INTO {TARGET_TABLE}
            (tpep_pickup_datetime, tpep_dropoff_datetime,
             pulocationid, dolocationid, trip_distance, fare_amount)
        SELECT tpep_pickup_datetime, tpep_dropoff_datetime,
               pulocationid, dolocationid, trip_distance, fare_amount
        FROM {SOURCE_TABLE}
        WHERE {hour_condition()}
    ''', params)
    conn.commit()
    seconds = round(time.perf_counter() - t, 2)

    # Verify and measure
    cursor.execute(f"SELECT count(*) FROM {TARGET_TABLE}")
    created = cursor.fetchone()[0]

    cursor.execute("SELECT pg_total_relation_size(%s)", (TARGET_TABLE,))
    target_mb = round(cursor.fetchone()[0] / 1024**2, 1)
    cursor.execute("SELECT pg_total_relation_size(%s)", (SOURCE_TABLE,))
    source_mb = round(cursor.fetchone()[0] / 1024**2, 1)

    print("\nDone.")
    print(f"  rows in {TARGET_TABLE}: {created:,}  ->  {'match' if created == expected else 'MISMATCH'}")
    print(f"  time of this step:     {seconds} s")
    print(f"  size of {SOURCE_TABLE}:    {source_mb} MB")
    print(f"  size of {TARGET_TABLE}: {target_mb} MB")

    conn.close()


if __name__ == "__main__":
    main()