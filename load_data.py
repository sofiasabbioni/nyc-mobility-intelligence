"""Step 2 - Store it: load the raw TLC files into PostgreSQL, as they are (no cleaning!).

First calls download_data.py, so the files are always there.

The months are loaded one at a time. Each month is read from the Parquet file in
chunks (iter_batches) and sent to PostgreSQL with COPY, so memory stays low and
loading is fast. A month that is already in the database is skipped, so restarting
does not reload everything.
"""
import io
import os
import time

import pandas as pd
import psycopg
import pyarrow.parquet as pq

import download_data

DATA_DIR = download_data.DATA_DIR
MONTHS = download_data.MONTHS
CHUNK = 100_000   # rows read from the file and sent to the database at a time

# The 20 columns of the TLC yellow-taxi files, in lowercase to avoid conflict with PostgreSQL.
# PostgreSQL turns names into lowercase anyway (PULocationID -> pulocationid),
TRIP_COLUMNS = [
    "vendorid", "tpep_pickup_datetime", "tpep_dropoff_datetime",
    "passenger_count", "trip_distance", "ratecodeid", "store_and_fwd_flag",
    "pulocationid", "dolocationid", "payment_type", "fare_amount", "extra",
    "mta_tax", "tip_amount", "tolls_amount", "improvement_surcharge",
    "total_amount", "congestion_surcharge", "airport_fee", "cbd_congestion_fee",
]

# Columns that TLC stores as decimals (1.0) but our table has as INTEGER.
# COPY rejects "1.0" for an INTEGER column, so we convert them first.
INT_COLUMNS = [
    "vendorid", "passenger_count", "ratecodeid",
    "pulocationid", "dolocationid", "payment_type",
]


def main():
    download_data.main()

    conn = psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "db"),   # the service name in docker-compose.yml
        port=5432,
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )
    cursor = conn.cursor()
    print("\nConnected to PostgreSQL")
    total_start = time.perf_counter()

    # 1. Create the tables (only if they do not exist yet)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS taxi_trips (
            vendorid INTEGER,
            tpep_pickup_datetime TIMESTAMP,     -- New York local time, as in the file
            tpep_dropoff_datetime TIMESTAMP,
            passenger_count INTEGER,
            trip_distance DOUBLE PRECISION,
            ratecodeid INTEGER,
            store_and_fwd_flag TEXT,
            pulocationid INTEGER,
            dolocationid INTEGER,
            payment_type INTEGER,
            fare_amount DOUBLE PRECISION,
            extra DOUBLE PRECISION,
            mta_tax DOUBLE PRECISION,
            tip_amount DOUBLE PRECISION,
            tolls_amount DOUBLE PRECISION,
            improvement_surcharge DOUBLE PRECISION,
            total_amount DOUBLE PRECISION,
            congestion_surcharge DOUBLE PRECISION,
            airport_fee DOUBLE PRECISION,
            cbd_congestion_fee DOUBLE PRECISION
        )
    ''')

    # loaded_months keeps one row per month we finished loading
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS loaded_months (
            month TEXT PRIMARY KEY,
            rows_loaded INTEGER NOT NULL,
            seconds DOUBLE PRECISION NOT NULL
        )
    ''')

    # 2. Which months are already in the database? (prevents duplicates)
    cursor.execute("SELECT month FROM loaded_months")
    already = []
    for row in cursor.fetchall():
        already.append(row[0])

    # If the database has a month we did NOT ask for (for example, we now want
    # only 2026-04 to measure 1 month), we empty the trips and start clean.
    start_clean = False
    for month in already:
        if month not in MONTHS:
            start_clean = True

    if start_clean:
        print("The database has other months than TAXI_MONTHS: starting clean.")
        cursor.execute("DELETE FROM taxi_trips")
        cursor.execute("DELETE FROM loaded_months")
        already = []
    conn.commit()

    # 3. Taxi zones (265 rows): small, so we simply reload them every time
    cursor.execute("DROP TABLE IF EXISTS taxi_zones")
    cursor.execute('''
        CREATE TABLE taxi_zones (
            locationid INTEGER PRIMARY KEY,
            borough TEXT,
            zone TEXT,
            service_zone TEXT
        )
    ''')

    zones = pd.read_csv(os.path.join(DATA_DIR, "taxi_zone_lookup.csv"))
    # Empty cells in pandas are NaN; the database needs None (NULL) instead.
    # astype(object) lets a column hold None, where() puts None in every empty cell.
    zones = zones.astype(object).where(zones.notna(), None)

    cursor.executemany(
        "INSERT INTO taxi_zones (locationid, borough, zone, service_zone) VALUES (%s, %s, %s, %s)",
        zones.values.tolist(),
    )
    conn.commit()
    print(f"{len(zones)} taxi zones inserted.")

    # 4. Trips: one month at a time, read AND written in chunks.
    # iter_batches reads only CHUNK rows from the Parquet file at a time, so memory stays low.
    # COPY sends each chunk as one CSV stream, much faster than one INSERT per row.
    copy_sql = f"COPY taxi_trips ({', '.join(TRIP_COLUMNS)}) FROM STDIN WITH (FORMAT csv)"

    for month in MONTHS:
        if month in already:
            print(f"{month}: already loaded, skipped")
            continue

        parquet_file = pq.ParquetFile(os.path.join(DATA_DIR, download_data.trips_file(month)))
        total = parquet_file.metadata.num_rows      # row count, without reading the data
        print(f"\n{month}: {total:,} rows")

        t = time.perf_counter()
        done = 0
        with cursor.copy(copy_sql) as copy:
            for batch in parquet_file.iter_batches(batch_size=CHUNK):
                chunk = batch.to_pandas()

                # Lowercase names, then the same column order as the table.
                # If the TLC ever renames a column, this stops with a KeyError naming it.
                chunk.columns = [c.lower() for c in chunk.columns]
                chunk = chunk[TRIP_COLUMNS]

                # 1.0 -> 1 (Int64 with a capital I keeps empty cells empty)
                chunk[INT_COLUMNS] = chunk[INT_COLUMNS].astype("Int64")

                # Write the chunk as CSV text and send it. Empty cell = NULL for COPY.
                buf = io.StringIO()
                chunk.to_csv(buf, header=False, index=False)
                copy.write(buf.getvalue())

                done += len(chunk)
                print(f"  {done:,} / {total:,} rows copied", flush=True)
        seconds = round(time.perf_counter() - t, 2)

        cursor.execute(
            "INSERT INTO loaded_months (month, rows_loaded, seconds) VALUES (%s, %s, %s)",
            (month, total, seconds),
        )
        # Commit after each month: if the script crashes during a month,
        # only that month is thrown away and the finished ones are kept.
        conn.commit()
        print(f"{month}: loaded in {seconds} s")

    # 5. Measurements: time and disk
    cursor.execute("SELECT COUNT(*) FROM taxi_trips")
    rows_in_db = cursor.fetchone()[0]

    cursor.execute("SELECT pg_database_size(current_database())")
    db_mb = round(cursor.fetchone()[0] / 1024**2, 1)

    files_bytes = 0
    for month in MONTHS:
        files_bytes += os.path.getsize(os.path.join(DATA_DIR, download_data.trips_file(month)))
    files_mb = round(files_bytes / 1024**2, 1)

    print("\nDone.")
    print(f"  months in database: {', '.join(MONTHS)}")
    print(f"  trips in database:  {rows_in_db:,}")
    print(f"  time of this run:   {round(time.perf_counter() - total_start, 2)} s")
    print(f"  Parquet files:      {files_mb} MB")
    print(f"  database on disk:   {db_mb} MB")

    conn.close()


if __name__ == "__main__":
    main()