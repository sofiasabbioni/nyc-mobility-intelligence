"""Small synthetic TLC-like files for tests (NOT real data). Includes deliberate bad rows."""
import csv
import datetime as dt
import random
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

MONTHS = ["2026-04", "2026-05", "2026-06", "2026-07"]
NAMES = ["tpep_pickup_datetime", "tpep_dropoff_datetime", "PULocationID", "DOLocationID",
         "passenger_count", "trip_distance", "fare_amount", "tip_amount", "total_amount"]


def month_rows(month: str, rows: int, seed: int):
    random.seed(seed)
    y, mo = map(int, month.split("-"))
    start = dt.datetime(y, mo, 1)
    nxt = dt.datetime(y + (mo == 12), 1 if mo == 12 else mo + 1, 1)
    secs = int((nxt - start).total_seconds())
    out = []
    for _ in range(rows):
        t = start + dt.timedelta(seconds=random.randrange(secs))
        fare = round(random.uniform(4, 80), 2)
        out.append([t, t + dt.timedelta(minutes=random.randint(3, 40)), random.randint(1, 265),
                    random.randint(1, 265), random.choice([1.0, 2.0, None]), round(random.uniform(0.3, 20), 2),
                    fare, round(fare * 0.1, 2), round(fare * 1.1 + 1.5, 2)])
    out += bad_rows(month)
    out.sort(key=lambda r: (r[0] is None, r[0] or start))
    return out


def bad_rows(month: str):
    """14 hand-made rows: one per exclusion reason (zone and amount twice), 2 valid daytime/06:00
    rows that the night filter removes, and 4 valid night rows on the window boundaries."""
    y, mo = map(int, month.split("-"))
    start = dt.datetime(y, mo, 1)
    night = lambda h: start + dt.timedelta(days=9, hours=h)  # a known timestamp
    ok = lambda ts: [ts, ts + dt.timedelta(minutes=9), 5, 6, 1.0, 2.0, 10.0, 1.0, 12.0]
    bad = [
        [None] + ok(night(20))[1:],                                       # pickup missing
        ok(start - dt.timedelta(days=2) + dt.timedelta(hours=20)),         # pickup in previous month
        [night(21), None] + ok(night(21))[2:],                            # dropoff missing
        [night(22), night(22) - dt.timedelta(minutes=3)] + ok(night(22))[2:],  # dropoff before pickup
        ok(night(23))[:2] + [0] + ok(night(23))[3:],                      # zone 0
        ok(night(23))[:3] + [None] + ok(night(23))[4:],                   # zone missing
        ok(night(19))[:6] + [-3.0] + ok(night(19))[7:],                   # negative fare
        ok(night(19))[:5] + [float("nan")] + ok(night(19))[6:],           # NaN distance
        ok(night(12)),                                                    # valid but daytime
        ok(night(18)), ok(night(23)), ok(night(0)), ok(night(5)),         # valid night, boundaries
        ok(night(6)),                                                     # 06:00 is NOT night
    ]
    return bad


def make_raw(raw: Path, rows: int = 3000, months=MONTHS, extra_column: bool = True):
    raw.mkdir(parents=True, exist_ok=True)
    for i, m in enumerate(months):
        data = month_rows(m, rows, seed=i)
        cols = list(zip(*data))
        types = [pa.timestamp("us"), pa.timestamp("us"), pa.int32(), pa.int32(), pa.float64(),
                 pa.float64(), pa.float64(), pa.float64(), pa.float64()]
        arrays = [pa.array(c, type=t) for c, t in zip(cols, types)]
        names = list(NAMES)
        if extra_column:
            arrays.append(pa.array([1] * len(data), type=pa.int32()))
            names.append("VendorID")
        pq.write_table(pa.Table.from_arrays(arrays, names=names), raw / f"yellow_tripdata_{m}.parquet")
    with open(raw / "taxi_zone_lookup.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["LocationID", "Borough", "Zone", "service_zone"])
        boroughs = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "EWR", "Unknown"]
        for i in range(1, 266):
            w.writerow([i, boroughs[i % 7], f"Zone {i}", "Yellow Zone" if i % 2 else "N/A"])
