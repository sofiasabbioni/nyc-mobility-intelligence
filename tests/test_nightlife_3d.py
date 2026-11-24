"""3D nightlife map: pure helpers, deck definition and the Streamlit wiring. Synthetic data only."""
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import nightlife_3d as n3
from nightlife_map import REQUIRED, colour_scale

ROWS = [  # zone_id, zone, borough, evening, late_dep, late_arr, score
    (1, "Alpha", "Manhattan", 100.0, 40.0, 10.0, 4.0),
    (2, "Beta & Co", "Brooklyn", 50.0, 5.0, 5.0, 1.0),
    (3, "Gamma", "Queens", 10.0, 1.0, 4.0, 0.25),
    (4, "Delta", "Bronx", 70.0, 90.0, 9.0, 10.0),
]


def frame():
    return pd.DataFrame([dict(zone_id=z, zone=n, borough=b, night_type="weekend", nights=34,
                              avg_evening_arrivals=e, avg_late_departures=d, avg_late_arrivals=a,
                              nightlife_score=s, enough_trips=True, excluded_reason=None)
                         for z, n, b, e, d, a, s in ROWS])


def squares(ids):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"LocationID": i, "extra": "dropped"},
         "geometry": {"type": "Polygon", "coordinates": [[[-74 + i * .02, 40.7], [-73.99 + i * .02, 40.7],
                                                           [-73.99 + i * .02, 40.71], [-74 + i * .02, 40.71],
                                                           [-74 + i * .02, 40.7]]]}} for i in ids]}


def test_colour_scale_is_what_the_2d_map_used():
    d = frame()
    assert colour_scale("nightlife_score", d) == ("RdBu_r", max(2, float(d["nightlife_score"].quantile(.95))))
    assert colour_scale("avg_late_departures", d) == ("Viridis", max(1, float(d["avg_late_departures"].quantile(.95))))
    small = d.assign(avg_late_arrivals=0.1)
    assert colour_scale("avg_late_arrivals", small)[1] == 1          # floors kept
    assert colour_scale("nightlife_score", small.assign(nightlife_score=.5))[1] == 2


def test_colour_for_matches_plotly_scale_and_clips():
    import plotly.colors as pc
    assert n3.colour_for("RdBu_r", 0, 4)[:3] == [int(x) for x in pc.unlabel_rgb(pc.sample_colorscale("RdBu_r", [0])[0])]
    assert n3.colour_for("RdBu_r", 4, 4) == n3.colour_for("RdBu_r", 99, 4)       # clipped at the top
    assert n3.colour_for("RdBu_r", -3, 4) == n3.colour_for("RdBu_r", 0, 4)
    assert n3.colour_for("Viridis", 2, 4)[:3] == [int(round(x)) for x in pc.unlabel_rgb(pc.sample_colorscale("Viridis", [.5])[0])]
    assert len(n3.colour_for("Viridis", 1, 0)) == 4                                # zmax 0 does not crash


def test_height_is_proportional_capped_and_non_negative():
    assert n3.height_for(0, 4) == 0
    assert n3.height_for(2, 4) == n3.MAX_HEIGHT_M / 2
    assert n3.height_for(4, 4) == n3.height_for(40, 4) == n3.MAX_HEIGHT_M
    assert n3.height_for(-1, 4) == 0 and n3.height_for(1, 0) == 0


def test_layers_keep_every_zone_of_the_2d_data_and_the_rest_is_backdrop():
    d = frame()
    zones, ctx = n3.build_layers_data(squares([1, 2, 3, 4, 5, 6]), d, "nightlife_score", "Score", "RdBu_r", 5.0)
    assert sorted(n3.zone_ids(zones)) == [1, 2, 3, 4]
    assert sorted(n3.zone_ids(ctx)) == [5, 6]
    assert all("elevation" not in f["properties"] for f in ctx["features"])


def test_heights_follow_the_selected_measure_not_a_building_height():
    d = frame()
    for metric in ("nightlife_score", "avg_late_departures", "avg_evening_arrivals", "avg_late_arrivals"):
        _, zmax = colour_scale(metric, d)
        zones, _ = n3.build_layers_data(squares([1, 2, 3, 4]), d, metric, "m", *colour_scale(metric, d))
        by_id = {f["properties"]["LocationID"]: f["properties"]["elevation"] for f in zones["features"]}
        order_by_value = d.sort_values(metric)["zone_id"].tolist()
        order_by_height = sorted(by_id, key=lambda k: (by_id[k], order_by_value.index(k)))
        assert order_by_height == order_by_value or len(set(by_id.values())) < 4   # ties only where capped
        assert max(by_id.values()) <= n3.MAX_HEIGHT_M


def test_tooltip_fields_exist_and_use_the_2d_rounding():
    d = frame()
    zones, _ = n3.build_layers_data(squares([1, 2]), d, "nightlife_score", "Score", "RdBu_r", 5.0)
    p = {f["properties"]["LocationID"]: f["properties"] for f in zones["features"]}[2]
    assert p["zone"] == "Beta &amp; Co" and p["borough"] == "Brooklyn"       # escaped for the HTML tooltip
    assert (p["value_text"], p["evening_text"], p["departures_text"], p["arrivals_text"]) == ("1.00", "50.0", "5.0", "5.0")
    for key in ("zone", "borough", "metric_label", "value_text", "evening_text", "departures_text", "arrivals_text"):
        assert "{" + key + "}" in n3.TOOLTIP["html"]
        assert key in p


def test_unknown_borough_and_missing_values_are_not_removed_or_crash():
    d = frame()
    d.loc[0, "borough"] = "Unknown"
    d.loc[1, "avg_evening_arrivals"] = np.nan
    zones, _ = n3.build_layers_data(squares([1, 2]), d, "nightlife_score", "Score", "RdBu_r", 5.0)
    props = {f["properties"]["LocationID"]: f["properties"] for f in zones["features"]}
    assert props[1]["borough"] == "Unknown" and props[2]["evening_text"] == "n/a"


def test_simplify_keeps_ids_and_reduces_vertices():
    ring = [[-74 + i * 1e-5, 40.7 + (1e-6 if i % 2 else 0)] for i in range(400)]
    ring += [[-73.9, 40.8], [-74, 40.7]]
    shapes = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"LocationID": 7, "x": 1}, "geometry": {"type": "Polygon", "coordinates": [ring]}}]}
    out = n3.simplify_shapes(shapes)
    assert out["features"][0]["properties"] == {"LocationID": 7}
    assert len(out["features"][0]["geometry"]["coordinates"][0]) < len(ring)


def test_deck_is_extruded_pickable_tilted_and_tooltipped():
    zones, ctx = n3.build_layers_data(squares([1, 2, 3, 4, 5]), frame(), "nightlife_score", "Score", "RdBu_r", 5.0)
    spec = json.loads(n3.build_deck(zones, ctx).to_json())
    layers = {l["id"]: l for l in spec["layers"]}
    assert layers["zones"]["extruded"] is True and layers["zones"]["pickable"] is True
    assert layers["context"]["extruded"] is False
    assert spec["initialViewState"]["pitch"] >= 40 and spec["initialViewState"]["maxPitch"] >= 60
    assert layers["zones"]["getElevation"] == "@@=properties.elevation"


def test_legend_says_height_is_nightlife_activity():
    html = n3.legend_html("RdBu_r", 5.0, "Late <b>x</b> per night")
    assert "not building height" in html and "&lt;b&gt;" in html and "5.00+" in html


# ---------------------------------------------------------------- Streamlit wiring
@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    import geopandas as gpd
    from shapely.geometry import box
    res, raw = tmp_path / "results", tmp_path / "raw"
    (res / "nightlife").mkdir(parents=True); raw.mkdir()
    geo = gpd.GeoDataFrame({"LocationID": [1, 2, 3, 4, 5]},
                           geometry=[box(-74 + i * .03, 40.7, -73.98 + i * .03, 40.72) for i in range(5)], crs="EPSG:4326").to_crs(2263)
    shp = tmp_path / "shp"; shp.mkdir(); geo.to_file(shp / "taxi_zones.shp")
    with zipfile.ZipFile(raw / "taxi_zones.zip", "w") as z:
        for f in shp.glob("taxi_zones.*"):
            z.write(f, f.name)
    rows = []
    for nt in ("weekend", "weekday", "all"):
        for _, r in frame().iterrows():
            rows.append({**r.to_dict(), "night_type": nt})
    rows.append({**frame().iloc[0].to_dict(), "zone_id": 5, "zone": "Airport X", "borough": "Queens",
                 "excluded_reason": "airport", "night_type": "weekend"})
    m = pd.DataFrame(rows); assert REQUIRED.issubset(m.columns)
    m.to_csv(res / "nightlife" / "zone_metrics.csv", index=False)
    pd.DataFrame([dict(zone_id=1, night_type="weekend", hour=h, pickups=3, dropoffs=2) for h in range(24)]).to_csv(
        res / "nightlife" / "hourly.csv", index=False)
    monkeypatch.setenv("RESULTS_DIR", str(res)); monkeypatch.setenv("DATA_DIR", str(raw))
    return res


def run_app():
    from streamlit.testing.v1 import AppTest
    app = str(Path(__file__).resolve().parents[1] / "app" / "dashboard.py")
    return AppTest.from_file(app, default_timeout=120).run()


def traces(at):
    return [t for c in at.get("plotly_chart") for t in json.loads(c.proto.spec)["data"]]


def test_default_view_is_the_unchanged_2d_map(app_env):
    at = run_app()
    assert not at.exception
    radio = [r for r in at.radio if r.label == "Map view"][0]
    assert radio.options == ["2D map", "3D map"] and radio.value == "2D map"
    assert not at.get("deck_gl_json_chart")
    choro = [t for t in traces(at) if t["type"] == "choroplethmap"]
    assert len(choro) == 1
    d = frame()
    assert choro[0]["zmin"] == 0 and choro[0]["zmax"] == colour_scale("nightlife_score", d)[1]
    loc = choro[0]["locations"]
    if isinstance(loc, dict):                                               # plotly packs numpy arrays as base64
        import base64
        loc = np.frombuffer(base64.b64decode(loc["bdata"]), dtype=loc["dtype"]).tolist()
    assert sorted(loc) == [1, 2, 3, 4]                                      # airport (5) still excluded


def test_3d_view_uses_same_rows_filters_and_hides_the_2d_map(app_env):
    at = run_app()
    [r for r in at.radio if r.label == "Map view"][0].set_value("3D map"); at.run()
    assert not at.exception
    assert not [t for t in traces(at) if t["type"] == "choroplethmap"]
    spec = json.loads(at.get("deck_gl_json_chart")[0].proto.json)
    zones = [l for l in spec["layers"] if l["id"] == "zones"][0]["data"]["features"]
    assert sorted(f["properties"]["LocationID"] for f in zones) == [1, 2, 3, 4]
    # borough filter applies
    [m for m in at.multiselect if m.label == "Boroughs"][0].set_value(["Manhattan"]); at.run()
    spec = json.loads(at.get("deck_gl_json_chart")[0].proto.json)
    zones = [l for l in spec["layers"] if l["id"] == "zones"][0]["data"]["features"]
    assert [f["properties"]["LocationID"] for f in zones] == [1]
    # night type and metric
    sb = {s.label: s for s in at.selectbox}
    sb["Night type"].select("weekday"); sb["Colour by"].select("Late departures per night"); at.run()
    assert not at.exception and at.get("deck_gl_json_chart")
    # the hourly chart and the table are still there
    assert any(m.value == "**Top 10 zones for the selected measure**" for m in at.markdown)


def test_missing_geometry_keeps_the_message_in_both_views(app_env, monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "nowhere"))
    at = run_app()
    [r for r in at.radio if r.label == "Map view"][0].set_value("3D map"); at.run()
    assert not at.exception and any("Missing zone geometry" in i.value for i in at.info)


# ---------------------------------------------------------------- packaging and readability CSS
ROOT = Path(__file__).resolve().parents[1]


def test_image_ships_the_new_module_and_pins_pydeck():
    assert "nightlife_3d.py" in (ROOT / "app" / "Dockerfile").read_text()
    assert "pydeck==0.9.3" in (ROOT / "app" / "requirements.txt").read_text().splitlines()


def test_kpi_values_are_never_ellipsised():
    import theme
    import streamlit as st
    captured = []
    orig = st.markdown
    try:
        st.markdown = lambda body, **k: captured.append(body)
        theme.apply()
    finally:
        st.markdown = orig
    css = "".join(captured)
    assert 'stMetricValue"] p' in css and "text-overflow:clip" in css and "container-type:inline-size" in css
    assert "card-findings" in css and "card-quality-summary" in css
