# Dashboard user guide

## Goal

Show how to read the NYC nightlife visualizations in the dashboard, not only how to open them.
Everything below was checked on the running dashboard (local preview, 25/11/2026) with the nightlife
results produced by `loader/nightlife_metrics.py` on 18/11/2026: April-July 2026, 121 complete nights
(87 weekday, 34 weekend), see `results/nightlife/validation.json`. Numbers quoted here come from
`results/nightlife/zone_metrics.csv`; with a newer run they can change.

## How to open the dashboard

- Full pipeline (as in the README): `docker compose up --build`, then open http://localhost:8501.
- Only the app, reusing existing results (what I used): from the repository root

```powershell
$env:RESULTS_DIR = (Resolve-Path ".\results").Path
$env:DATA_DIR = (Resolve-Path ".\data\raw").Path
& .\.venv\Scripts\python.exe -m streamlit run app\dashboard.py --server.port 8502
```

  then open http://localhost:8502. The Docker equivalent is
  `docker compose run --rm --no-deps -p 8502:8501 app`.
- The map needs `results/nightlife/zone_metrics.csv`, `results/nightlife/hourly.csv` and the official
  zone shapes in `data/raw/taxi_zones.zip`. If one is missing the dashboard shows a message instead of the map.

The nightlife section is in the first tab, **The night in the city**, below the city charts, under the
heading **NYC After Dark: nightlife geography**. Its controls are:

| Control | Options |
|---|---|
| **Night type** | `weekend` (Friday and Saturday nights), `weekday`, `all` |
| **Colour by** | Nightlife score (late departures / late arrivals), Late departures per night, Evening arrivals per night, Late arrivals per night |
| **Boroughs** | multiselect, all boroughs selected by default (EWR and Unknown are not offered) |
| **Map view** | `2D map` or `3D map` |
| **Select up to three zones** | zones drawn in the hourly chart (default: the two highest for the selected measure) |

Below the map: **Top 10 zones for the selected measure** and **Pickup and drop-off patterns by hour**.

## Walkthrough 1 - Compare night types

- Tab: **The night in the city**.
- Filters: **Night type** `weekend`, then `weekday`; **Colour by** *Late departures per night*.
- What to look for: the metric **Nights covered** changes (34 weekend nights, 87 weekday nights), and the
  colours and the Top 10 change with it. Per-night late departures are much higher on weekend nights in
  the same zones: East Village goes from about 328 to about 2,300 late departures per night,
  Lower East Side from about 231 to about 1,615.
- Then switch **Colour by** to the *Nightlife score*: the Top 10 is led by Greenwich Village South on both
  night types, but the rest of the list changes (e.g. Williamsburg North Side is in the weekend top 3, not
  in the weekday one).
- Limitation: weekend and weekday have different numbers of nights (34 vs 87). Compare the **per-night**
  values, never raw totals, and remember that the `all` option mixes the two.

## Walkthrough 2 - Compare boroughs and zones

- Filters: keep the night type fixed, then use **Boroughs** to leave only one borough (for example
  only Brooklyn). The map, the Top 10 and the zone list of the hourly chart are recomputed on that subset.
- What to look for: with the score, Brooklyn's top zones on weekend nights are Brooklyn Navy Yard,
  Williamsburg North Side, East Williamsburg and Bushwick North. Selecting only Staten Island with the score
  shows the message "No zones satisfy the selected filters": none of its zones reaches the volume threshold.
- In **Select up to three zones** pick two zones (e.g. East Village and Upper East Side North) to compare
  their pickups (solid line) and drop-offs (dashed line) hour by hour, from 18:00 to 05:00.
- Limitation: a borough is not a unit of analysis here. Every value is computed per **taxi zone**; the
  borough filter only selects which zones are shown. Taxi zones are areas, not exact pickup points.

## Walkthrough 3 - Compare ratios and absolute volumes

- Metric: **nightlife score = late yellow-taxi pickups / late drop-offs** in the zone (00:00-04:59).
- Above 1 means more late pickups than drop-offs were recorded, below 1 the reverse. This is a
  directional **taxi-flow asymmetry**, not proof that a zone is a nightlife or a residential area.
- What to look for: with **Night type** `weekend` and the score, Brooklyn Navy Yard is second in the
  Top 10 (score about 2.9), but the table also shows its volumes: about 25 late departures and 8 late
  arrivals per night, against about 1,600 departures per night in Lower East Side (score about 2.4).
  Switching **Colour by** to *Late departures per night* gives a very different Top 10 (East Village,
  Lower East Side, West Village, Greenwich Village South...).
- Always check the absolute volumes before reading an extreme ratio. Zones with fewer than 100 late
  pickups and drop-offs (and airports / unknown zones) are not ranked and not drawn on the score map.

## 2D and 3D views

The **Map view** switch shows the same zones, filters and values in two ways:

- **2D map** (Plotly): each zone is coloured by the selected measure; hover shows the zone, borough, the
  selected value and the three per-night activity values.
- **3D map** (PyDeck, `app/nightlife_3d.py`): the same zones are extruded; **height and colour both show the
  selected measure** (height is not building height). Zones that are not ranked or are outside the selected
  boroughs are drawn flat and grey as a backdrop. Drag to pan, right-drag or Ctrl-drag to rotate and tilt,
  scroll to zoom.
- Both views use the same colour scale, from 0 to a cap at the 95th percentile of the zones shown (at least 2
  for the score). Zones above the cap get the top colour (and in 3D the maximum height); the tooltip always
  shows the exact value.
- Use the 2D map to compare zones precisely; the 3D map helps to see where activity is concentrated, but
  perspective and occlusion make close comparisons harder.

## Known limitations

- Yellow taxis are only one way to move at night (subway, ride-hailing apps, walking and cycling are not
  in the data), so the map shows taxi activity, not nightlife attendance.
- The score can be moved by things other than nightlife: transport hubs, work shifts and commuting,
  where taxis are available to be hailed. It must be read together with the absolute volumes.
- The 100-event threshold is an analysis choice: low-volume zones (many outside Manhattan) disappear from
  the score map, so absence on the map does not mean "no nightlife".
- Weekend and weekday cover different numbers of nights; the `all` option mixes both.
