"""Streamlit dashboard. It only reads the CSV/JSON files in /results (never a database).
Every section tolerates missing, empty or half-written files.

The visual layer (colours, CSS, chart theme, sidebar) lives in theme.py and .streamlit/config.toml.
The data, calculations, queries and chart meaning are unchanged."""
import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import theme
from results_io import (ratio_sentences, read_csv_safe, read_json_safe, size_labels, timings_status)
from nightlife_map import render_nightlife_map

RESULTS = Path(os.environ.get("RESULTS_DIR", "/results"))
NIGHT_START = int(os.environ.get("NIGHT_START", "18"))
NIGHT_END = int(os.environ.get("NIGHT_END", "6"))
LABEL = {"postgres": "PostgreSQL", "postgres_indexed": "PostgreSQL + index on pickup_ts", "duckdb": "DuckDB"}

st.set_page_config(page_title="Night taxis: PostgreSQL vs DuckDB", layout="wide")
theme.apply()


def need(df, problem):
    """Show the problem instead of the section when data is not usable."""
    if df is None:
        st.info(problem)
        return False
    return True


def card(name: str, parent=st):
    """White rounded card around one section. The key gives the container a stable CSS class."""
    return parent.container(border=True, key=f"card-{name}")


def sidebar_info() -> None:
    """Brand + facts in the sidebar. Only reads values that the result files already contain."""
    meta, _ = read_json_safe(RESULTS, "city/meta.json")
    run, _ = read_json_safe(RESULTS, "run_status.json")
    facts = [("Night window", f"{NIGHT_START:02d}:00 to {(NIGHT_END - 1) % 24:02d}:59")]
    if meta:
        facts.insert(0, ("Cleaned night trips", f"{int(meta['rows']):,}"))
        facts.insert(1, ("Months", str(meta["months"])))
    status = None
    if run:
        state = str(run.get("state", "unknown"))
        state = state if state in ("complete", "running", "failed") else "unknown"
        status = (state, {"complete": "Complete", "running": "Running", "failed": "Failed",
                          "unknown": "Unknown"}[state])
    theme.sidebar(facts, status, "Data: NYC TLC Yellow Taxi, New York local time. Subset of the original files.")


sidebar_info()

st.title("NYC yellow taxi nights: PostgreSQL vs DuckDB")
last_hour = (NIGHT_END - 1) % 24
st.caption(f"Dataset used by both databases: CLEANED NIGHT TRIPS ONLY (pickup {NIGHT_START:02d}:00 to "
           f"{last_hour:02d}:59, New York local time). It is a subset of the original TLC files, not the "
           f"complete data. Row counts and excluded rows are in the 'Data quality' tab.")
tab_city, tab_data, tab_db = st.tabs(["The night in the city", "Data quality", "PostgreSQL vs DuckDB"])

# ------------------------------------------------------------ city
with tab_city:
    hourly, p1 = read_csv_safe(RESULTS, "city/hourly.csv")
    zones, _ = read_csv_safe(RESULTS, "city/top_zones.csv")
    boroughs, _ = read_csv_safe(RESULTS, "city/boroughs.csv")
    meta, _ = read_json_safe(RESULTS, "city/meta.json")
    if need(hourly, p1):
        hourly = hourly.copy()
        hourly["time"] = hourly["hour"].map(lambda h: f"{int(h):02d}:00")
        order = list(hourly["time"])
        rows_total = int(hourly["trips"].sum())
        note = f" (first {meta['months']} month(s))" if meta else ""
        st.metric("Cleaned night trips analysed" + note, f"{rows_total:,}")
        c1, c2 = st.columns(2)
        b1, b2 = card("hours", c1), card("fare-tip", c2)
        b1.subheader("Trips by pickup hour")
        b1.altair_chart(alt.Chart(hourly).mark_bar(fill=theme.VERTICAL_GRADIENT, **theme.BAR_V).encode(
            x=alt.X("time:N", sort=order, title="Pickup hour", axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("trips:Q", title="Trips"),
            tooltip=["time", "trips"]), width="stretch", theme=None)
        b2.subheader("Average fare and tip by pickup hour")
        long = hourly.melt(id_vars=["time"], value_vars=["avg_fare", "avg_tip"], var_name="measure",
                           value_name="dollars")
        b2.altair_chart(alt.Chart(long).mark_line(point=True).encode(
            x=alt.X("time:N", sort=order, title="Pickup hour", axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("dollars:Q", title="USD"),
            color=alt.Color("measure:N", scale=theme.scale(long["measure"])),
            tooltip=["time", "measure", "dollars"]), width="stretch", theme=None)
        c3, c4 = st.columns(2)
        if zones is not None:
            z = zones.copy()
            z["label"] = z["zone"].fillna("(no zone name)") + " (" + z["borough"].fillna("?") + ")"
            b3 = card("zones", c3)
            b3.subheader("Top 10 pickup zones")
            b3.altair_chart(alt.Chart(z).mark_bar(**theme.BAR_H).encode(
                y=alt.Y("label:N", sort="-x", title=None, axis=theme.label_axis()),
                x=alt.X("trips:Q", title="Trips"),
                tooltip=["label", "trips"]), width="stretch", theme=None)
        if boroughs is not None:
            b4 = card("boroughs", c4)
            b4.subheader("Trips by borough")
            b4.altair_chart(alt.Chart(boroughs).mark_bar(**theme.BAR_H).encode(
                y=alt.Y("borough:N", sort="-x", title=None, axis=theme.label_axis()),
                x=alt.X("trips:Q", title="Trips"),
                tooltip=["borough", "trips"]), width="stretch", theme=None)

with tab_city:
    st.divider()
    render_nightlife_map(RESULTS)

# ------------------------------------------------------------ data quality
with tab_data:
    summ, p = read_csv_safe(RESULTS, "data_quality/preprocess_summary.csv")
    excl, p2 = read_csv_safe(RESULTS, "data_quality/exclusions.csv")
    val, p3 = read_csv_safe(RESULTS, "data_quality/validation.csv")
    with card("quality-summary"):
        if need(summ, p):
            st.subheader("Source rows, kept rows and excluded rows per month")
            st.dataframe(summ, width="stretch", hide_index=True)
            if (summ["unaccounted_rows"] != 0).any():
                st.error("Some rows are unaccounted for: processed + excluded does not equal source.")
            else:
                st.success("For every month: kept + excluded = source rows.")
    with card("quality-exclusions"):
        if need(excl, p2):
            st.subheader("Excluded rows by reason and source month")
            st.caption("Reasons are checked in this order and never overlap: a row is counted under the "
                       "first rule that applies. See docs/EXCLUSION_RULES.md.")
            piv = excl.pivot_table(index="reason", columns="source_month", values="rows", aggfunc="sum")
            piv["total"] = piv.sum(axis=1)
            st.dataframe(piv, width="stretch")
    with card("quality-validation"):
        if need(val, p3):
            st.subheader("Validation checks")
            bad = val[val["status"] != "PASS"]
            (st.error if len(bad) else st.success)(
                f"{len(val) - len(bad)} of {len(val)} checks passed" + ("" if not len(bad) else ": see the table"))
            st.dataframe(val if len(bad) == 0 else bad, width="stretch", hide_index=True)

# ------------------------------------------------------------ engines
with tab_db:
    ok, message = timings_status(RESULTS)
    (st.success if ok else st.warning)(message)
    datasets, _ = read_csv_safe(RESULTS, "datasets.csv")
    summary, ps = read_csv_safe(RESULTS, "benchmark_summary.csv")
    if ok and need(summary, ps):
        labels = size_labels(datasets, summary["months"].unique())
        summary = summary.copy()
        summary["engine_name"] = summary["engine"].map(LABEL)
        summary["size"] = summary["months"].map(labels)
        size_order = [labels[m] for m in sorted(labels)]
        env, _ = read_json_safe(RESULTS, "run_status.json")
        if env:
            st.caption(f"{env.get('runs')} measured runs per query after {env.get('warmups')} warm-up runs; "
                       f"query order: {env.get('order')} (seed {env.get('seed')}). 'First' = first execution on a new "
                       f"connection (OS cache not flushed, so not a true cold start); 'warm' = after warm-up.")
        with card("queries"):
            st.subheader("Analytical queries and the selective range query")
            phase = st.radio("Measurement", ["warm", "first"], horizontal=True,
                             format_func=lambda x: {"warm": "Warm (median, bars show min-max)",
                                                    "first": "First execution (single run)"}[x])
            q = st.selectbox("Query", ["Q1", "Q2", "Q3", "Q5"])
            sub = summary[(summary["query"] == q) & (summary["phase"] == phase)
                          & summary["engine"].isin(["postgres", "duckdb"])]
            base = alt.Chart(sub).encode(
                x=alt.X("size:N", sort=size_order, title="Test size (actual rows)", axis=theme.size_axis()),
                xOffset="engine_name:N",
                color=alt.Color("engine_name:N", title="Engine", scale=theme.scale(sub["engine_name"])))
            chart = base.mark_bar(**theme.BAR_V).encode(
                y=alt.Y("median_s:Q", title="Seconds"),
                tooltip=["engine_name", "size", "n", "median_s", "mean_s", "stdev_s"])
            if phase == "warm":
                chart += base.mark_rule(strokeWidth=1.6).encode(y="min_s:Q", y2="max_s:Q", color=alt.value(theme.INK))
            st.altair_chart(chart, width="stretch", theme=None)
            cols = ["engine_name", "size", "phase", "n", "median_s", "mean_s", "stdev_s", "min_s", "max_s"]
            st.dataframe(summary[(summary["query"] == q) & summary["engine"].isin(["postgres", "duckdb"])][cols]
                         .sort_values(["size", "engine_name", "phase"]), width="stretch", hide_index=True)
            for s in ratio_sentences(summary[summary["query"].isin(["Q1", "Q2", "Q3", "Q5"])], labels):
                st.write("- " + s)
            st.caption("Ratios describe THIS run on THIS machine. PostgreSQL is reached through a client/server "
                       "connection and DuckDB runs inside the benchmark process, so part of any difference is "
                       "architecture, not only storage layout.")

        with card("q4"):
            st.subheader("Q4: 200 sequential point lookups (application-level latency)")
            lat, pl = read_csv_safe(RESULTS, "lookup_latency.csv")
            st.caption("This is the latency a Python client sees for 200 one-after-the-other lookups by trip_id. "
                       "It includes Python call overhead and, for PostgreSQL, a network round trip per lookup. "
                       "It is not a measure of pure engine processing time.")
            if need(lat, pl):
                l2 = lat[lat["phase"] == "warm"].copy()
                l2["engine_name"] = l2["engine"].map(LABEL)
                l2["size"] = l2["months"].map(labels)
                st.dataframe(l2[["engine_name", "size", "lookups", "mean_ms", "median_ms", "p95_ms", "p99_ms",
                                 "max_ms"]], width="stretch", hide_index=True)

        with card("index"):
            st.subheader("PostgreSQL index experiment (Q5, index on pickup_ts)")
            ex, pe = read_csv_safe(RESULTS, "explain_q5.csv")
            if need(ex, pe):
                ex = ex.copy()
                ex["size"] = ex["months"].map(labels)
                st.dataframe(ex[["size", "state", "plan_nodes", "index_used", "execution_ms"]],
                             width="stretch", hide_index=True)
                with_idx = ex[ex["state"] == "with_index"]
                if (~with_idx["index_used"].astype(str).str.lower().eq("true")).any():
                    st.warning("In at least one size PostgreSQL did NOT use the index (see plan_nodes): "
                               "those timings must not be described as an index effect.")
                ib, _ = read_csv_safe(RESULTS, "index_build.csv")
                if ib is not None:
                    st.caption("Index build time (seconds): " + ", ".join(
                        f"{labels.get(int(r.months), r.months)}: {r.seconds:.2f}" for r in ib.itertuples()))
                pi = summary[(summary["query"] == "Q5") & (summary["phase"] == "warm")
                             & summary["engine"].isin(["postgres", "postgres_indexed"])]
                if not pi.empty:
                    st.dataframe(pi[["engine_name", "size", "n", "median_s", "mean_s", "stdev_s"]],
                                 width="stretch", hide_index=True)

    with card("load"):
        st.subheader("Loading time (defined identically for both engines, see docs/METHODOLOGY.md)")
        loads, pl2 = read_csv_safe(RESULTS, "load_times.csv")
        if need(loads, pl2):
            lbl = size_labels(datasets, loads["months"].unique())
            loads = loads.copy()
            loads["engine_name"] = loads["engine"].map(LABEL)
            loads["size"] = loads["months"].map(lbl)
            st.altair_chart(alt.Chart(loads).mark_bar(**theme.BAR_V).encode(
                x=alt.X("size:N", sort=[lbl[m] for m in sorted(lbl)], title="Test size (actual rows)",
                        axis=theme.size_axis()),
                y=alt.Y("seconds:Q", title="Load time (s), single measurement"),
                color=alt.Color("engine_name:N", scale=theme.scale(loads["engine_name"])),
                xOffset="engine_name:N", tooltip=["engine_name", "size", "rows", "seconds"]),
                width="stretch", theme=None)

    with card("storage"):
        st.subheader("Storage")
        disk, pd_ = read_csv_safe(RESULTS, "disk_sizes.csv")
        if need(disk, pd_):
            d = disk[disk["what"].isin(["postgres", "postgres_indexed", "duckdb", "parquet_clean", "parquet_raw"])].copy()
            d["MB"] = d["bytes"] / 1024**2
            lbl = size_labels(datasets, d["months"].unique())
            d["size"] = d["months"].map(lbl)
            d["what"] = d["what"].replace({"postgres": "PostgreSQL (tables+PK)", "postgres_indexed": "PostgreSQL + index",
                                           "duckdb": "DuckDB file", "parquet_clean": "Parquet, cleaned night trips",
                                           "parquet_raw": "Parquet, original files (all trips)"})
            st.caption("The original files hold ALL trips, the other rows hold only the cleaned night trips: "
                       "compare databases with each other and with the cleaned Parquet, not with the original files.")
            st.altair_chart(alt.Chart(d).mark_bar(**theme.BAR_V).encode(
                x=alt.X("size:N", sort=[lbl[m] for m in sorted(lbl)], title="Test size (actual rows)",
                        axis=theme.size_axis()),
                y=alt.Y("MB:Q", title="MB"),
                color=alt.Color("what:N", title="", scale=theme.scale(d["what"])), xOffset="what:N",
                tooltip=["what", "size", "MB"]), width="stretch", theme=None)

    with card("memory"):
        st.subheader("Peak memory (whole container, from docker stats)")
        mem, pm = read_csv_safe(RESULTS, "memory_summary.csv")
        if need(mem, pm):
            st.dataframe(mem, width="stretch", hide_index=True)
        else:
            st.caption("Optional: run scripts/sample_docker_stats.py during the benchmark, then scripts/summarize_memory.py.")

    with card("consistency"):
        st.subheader("Database consistency checks")
        cons, pc_ = read_csv_safe(RESULTS, "db_consistency.csv")
        if need(cons, pc_):
            st.dataframe(cons, width="stretch", hide_index=True)
