# Nightlife map (Matteo)

Interactive map of NYC taxi zones, shown inside the existing dashboard tab
**"The night in the city"**, below Sofia's city charts. Code: `app/nightlife_map.py`,
called from `app/dashboard.py` with `render_nightlife_map(RESULTS)`.

## What the map shows

- A choropleth of the official TLC taxi zones, coloured by one measure:
  - **Nightlife score** = late departures / late arrivals, i.e. yellow-taxi pickups / drop-offs in the zone
    between 00:00 and 04:59, for the selected night type. Above 1 means more late yellow-taxi pickups than
    drop-offs were observed; below 1 the reverse. Diverging colour scale centred on 1.
    The score is a **mobility asymmetry proxy**: it is not proof of nightlife, land use, demographics or
    residential character. Other causes can move it (transport hubs, work shifts and commuting patterns,
    where taxis are available to be hailed), so it must be read together with the absolute trip volumes.
  - **Late departures per night**, **evening arrivals per night**, **late arrivals per night**.
- Filters: night type (weekend / weekday / all), colour measure, boroughs.
- A **top-10 table** for the selected measure. When ranking by the score, the table also shows late departures
  and late arrivals per night, so a high ratio built on few trips is visible.
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
West Village (2.43), Lower East Side (2.39), Meatpacking/West Village West (2.28), East Village (1.97):
in these zones more late yellow-taxi pickups than drop-offs were recorded. Zones such as the Upper East Side
show the opposite asymmetry (score below 1). These are observed taxi patterns, not a classification of
neighbourhoods.

## Limits

- Taxi trips are a **mobility proxy**: they do not measure the number of bars, clubs or visitors,
  and people also use the subway, ride-hailing apps (not in yellow-taxi data), walking or cycling.
- Yellow taxis are concentrated in Manhattan, so outer-borough zones have fewer trips and more of them
  are excluded as low volume.
- A ratio can be high with few trips: Brooklyn Navy Yard is second with only 837 late departures and
  287 late arrivals. The 100-event threshold is an analysis choice; a higher threshold would remove it.
- Taxi zones are areas, not exact pickup points.

## 3D view (map view selector)

Above the map, **Map view** switches between **2D map** (default, unchanged) and **3D map** (PyDeck / deck.gl,
`app/nightlife_3d.py`, called from `render_nightlife_map`).

- Same rows as the 2D map: the weekday/weekend/all, colour-measure and borough filters, the airport / unknown /
  low-volume exclusions and the 100-event rule are applied once, before either view is drawn.
- Each taxi zone is an extruded polygon. **Height = the selected measure** (nightlife score, late departures,
  evening arrivals or late arrivals per night). It is NOT building height.
- Colour uses the same scale as the 2D map (`colour_scale` in `nightlife_map.py`: RdBu_r for the score,
  Viridis otherwise, domain 0 to the 95th percentile of the zones shown). Height uses the same domain, so a zone
  above that maximum is drawn at the maximum height; its exact value is in the tooltip.
  Maximum extrusion is `MAX_HEIGHT_M` (6000 m, deliberately exaggerated).
- Zones not drawn in 2D (airports, unknown, low-volume, filtered-out boroughs) appear flat and grey, only as a
  geographic backdrop. They carry no value.
- Tooltip: zone, borough, selected value, evening arrivals / late departures / late arrivals per night (same
  rounding as the 2D tooltip).
- Interaction: drag = pan, scroll = zoom, right-drag or Ctrl-drag = rotate and tilt. Initial camera: pitch 55 deg,
  bearing -29 deg (aligns Manhattan's street grid vertically).
- Performance: the zone polygons are simplified (about 20 m) and rounded to about 1 m for the 3D view only, and
  cached. The 2D map keeps the full geometry.
- Needs `pydeck` (pinned in `app/requirements.txt`). If it is missing, a message is shown and 2D still works.
- The base map tiles (Carto) are loaded by the browser, so the 3D map needs internet access like the 2D map.
