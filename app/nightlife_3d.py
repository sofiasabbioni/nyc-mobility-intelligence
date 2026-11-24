"""3D (extruded) view of the nightlife map, drawn with PyDeck / deck.gl.

Presentation only. It receives the SAME rows and the SAME colour scale as the 2D map
(see nightlife_map.colour_scale): no metric is recomputed here.

Height encodes the selected nightlife measure, not building height. It uses the same
domain as the colour scale (0 .. zmax, zmax = the colour maximum), so a zone above zmax is
drawn at the maximum height and coloured with the top colour; its exact value is in the tooltip.
"""
from __future__ import annotations

import html
import re
from typing import Iterable

import pandas as pd
import plotly.colors as pc

MAX_HEIGHT_M = 6000        # metres of extrusion at the top of the scale (exaggerated on purpose)
SIMPLIFY_DEG = 0.0002      # ~20 m: far below what is visible at city zoom, keeps the payload small
GRID_DEG = 1e-5            # coordinate precision (~1 m)
CONTEXT_FILL = [226, 231, 242, 255]
LINE_COLOUR = [255, 255, 255, 140]
VIEW = {"latitude": 40.70, "longitude": -73.93, "zoom": 9.7, "pitch": 55, "bearing": -29}

_RGB = re.compile(r"[\d.]+")


def colour_for(colorscale: str, value: float, zmax: float, alpha: int = 225) -> list[int]:
    """RGBA of `value` on a linear 0..zmax domain: the same mapping the 2D choropleth uses."""
    t = 0.0 if zmax <= 0 else min(max(float(value) / zmax, 0.0), 1.0)
    rgb = pc.sample_colorscale(colorscale, [t])[0]
    r, g, b = (int(round(float(x))) for x in _RGB.findall(rgb)[:3])
    return [r, g, b, alpha]


def height_for(value: float, zmax: float, max_height: float = MAX_HEIGHT_M) -> float:
    """Extrusion height in metres: proportional to the value, capped at the colour maximum."""
    if zmax <= 0:
        return 0.0
    return round(min(max(float(value), 0.0), zmax) / zmax * max_height, 1)


def simplify_shapes(shapes: dict, tolerance: float = SIMPLIFY_DEG) -> dict:
    """Lighter copy of the GeoJSON for the browser. Topology-preserving, ids untouched."""
    import shapely
    from shapely.geometry import mapping, shape

    feats = []
    for f in shapes["features"]:
        geom = shape(f["geometry"]).simplify(tolerance, preserve_topology=True)
        geom = shapely.set_precision(geom, GRID_DEG)
        if geom.is_empty:
            continue
        feats.append({"type": "Feature", "properties": {"LocationID": int(f["properties"]["LocationID"])},
                      "geometry": mapping(geom)})
    return {"type": "FeatureCollection", "features": feats}


def _fmt(v, digits: int) -> str:
    return "n/a" if pd.isna(v) else f"{float(v):.{digits}f}"


def build_layers_data(shapes: dict, data: pd.DataFrame, metric: str, metric_label: str,
                      colorscale: str, zmax: float) -> tuple[dict, dict]:
    """(zones, context) GeoJSON collections.

    zones   = one feature per zone present in `data` (the rows the 2D map draws), extruded and coloured.
    context = every other polygon, flat and grey, so the geography stays readable (airports, unranked
              or filtered-out zones). Nothing is dropped from the data: this is only the backdrop.
    """
    uniq = data.drop_duplicates("zone_id")
    rows = uniq.set_index(uniq["zone_id"].astype(int))
    zones, context = [], []
    for f in shapes["features"]:
        zid = int(f["properties"]["LocationID"])
        if zid not in rows.index:
            context.append({"type": "Feature", "properties": {"LocationID": zid}, "geometry": f["geometry"]})
            continue
        r = rows.loc[zid]
        value = float(r[metric])
        zones.append({"type": "Feature", "geometry": f["geometry"], "properties": {
            "LocationID": zid,
            "zone": html.escape(str(r["zone"])),
            "borough": html.escape(str(r["borough"])),
            "metric_label": metric_label,
            "value_text": f"{value:.2f}",
            "evening_text": _fmt(r["avg_evening_arrivals"], 1),
            "departures_text": _fmt(r["avg_late_departures"], 1),
            "arrivals_text": _fmt(r["avg_late_arrivals"], 1),
            "elevation": height_for(value, zmax),
            "fill": colour_for(colorscale, value, zmax),
        }})
    return ({"type": "FeatureCollection", "features": zones},
            {"type": "FeatureCollection", "features": context})


TOOLTIP = {
    "html": ("<b>{zone}</b> ({borough})<br/>{metric_label}: <b>{value_text}</b><br/>"
             "Evening arrivals/night: {evening_text}<br/>Late departures/night: {departures_text}<br/>"
             "Late arrivals/night: {arrivals_text}"),
    "style": {"backgroundColor": "#0b1530", "color": "#e6ecff", "fontSize": "12px", "borderRadius": "8px",
              "padding": "8px 10px", "fontFamily": "Inter, system-ui, sans-serif"},
}


def build_deck(zones: dict, context: dict):
    """PyDeck Deck: flat grey backdrop + extruded, pickable zones. Raises ImportError without pydeck."""
    import pydeck as pdk

    backdrop = pdk.Layer("GeoJsonLayer", context, id="context", stroked=True, filled=True, extruded=False,
                         get_fill_color=CONTEXT_FILL, get_line_color=[200, 207, 222, 255],
                         line_width_min_pixels=0.5, pickable=False)
    towers = pdk.Layer("GeoJsonLayer", zones, id="zones", stroked=True, filled=True, extruded=True,
                       wireframe=False, get_elevation="properties.elevation",
                       get_fill_color="properties.fill", get_line_color=LINE_COLOUR,
                       line_width_min_pixels=0.5, pickable=True, auto_highlight=True,
                       highlight_color=[255, 255, 255, 90], opacity=0.95)
    view = pdk.ViewState(min_zoom=8, max_zoom=15, max_pitch=75, **VIEW)
    return pdk.Deck(layers=[backdrop, towers], initial_view_state=view, map_style="light",
                    tooltip=TOOLTIP, height=590)


def legend_html(colorscale: str, zmax: float, metric_label: str, capped: bool = True) -> str:
    """Small legend: colour bar (same scale as the 2D map) and what height means."""
    stops = ", ".join("rgb({},{},{}) {}%".format(*colour_for(colorscale, i / 10 * zmax, zmax)[:3], i * 10)
                      for i in range(11))
    label = html.escape(metric_label)
    note = (f" Colour and height are capped at {zmax:.2f} (95th percentile of the zones shown, as in the 2D "
            f"map); the tooltip always shows the exact value.") if capped else ""
    return (
        '<div class="night3d-legend">'
        f'<div class="night3d-bar" style="background:linear-gradient(90deg,{stops})"></div>'
        f'<div class="night3d-scale"><span>0</span><span>{zmax:.2f}+</span></div>'
        f'<p><b>Height and colour show: {label}.</b> Height is nightlife activity, not building height. '
        f'Grey, flat zones are not ranked (airports, unknown, low-volume or outside the selected boroughs).'
        f'{note} Drag to pan; right-drag or Ctrl-drag to rotate and tilt; scroll to zoom.</p></div>')


LEGEND_CSS = """
<style>
.night3d-legend { max-width: 34rem; margin: .35rem 0 .2rem; font-size: .86rem; color: var(--muted, #5d6b8c); }
.night3d-bar { height: .65rem; border-radius: .4rem; border: 1px solid rgba(15,27,61,.12); }
.night3d-scale { display: flex; justify-content: space-between; font-size: .78rem; margin: .15rem 0 .35rem; }
.night3d-legend p { margin: 0; line-height: 1.4; }
</style>
"""


def zone_ids(collection: dict) -> Iterable[int]:
    return [int(f["properties"]["LocationID"]) for f in collection["features"]]
