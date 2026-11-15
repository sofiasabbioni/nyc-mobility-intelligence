import os
import time
from datetime import datetime, timedelta

import psycopg
from psycopg import sql

SOURCE_TABLE = "taxi_trips_night_clean"
HOUR_TABLE = "night_zone_hour"
METRICS_TABLE = "night_zone_metrics"

MONTHS = sorted(m.strip() for m in os.environ.get("TAXI_MONTHS", "2026-04,2026-05,2026-06,2026-07").split(","))
NIGHT_START_HOUR = int(os.environ.get("NIGHT_START_HOUR", 20))
NIGHT_END_HOUR = int(os.environ.get("NIGHT_END_HOUR", 6))
LATE_END_HOUR = 5          # late window: 00:00 to 04:59
MIN_TRIPS = int(os.environ.get("MIN_TRIPS", 100))

AIRPORT_ZONES = [1, 132, 138]     
UNKNOWN_ZONES = [264, 265]       

CHECK_ZONES = ["East Village", "Lower East Side", "Williamsburg%", "Upper East Side%", "Astoria"]


def observation_window(months):
    """First day of the first month, and the first day after the last month."""
    start = datetime.strptime(months[0] + "-01", "%Y-%m-%d")
    year, month = (int(x) for x in months[-1].split("-"))
    year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return start.date(), datetime(year, month, 1).date()


def connect():
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def main():
    if not (LATE_END_HOUR <= NIGHT_END_HOUR <= 12 < NIGHT_START_HOUR <= 23):
        raise SystemExit(f"Unexpected night hours: {NIGHT_START_HOUR}:00 to {NIGHT_END_HOUR}:00")

    t_start = time.perf_counter()
    window_start, window_end = observation_window(MONTHS)
    first_night = window_start                    
    last_night = window_end - timedelta(days=2)    

    start_h = sql.Literal(NIGHT_START_HOUR)
    end_h = sql.Literal(NIGHT_END_HOUR)
    late_h = sql.Literal(LATE_END_HOUR)
    first = sql.Literal(first_night)
    last = sql.Literal(last_night)
    source = sql.Identifier(SOURCE_TABLE)
    hour_table = sql.Identifier(HOUR_TABLE)
    metrics_table = sql.Identifier(METRICS_TABLE)
    
    def night_of(column):
        return sql.SQL("({} - make_interval(hours => {}))::date").format(sql.Identifier(column), end_h)

    def night_type(date_expr):
        return sql.SQL("CASE WHEN EXTRACT(isodow FROM {}) IN (5, 6) THEN 'weekend' ELSE 'weekday' END"
                       ).format(date_expr)

    conn = connect()
    print("Connected to PostgreSQL")
    print(f"Nights analysed: {first_night} to {last_night} (named after their evening)")
    print(f"Night hours: {NIGHT_START_HOUR}:00 to {NIGHT_END_HOUR - 1}:59, "
          f"late window 00:00 to {LATE_END_HOUR - 1}:59, minimum trips {MIN_TRIPS}")

    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(source))
        source_total = cur.fetchone()[0]
        if source_total == 0:
            raise SystemExit(f"{SOURCE_TABLE} is empty: run cleaning_data_postgress.py first.")
        print(f"Rows in {SOURCE_TABLE}: {source_total:,}")

        cur.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(hour_table))
        cur.execute(sql.SQL("""
            CREATE TABLE {hour_table} AS
            WITH events AS (
                SELECT dolocationid AS zone_id,
                       EXTRACT(hour FROM tpep_dropoff_datetime)::int AS hour,
                       {night_drop} AS night_date,
                       1 AS dropoff, 0 AS pickup
                FROM {source}
                UNION ALL
                SELECT pulocationid,
                       EXTRACT(hour FROM tpep_pickup_datetime)::int,
                       {night_pick},
                       0, 1
                FROM {source}
            )
            SELECT zone_id,
                   night_date,
                   {night_type} AS night_type,
                   hour,
                   (hour - {start_h} + 24) % 24 AS hour_order,   -- 0 = 20:00 ... 9 = 05:00, for charts
                   sum(dropoff)::bigint AS dropoffs,
                   sum(pickup)::bigint  AS pickups
            FROM events
            WHERE (hour >= {start_h} OR hour < {end_h})
              AND night_date BETWEEN {first} AND {last}
            GROUP BY zone_id, night_date, hour
        """).format(hour_table=hour_table, source=source,
                    night_drop=night_of("tpep_dropoff_datetime"),
                    night_pick=night_of("tpep_pickup_datetime"),
                    night_type=night_type(sql.SQL("night_date")),
                    start_h=start_h, end_h=end_h, first=first, last=last))

        cur.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(metrics_table))
        cur.execute(sql.SQL("""
            CREATE TABLE {metrics_table} AS
            WITH nights AS (
                SELECT d::date AS night_date, {night_type} AS night_type
                FROM generate_series({first}::date, {last}::date, interval '1 day') AS d
            ),
            night_counts AS (
                SELECT night_type, count(*) AS nights FROM nights GROUP BY night_type
                UNION ALL
                SELECT 'all', count(*) FROM nights
            ),
            sums AS (
                SELECT zone_id,
                       CASE WHEN GROUPING(night_type) = 1 THEN 'all' ELSE night_type END AS night_type,
                       sum(dropoffs) FILTER (WHERE hour >= {start_h}) AS evening_arrivals,
                       sum(pickups)  FILTER (WHERE hour <  {late_h})  AS late_departures,
                       sum(dropoffs) FILTER (WHERE hour <  {late_h})  AS late_arrivals
                FROM {hour_table}
                GROUP BY GROUPING SETS ((zone_id, night_type), (zone_id))
            )
            SELECT z.locationid AS zone_id,
                   z.borough,
                   z.zone,
                   nc.night_type,
                   nc.nights,
                   coalesce(s.evening_arrivals, 0)::bigint AS evening_arrivals,   -- E
                   coalesce(s.late_departures, 0)::bigint  AS late_departures,    -- L
                   coalesce(s.late_arrivals, 0)::bigint    AS late_arrivals,      -- H
                   round(coalesce(s.evening_arrivals, 0)::numeric / nc.nights, 2) AS avg_evening_arrivals,
                   round(coalesce(s.late_departures, 0)::numeric  / nc.nights, 2) AS avg_late_departures,
                   round(coalesce(s.late_arrivals, 0)::numeric    / nc.nights, 2) AS avg_late_arrivals,
                   CASE WHEN coalesce(s.late_arrivals, 0) > 0
                        THEN round(s.late_departures::numeric / s.late_arrivals, 3)
                   END AS nightlife_score,
                   (coalesce(s.late_departures, 0) >= {min_trips}
                    AND coalesce(s.late_arrivals, 0) >= {min_trips}) AS enough_trips,
                   CASE WHEN z.locationid IN ({airports}) THEN 'airport'
                        WHEN z.locationid IN ({unknown}) THEN 'unknown zone'
                   END AS excluded_reason
            FROM taxi_zones z
            CROSS JOIN night_counts nc
            LEFT JOIN sums s ON s.zone_id = z.locationid AND s.night_type = nc.night_type
            ORDER BY z.locationid, nc.night_type
        """).format(metrics_table=metrics_table, hour_table=hour_table,
                    night_type=night_type(sql.SQL("d")),
                    first=first, last=last, start_h=start_h, late_h=late_h,
                    min_trips=sql.Literal(MIN_TRIPS),
                    airports=sql.SQL(", ").join(sql.Literal(z) for z in AIRPORT_ZONES),
                    unknown=sql.SQL(", ").join(sql.Literal(z) for z in UNKNOWN_ZONES)))

        cur.execute(sql.SQL("""
            SELECT
                count(*) FILTER (WHERE NOT (EXTRACT(hour FROM tpep_pickup_datetime) >= {start_h}
                                            OR EXTRACT(hour FROM tpep_pickup_datetime) < {end_h})),
                count(*) FILTER (WHERE (EXTRACT(hour FROM tpep_pickup_datetime) >= {start_h}
                                        OR EXTRACT(hour FROM tpep_pickup_datetime) < {end_h})
                                   AND {night_pick} NOT BETWEEN {first} AND {last})
            FROM {source}
        """).format(source=source, start_h=start_h, end_h=end_h, first=first, last=last,
                    night_pick=night_of("tpep_pickup_datetime")))
        outside_hours, edge_nights = cur.fetchone()

        cur.execute(sql.SQL("SELECT coalesce(sum(pickups), 0), coalesce(sum(dropoffs), 0) FROM {}"
                            ).format(hour_table))
        pickups_kept, dropoffs_kept = cur.fetchone()

        cur.execute(sql.SQL("SELECT count(*) FROM {} WHERE zone_id NOT IN (SELECT locationid FROM taxi_zones)"
                            ).format(hour_table))
        unknown_zone_rows = cur.fetchone()[0]

        problems = []
        if outside_hours:
            problems.append(f"{outside_hours:,} pickups in {SOURCE_TABLE} are outside the night hours "
                            "(check NIGHT_START_HOUR / NIGHT_END_HOUR match the night table)")
        if pickups_kept + edge_nights + outside_hours != source_total:
            problems.append(f"pickups kept {pickups_kept:,} + edge nights {edge_nights:,} "
                            f"!= source rows {source_total:,}")
        if unknown_zone_rows:
            problems.append(f"{unknown_zone_rows:,} rows in {HOUR_TABLE} have a zone not in taxi_zones")
        if problems:
            conn.rollback()
            raise SystemExit("Reconciliation failed, nothing was saved: " + "; ".join(problems))

        conn.commit()

        cur.execute(sql.SQL("SELECT night_type, max(nights) FROM {} GROUP BY 1 ORDER BY 1").format(metrics_table))
        nights = dict(cur.fetchall())

        cur.execute(sql.SQL("""
            SELECT zone, borough, nightlife_score, late_departures, late_arrivals
            FROM {} WHERE night_type = 'weekend' AND enough_trips AND excluded_reason IS NULL
            ORDER BY nightlife_score DESC LIMIT 10
        """).format(metrics_table))
        top = cur.fetchall()

        cur.execute(sql.SQL("""
            SELECT zone, night_type, nightlife_score, late_departures, late_arrivals, enough_trips
            FROM {} WHERE zone ILIKE ANY (%s) AND night_type IN ('weekday', 'weekend')
            ORDER BY zone, night_type
        """).format(metrics_table), (CHECK_ZONES,))
        checks = cur.fetchall()

        cur.execute(sql.SQL("SELECT count(*) FILTER (WHERE enough_trips AND excluded_reason IS NULL) "
                            "FROM {} WHERE night_type = 'all'").format(metrics_table))
        ranked_zones = cur.fetchone()[0]

    conn.close()
    seconds = round(time.perf_counter() - t_start, 2)

    print("\nDone.")
    print(f"  nights:          {nights.get('all', 0)} "
          f"({nights.get('weekday', 0)} weekday, {nights.get('weekend', 0)} weekend)")
    print(f"  pickups used:    {pickups_kept:,}   (+ {edge_nights:,} on the two incomplete edge nights)")
    print(f"  drop-offs used:  {dropoffs_kept:,}   (drop-offs after {NIGHT_END_HOUR - 1}:59 are not night events)")
    print("  check:           pickups used + edge nights = source rows  ->  OK")
    print(f"  zones with enough trips (all nights, airports excluded): {ranked_zones}")

    print("\nTop 10 weekend nightlife scores (L / H):")
    for zone, borough, score, late_dep, late_arr in top:
        print(f"  {score:>6}  {zone} ({borough})   L={late_dep:,}  H={late_arr:,}")

    print("\nSanity check: nightlife areas should be above 1, residential ones below 1")
    for zone, ntype, score, late_dep, late_arr, enough in checks:
        note = "" if enough else "   (too few trips)"
        print(f"  {zone:<32}{ntype:<9}{str(score):>7}   L={late_dep:,}  H={late_arr:,}{note}")

    print(f"\n  time of this run: {seconds} s")


if __name__ == "__main__":
    main()
