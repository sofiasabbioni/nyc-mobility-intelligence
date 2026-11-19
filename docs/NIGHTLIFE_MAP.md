# Nightlife map (Matteo)

Interactive map of NYC taxi zones, shown inside the existing dashboard tab
**"The night in the city"**, below Sofia's city charts. Code: `app/nightlife_map.py`,
called from `app/dashboard.py` with `render_nightlife_map(RESULTS)`.

## What the map shows

- A choropleth of the official TLC taxi zones, coloured by one measure:
  - **Nightlife score** = late departures / late arrivals (pickups / drop-offs between 00:00 and 04:59).
    Above 1 people leave the zone late at night more than they arrive (going-out zone);
    below 1 they arrive and stay (residential zone). Diverging colour scale centred on 1.
  - **Late departures per night**, **evening arrivals per night**, **late arrivals per night**.
- Filters: night type (weekend / weekday / all), colour measure, boroughs.
- A **top-10 table** for the selected measure.
- An **hour-by-hour chart** (18:00 ... 05:00) of pickups and drop-offs per night for up to three zones.
- Tooltips with zone name, borough and the three per-night activity values.

## Data sources

- Numbers: Benedetta's exported results, read from CSV (not from PostgreSQL):
  - `results/nightlife/zone_metrics.csv`: 795 rows (265 zones x 3 night types), 14 columns.
  - `results/nightlife/hourly.csv`: zone, night type, hour, pickups, drop-offs.
  - Produced by `loader/nightlife_metrics.py`, see `docs/NIGHTLIFE_METHODOLOGY.md`.
- Geometry: official TLC taxi-zone shapes, `data/raw/taxi_zones.zip`
  (https://d37ci6vzurychx.cloudfront.net/misc/taxi_zones.zip), converted to EPSG:4326.
  Join: `zone_metrics.zone_id = LocationID`. Zones split in several polygons are dissolved by `LocationID`.
- `results/` and `data/` are not committed (ignored by Git).

## Definitions (data contract)

- Night: 18:00-05:59. Late window: 00:00-04:59. Evening arrivals: 18:00-23:59.
- A night is named after its evening; Friday and Saturday nights are weekend nights.
- Hour order for the chart: `(hour - 18) % 24`, so 18:00 -> 0 and 05:00 -> 11.
- For the score ranking, airports, unknown zones and low-volume zones
  (`enough_trips = False`, fewer than 100 late events) are excluded.
- Period in the current results: April-July 2026, 121 complete nights (87 weekday, 34 weekend).

## How to run

Local preview on Windows (from the repository root, with `.venv` created as in `docs/WINDOWS_GUIDE.md`):

```powershell
$env:RESULTS_DIR = (Resolve-Path ".\results").Path
$env:DATA_DIR = (Resolve-Path ".\data\raw").Path
& .\.venv\Scripts\python.exe -m streamlit run app\dashboard.py --server.port 8502
```

Docker (app container only, without the loader):

```powershell
docker compose -p taxi_matteo_preview build app
docker compose -p taxi_matteo_preview run --rm --no-deps -p 8502:8501 app
```

The `app` service now also mounts `./data/raw:/data/raw:ro` and sets `DATA_DIR: /data/raw`;
`app/Dockerfile` copies `nightlife_map.py`; `app/requirements.txt` adds `plotly==7.1.0` and `geopandas==1.2.0`.

## What was tested (19/11/2026, Windows 11, Python 3.12.10)

- `pytest tests\test_nightlife_map.py -q`: 2 passed (hour order across midnight; ratio ranking excludes
  airports and low-volume zones).
- `pytest tests -q`: 81 passed, 5 skipped.
- `docker compose config --quiet`: no errors.
- `docker compose -p taxi_matteo_preview build app`: image built.
- Local preview at http://localhost:8502: map drawn with 263 zone shapes, 34 weekend nights, top-10 table and
  hourly chart visible, no exceptions. The other tabs show "file not found yet" messages because the
  benchmark results are not on this machine (expected).

## First results (weekend nights, score ranking)

Greenwich Village South (3.12), Brooklyn Navy Yard (2.92), Williamsburg North Side (2.44),
West Village (2.43), Lower East Side (2.39), Meatpacking/West Village West (2.28), East Village (1.97).
Residential areas such as the Upper East Side stay below 1.

## Limits

- Taxi trips are a **mobility proxy**: they do not measure the number of bars, clubs or visitors,
  and people also use the subway, ride-hailing apps (not in yellow-taxi data), walking or cycling.
- Yellow taxis are concentrated in Manhattan, so outer-borough zones have fewer trips and more of them
  are excluded as low volume.
- A ratio can be high with few trips: Brooklyn Navy Yard is second with only 837 late departures and
  287 late arrivals. The 100-event threshold is an analysis choice; a higher threshold would remove it.
- Taxi zones are areas, not exact pickup points.
