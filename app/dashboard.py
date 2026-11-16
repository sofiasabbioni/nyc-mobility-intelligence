"""NYC After Dark - interactive map of nightlife zones (Streamlit).

What it shows
    One colour per NYC taxi zone, computed from the table night_zone_metrics
    (built by analysis_data_postgres.py). The user picks the night type
    (weekday / weekend / all nights), the metric and the boroughs.

Where the data comes from
    - Numbers: PostgreSQL, table night_zone_metrics (service "db" in docker-compose).
    - Zone shapes: the official TLC file taxi_zones.zip, already downloaded by
      download_data.py into /data (shared volume with the loader).

Run it
    docker compose up           -> open http://localhost:8501
"""
import glob
import os
import tempfile
import zipfile

import geopandas as gpd
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import URL, create_engine

DATA_DIR = os.environ.get("DATA_DIR", "data")
ZONES_ZIP = os.path.join(DATA_DIR, "taxi_zones.zip")
METRICS_TABLE = "night_zone_metrics"
HOUR_TABLE = "night_zone_hour"

# label shown to the user -> (column, kind). "ratio" metrics are coloured on a log scale around 1.
METRICS = {
    "Nightlife score (late departures / late arrivals)": ("nightlife_score", "ratio"),
    "Weekend boost (weekend / weekday late departures)": ("weekend_boost", "ratio"),
    "Late departures per night (pickups 00:00-04:59)": ("avg_late_departures", "count"),
    "Evening arrivals per night (drop-offs 20:00-23:59)": ("avg_evening_arrivals", "count"),
    "Late arrivals per night (drop-offs 00:00-04:59)": ("avg_late_arrivals", "count"),
}
NIGHT_TYPES = {"Weekend nights (Fri, Sat)": "weekend", "Weekday nights (Sun-Thu)": "weekday", "All nights": "all"}

st.set_page_config(page_title="NYC After Dark", layout="wide")


# ---------------------------------------------------------------- data access

@st.cache_resource
def get_engine():
    """One connection pool for the whole app. The host is the Compose service name 'db'."""
    url = URL.create(
        "postgresql+psycopg",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        database=os.environ["POSTGRES_DB"],
    )
    return create_engine(url)


@st.cache_data(ttl=600)
def load_metrics():
    """The precomputed table: one row per zone and night type (about 265 x 3 rows, so it is instant)."""
    query = f"""
        SELECT zone_id, zone, borough, night_type, nights,
               evening_arrivals, late_departures, late_arrivals,
               avg_evening_arrivals, avg_late_departures, avg_late_arrivals,
               nightlife_score, enough_trips, excluded_reason
        FROM {METRICS_TABLE}
    """
    df = pd.read_sql(query, get_engine())
    numeric = ["avg_evening_arrivals", "avg_late_departures", "avg_late_arrivals", "nightlife_score"]
    df[numeric] = df[numeric].astype(float)       # PostgreSQL NUMERIC arrives as Decimal
    return df


@st.cache_data(ttl=600)
def load_hourly():
    """Total drop-offs and pickups per zone, night type and hour (20:00 ... 05:00), summed over all nights."""
    query = f"""
        SELECT zone_id, night_type, hour, hour_order,
               sum(dropoffs) AS dropoffs, sum(pickups) AS pickups
        FROM {HOUR_TABLE}
        GROUP BY zone_id, night_type, hour, hour_order
    """
    return pd.read_sql(query, get_engine())


@st.cache_data
def load_zone_shapes():
    """Official TLC zone polygons as GeoJSON in latitude/longitude (the file is in feet, EPSG:2263)."""
    if not os.path.exists(ZONES_ZIP):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(ZONES_ZIP) as z:
            z.extractall(tmp)
        shp = glob.glob(os.path.join(tmp, "**", "*.shp"), recursive=True)[0]
        gdf = gpd.read_file(shp)
    gdf = gdf.to_crs(epsg=4326)
    # A few zones are split into several polygons with the same LocationID: merge them into one shape.
    gdf = gdf[["LocationID", "geometry"]].dissolve(by="LocationID").reset_index()
    gdf["LocationID"] = gdf["LocationID"].astype(int)
    gdf["geometry"] = gdf.geometry.simplify(0.0001)   # lighter map, same look at city scale
    return gdf.__geo_interface__


def add_weekend_boost(df):
    """Weekend / weekday late departures per night, for every zone (needs both night types)."""
    wide = df.pivot(index="zone_id", columns="night_type", values="avg_late_departures")
    enough = df.pivot(index="zone_id", columns="night_type", values="enough_trips")
    boost = (wide["weekend"] / wide["weekday"].replace(0, np.nan)).where(enough["weekend"] & enough["weekday"])
    return df.merge(boost.rename("weekend_boost").reset_index(), on="zone_id", how="left")


# ---------------------------------------------------------------- page

st.title("NYC After Dark - where does New York go out at night?")
st.caption(
    "Yellow-taxi trips, NYC TLC data. A zone where many people are picked up late at night "
    "(00:00-04:59) compared with how many are dropped off there at that time is a going-out zone; "
    "a zone where people mostly arrive late and stay is residential."
)

try:
    metrics = add_weekend_boost(load_metrics())
except Exception as error:   # the loader has not finished yet, or the database is down
    st.error(f"Cannot read {METRICS_TABLE} from PostgreSQL. Has the loader finished? ({error})")
    st.stop()

shapes = load_zone_shapes()
if shapes is None:
    st.error(f"Zone shapes not found at {ZONES_ZIP}. Run the loader first (it downloads taxi_zones.zip).")
    st.stop()

# --- controls (left sidebar)
with st.sidebar:
    st.header("Map settings")
    metric_label = st.radio("Colour the zones by", list(METRICS))
    column, kind = METRICS[metric_label]
    if column == "weekend_boost":
        night_label = "All nights"
        st.caption("The weekend boost already compares the two night types.")
    else:
        night_label = st.radio("Nights", list(NIGHT_TYPES))
    # EWR (Newark airport) and "Unknown" are not real NYC boroughs: keep them out of the filter
    boroughs = sorted(b for b in metrics["borough"].dropna().unique() if b not in ("EWR", "Unknown"))
    chosen_boroughs = st.multiselect("Boroughs", boroughs, default=boroughs)
    hide_airports = st.checkbox("Hide airports (JFK, LaGuardia, Newark)", value=True)

# --- the rows to colour
night_type = NIGHT_TYPES[night_label]
data = metrics[(metrics["night_type"] == night_type) & metrics["borough"].isin(chosen_boroughs)].copy()
data = data[data["excluded_reason"].ne("unknown zone")]
if hide_airports:
    data = data[data["excluded_reason"].ne("airport")]
if kind == "ratio":
    # a ratio built on a handful of trips is noise: those zones stay grey
    data = data[data["enough_trips"] & data[column].notna() & (data[column] > 0)]
data = data[data[column].notna()]

# --- colour scale
if kind == "ratio":
    # Ratios are multiplicative (2 = twice as many, 0.5 = half), so colour log2(value):
    # 1 is the white centre, nightlife zones red, residential zones blue.
    z = np.log2(data[column])
    ticks = [0.125, 0.25, 0.5, 1, 2, 4, 8]
    limit = 2.0   # colour scale from 0.25 to 4: beyond that the colour stays at its darkest
    colour = dict(z=z, colorscale="RdBu_r", zmid=0, zmin=-limit, zmax=limit,
                  colorbar=dict(title="ratio", tickvals=np.log2(ticks), ticktext=[str(t) for t in ticks]))
else:
    # Counts: Midtown is so much busier than the rest that it would make every other zone look the same.
    # Cap the scale at the 95th percentile; the hover still shows the real number.
    z = data[column]
    colour = dict(z=z, colorscale="Viridis", zmin=0,
                  zmax=float(np.nanpercentile(z, 95)) if len(z) else 1,
                  colorbar=dict(title="trips / night"))

hover = (
    "<b>%{customdata[0]}</b> (%{customdata[1]})<br>"
    + metric_label.split(" (")[0] + ": <b>%{customdata[2]:.2f}</b><br>"
    "evening arrivals / night: %{customdata[3]:.1f}<br>"
    "late departures / night: %{customdata[4]:.1f}<br>"
    "late arrivals / night: %{customdata[5]:.1f}<extra></extra>"
)

fig = go.Figure()
# Layer 1: every zone in light grey, so zones without a value are still visible on the map.
fig.add_trace(go.Choroplethmap(
    geojson=shapes, featureidkey="properties.LocationID",
    locations=[f["properties"]["LocationID"] for f in shapes["features"]],
    z=[0] * len(shapes["features"]), colorscale=[[0, "#d9d9d9"], [1, "#d9d9d9"]],
    showscale=False, marker_line_width=0.3, marker_line_color="white", hoverinfo="skip",
))
# Layer 2: the zones that have a value, coloured.
fig.add_trace(go.Choroplethmap(
    geojson=shapes, featureidkey="properties.LocationID", locations=data["zone_id"],
    customdata=data[["zone", "borough", column, "avg_evening_arrivals",
                     "avg_late_departures", "avg_late_arrivals"]],
    hovertemplate=hover, marker_line_width=0.3, marker_line_color="white", marker_opacity=0.85,
    **colour,
))
fig.update_layout(map_style="carto-positron", map_center={"lat": 40.73, "lon": -73.94}, map_zoom=9.6,
                  height=650, margin=dict(l=0, r=0, t=0, b=0))

left, right = st.columns([3, 1])
with left:
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Grey = no value: too few trips for a reliable ratio (< "
               f"{os.environ.get('MIN_TRIPS', 100)} late pickups or drop-offs), airport, or borough filtered out. "
               "Taxi zones are official TLC areas, not exact GPS points.")
with right:
    nights = int(metrics.loc[metrics["night_type"] == night_type, "nights"].max())
    st.metric("Nights in the data", nights)
    st.metric("Zones coloured", len(data))
    st.subheader("Top 10")
    top = data.sort_values(column, ascending=False).head(10)
    st.dataframe(top[["zone", "borough", column]].rename(columns={column: "value"}).round(2),
                 hide_index=True, use_container_width=True)

# ---------------------------------------------------------------- hour by hour

st.subheader("Hour by hour: when do people arrive and leave?")
st.caption("Average taxi trips per night in each hour. Solid line = pickups (people leaving the zone), "
           "dashed line = drop-offs (people arriving). In a going-out zone arrivals peak in the evening "
           "and departures late at night; in a residential zone people arrive late and almost nobody leaves.")

try:
    hourly = load_hourly()
except Exception as error:
    st.warning(f"Cannot read {HOUR_TABLE}: {error}")
    hourly = None

if hourly is not None and not hourly.empty:
    # zone_id -> zone name. Two TLC zones (264, 265) have no name: drop them, or sorting the names fails.
    names = metrics[metrics["night_type"] == "all"].set_index("zone_id")["zone"].dropna()
    ranked = metrics[(metrics["night_type"] == "weekend") & metrics["enough_trips"]
                     & metrics["excluded_reason"].isna()].sort_values("nightlife_score")
    # default comparison: the most "going-out" zone and the most residential one (weekend nights).
    # For the residential one, look only at busy zones (above-median late arrivals): otherwise a park
    # with a handful of trips wins the "lowest score" and the comparison says nothing.
    busy = ranked[ranked["late_arrivals"] >= ranked["late_arrivals"].median()]
    defaults = [ranked["zone"].iloc[-1], busy["zone"].iloc[0]] if len(busy) >= 1 else []
    chosen_zones = st.multiselect("Zones to compare", sorted(names.unique()), default=defaults,
                                  max_selections=4)

    hours = hourly.copy()
    if night_type == "all":
        hours = hours.groupby(["zone_id", "hour", "hour_order"], as_index=False)[["dropoffs", "pickups"]].sum()
    else:
        hours = hours[hours["night_type"] == night_type]
    hours["zone"] = hours["zone_id"].map(names)
    hours = hours[hours["zone"].isin(chosen_zones)]

    # divide by the number of nights of that type, so weekday and weekend are comparable
    hours["pickups"] = hours["pickups"] / nights
    hours["dropoffs"] = hours["dropoffs"] / nights
    long = hours.melt(id_vars=["zone", "hour", "hour_order"], value_vars=["pickups", "dropoffs"],
                      var_name="trip", value_name="trips_per_night")
    long["trip"] = long["trip"].map({"pickups": "pickups (leaving)", "dropoffs": "drop-offs (arriving)"})
    long["hour_label"] = long["hour"].map(lambda h: f"{h:02d}:00")
    long = long.sort_values("hour_order")

    if long.empty:
        st.info("Choose at least one zone.")
    else:
        line = px.line(long, x="hour_label", y="trips_per_night", color="zone", line_dash="trip",
                       markers=True, color_discrete_sequence=["#d62728", "#1f77b4", "#2ca02c", "#ff7f0e"],
                       labels={"hour_label": "hour of the night",
                                             "trips_per_night": "trips per night", "trip": ""})
        line.update_xaxes(categoryorder="array",
                          categoryarray=[f"{(20 + i) % 24:02d}:00" for i in range(10)])
        line.update_layout(height=420, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(line, use_container_width=True)


with st.expander("Bottom 10 (the most residential / quietest zones)"):
    bottom = data.sort_values(column).head(10)
    st.dataframe(bottom[["zone", "borough", column]].rename(columns={column: "value"}).round(2),
                 hide_index=True, use_container_width=True)

with st.expander("How the numbers are defined"):
    st.markdown(
        "- **Evening arrivals (E)**: taxi drop-offs in the zone between 20:00 and 23:59.\n"
        "- **Late departures (L)**: taxi pickups in the zone between 00:00 and 04:59.\n"
        "- **Late arrivals (H)**: taxi drop-offs in the zone between 00:00 and 04:59.\n"
        "- **Nightlife score = L / H**: above 1, people leave the zone late at night more than they "
        "arrive (going-out zone); below 1, they arrive and stay (residential zone).\n"
        "- **Weekend boost**: late departures per weekend night / per weekday night.\n"
        "- A night is named after its evening: Friday night includes the early hours of Saturday.\n"
        "- These are recorded yellow-taxi trips, not all the people who move at night."
    )
