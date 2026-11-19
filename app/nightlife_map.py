"""Nightlife geographic map: read Benedetta's CSVs, not PostgreSQL."""
from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

HOURS = [18, 19, 20, 21, 22, 23, 0, 1, 2, 3, 4, 5]
METRICS = {
    "Nightlife score (late departures / late arrivals)": "nightlife_score",
    "Late departures per night": "avg_late_departures",
    "Evening arrivals per night": "avg_evening_arrivals",
    "Late arrivals per night": "avg_late_arrivals",
}
REQUIRED = {
    "zone_id", "zone", "borough", "night_type", "nights",
    "avg_evening_arrivals", "avg_late_departures",
    "avg_late_arrivals", "nightlife_score", "enough_trips",
    "excluded_reason",
}


def hour_order(hour: int) -> int:
    """18:00 -> 0, 23:00 -> 5, 00:00 -> 6, 05:00 -> 11."""
    return (int(hour) - 18) % 24


def filter_map_rows(data: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Do not rank airports, unknown zones or unreliable ratios."""
    result = data[~data["excluded_reason"].isin(
        ["airport", "unknown zone"]
    )].copy()
    if metric == "nightlife_score":
        result = result[
            result["enough_trips"] &
            result["excluded_reason"].isna() &
            (result[metric] > 0)
        ]
    return result[result[metric].notna()]


@st.cache_data(ttl=60)
def read_results(metrics_file: str, hourly_file: str):
    metrics = pd.read_csv(metrics_file)
    hourly = pd.read_csv(hourly_file)
    if not REQUIRED.issubset(metrics.columns):
        raise ValueError("zone_metrics.csv: expected columns missing")
    needed = {"zone_id", "night_type", "hour", "pickups", "dropoffs"}
    if not needed.issubset(hourly.columns):
        raise ValueError("hourly.csv: expected columns missing")
    return metrics, hourly


@st.cache_data(show_spinner=False)
def read_shapes(zip_name: str):
    """Convert official TLC taxi-zone geometry to WGS84 GeoJSON."""
    with tempfile.TemporaryDirectory() as temp:
        with zipfile.ZipFile(zip_name) as z:
            z.extractall(temp)
        shp = next(Path(temp).rglob("*.shp"), None)
        if shp is None:
            raise ValueError("taxi_zones.zip has no .shp file")
        geo = gpd.read_file(shp)
    geo = geo.to_crs(epsg=4326)
    geo["LocationID"] = geo["LocationID"].astype(int)
    geo = geo[["LocationID", "geometry"]].dissolve(
        by="LocationID"
    ).reset_index()
    return geo.__geo_interface__


def render_nightlife_map(results_dir):
    """Call from the existing first dashboard tab."""
    data_folder = Path(results_dir) / "nightlife"
    metrics_file = data_folder / "zone_metrics.csv"
    hourly_file = data_folder / "hourly.csv"
    if not metrics_file.exists() or not hourly_file.exists():
        st.info("Nightlife CSVs missing. Ask Benedetta for the results ZIP.")
        return
    try:
        metrics, hourly = read_results(str(metrics_file), str(hourly_file))
    except Exception as exc:
        st.warning(f"Nightlife data cannot be read: {exc}")
        return

    st.subheader("NYC After Dark: nightlife geography")
    st.caption(
        "Yellow-taxi mobility proxy, not a direct measure of nightlife. "
        "Night: 18:00-05:59; late: 00:00-04:59."
    )
    c1, c2 = st.columns(2)
    night = c1.selectbox("Night type", ["weekend", "weekday", "all"])
    metric_label = c2.selectbox("Colour by", list(METRICS))
    metric = METRICS[metric_label]
    base = metrics[metrics["night_type"] == night].copy()
    boroughs = sorted(
        b for b in base["borough"].dropna().unique()
        if b not in ["EWR", "Unknown"]
    )
    chosen = st.multiselect("Boroughs", boroughs, default=boroughs)
    base = base[base["borough"].isin(chosen)]
    data = filter_map_rows(base, metric)
    if data.empty:
        st.info("No zones satisfy the selected filters.")
        return

    st.metric("Nights covered", int(data["nights"].max()))
    st.caption("For ratio rankings, low-volume zones and airports are excluded.")

    shapes_path = Path(os.getenv("DATA_DIR", "data/raw")) / "taxi_zones.zip"
    if shapes_path.exists():
        try:
            shapes = read_shapes(str(shapes_path))
            fig = go.Figure(go.Choroplethmap(
                geojson=shapes,
                featureidkey="properties.LocationID",
                locations=data["zone_id"].astype(int),
                z=data[metric],
                customdata=data[[
                    "zone", "borough", "avg_evening_arrivals",
                    "avg_late_departures", "avg_late_arrivals"
                ]].to_numpy(),
                colorscale="RdBu_r" if metric == "nightlife_score"
                else "Viridis",
                zmid=1 if metric == "nightlife_score" else None,
                zmin=0,
                zmax=(max(2, float(data[metric].quantile(.95)))
                      if metric == "nightlife_score"
                      else max(1, float(data[metric].quantile(.95)))),
                marker_line_width=0.3,
                hovertemplate=(
                    "<b>%{customdata[0]}</b> (%{customdata[1]})<br>"
                    "Selected value: %{z:.2f}<br>"
                    "Evening arrivals/night: %{customdata[2]:.1f}<br>"
                    "Late departures/night: %{customdata[3]:.1f}<br>"
                    "Late arrivals/night: %{customdata[4]:.1f}"
                    "<extra></extra>"
                ),
            ))
            fig.update_layout(
                map_style="carto-positron",
                map_center={"lat": 40.73, "lon": -73.94},
                map_zoom=9.5, height=590,
                margin=dict(l=0, r=0, t=0, b=0),
            )
            st.plotly_chart(fig, width="stretch")
        except Exception as exc:
            st.warning(f"Map geometry failed, but tables are available: {exc}")
    else:
        st.info(f"Missing zone geometry: {shapes_path}")

    st.markdown("**Top 10 zones for the selected measure**")
    st.dataframe(
        data.nlargest(10, metric)[
            ["zone", "borough", metric]
        ].round(2),
        hide_index=True, width="stretch",
    )

    st.markdown("**Pickup and drop-off patterns by hour**")
    names = data.set_index("zone_id")["zone"]
    picks = st.multiselect(
        "Select up to three zones",
        data.sort_values(metric, ascending=False)["zone_id"].tolist(),
        default=data.sort_values(metric, ascending=False)["zone_id"].head(2).tolist(),
        format_func=lambda zid: str(names.get(zid, zid)),
        max_selections=3,
    )
    h = hourly[
        (hourly["night_type"] == night) &
        (hourly["zone_id"].isin(picks))
    ].copy()
    if h.empty:
        st.info("Select a zone to see the hourly comparison.")
        return
    nights = int(data["nights"].max())
    h["zone"] = h["zone_id"].map(names)
    h["hour_order"] = h["hour"].map(hour_order)
    h["hour_label"] = h["hour"].map(lambda v: f"{v:02d}:00")
    long = h.melt(
        id_vars=["zone", "hour_label", "hour_order"],
        value_vars=["pickups", "dropoffs"],
        var_name="event", value_name="count",
    )
    long["per_night"] = long["count"] / nights
    long = long.sort_values("hour_order")
    chart = px.line(
        long, x="hour_label", y="per_night", color="zone",
        line_dash="event", markers=True,
        category_orders={
            "hour_label": [f"{v:02d}:00" for v in HOURS]
        },
        labels={"hour_label": "Hour", "per_night": "Events per night"},
    )
    st.plotly_chart(chart, width="stretch")
