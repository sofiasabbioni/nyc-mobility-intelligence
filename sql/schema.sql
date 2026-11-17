-- Schema shared by PostgreSQL and DuckDB (same column names, same order).
-- trip_id comes from preprocess.py. The only index is the primary key of each table.
-- The extra index of the experiment (pickup_ts, PostgreSQL only) is created by the benchmark.

CREATE TABLE zones (
    location_id  INTEGER PRIMARY KEY,
    borough      TEXT,
    zone         TEXT,
    service_zone TEXT
);

CREATE TABLE trips (
    trip_id         BIGINT PRIMARY KEY,
    pickup_ts       TIMESTAMP,
    dropoff_ts      TIMESTAMP,
    pu_location_id  INTEGER,
    do_location_id  INTEGER,
    passenger_count INTEGER,
    trip_distance   DOUBLE PRECISION,
    fare_amount     DOUBLE PRECISION,
    tip_amount      DOUBLE PRECISION,
    total_amount    DOUBLE PRECISION
);

-- Which clean dataset is inside this database (checked by the benchmark before it measures).
CREATE TABLE load_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
