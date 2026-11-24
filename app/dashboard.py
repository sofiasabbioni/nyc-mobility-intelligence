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
from results_io import (key_findings, quality_kpis, ratio_sentences, read_csv_safe, read_json_safe, size_labels,
                        speedup_table, timings_status)
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
        # One measure at a time: on a shared axis the tip (a few dollars) was squashed under the fare.
        measure = b2.radio("Measure", ["avg_fare", "avg_tip"], horizontal=True, key="fare-tip-measure",
                           format_func=lambda m: {"avg_fare": "Average fare", "avg_tip": "Average tip"}[m])
        name = {"avg_fare": "Average fare (USD)", "avg_tip": "Average tip (USD)"}[measure]
        b2.altair_chart(alt.Chart(hourly).mark_line(point=True, color=theme.COLORS[measure]).encode(
            x=alt.X("time:N", sort=order, title="Pickup hour", axis=alt.Axis(labelAngle=-45)),
            y=alt.Y(f"{measure}:Q", title=name, scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("time:N", title="Pickup hour"),
                     alt.Tooltip(f"{measure}:Q", title=name, format="$.2f")]), width="stretch", theme=None)
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
            k = quality_kpis(summ)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Source rows (original files)", f"{k['source']:,}")
            m2.metric("Kept: cleaned night trips", f"{k['kept']:,}")
            m3.metric("Excluded rows", f"{k['excluded']:,}",
                      help="Includes every trip outside the night window: excluded does not mean invalid.")
            m4.metric("Unaccounted rows", f"{k['unaccounted']:,}")
            if k["balanced"]:
                st.caption(f"Totals over all months: {k['source']:,} source rows = {k['kept']:,} kept + "
                           f"{k['excluded']:,} excluded.")
            else:
                st.caption(f"Totals over all months: {k['source']:,} source rows, {k['kept']:,} kept, "
                           f"{k['excluded']:,} excluded, {k['unaccounted']:,} unaccounted.")
            st.caption("Excluded rows include the daytime trips, so 'excluded' is not a synonym for 'invalid'; "
                       "the reasons are in the table below.")
            st.dataframe(summ, width="stretch", hide_index=True)
            if k["balanced"]:
                st.success("For every month: kept + excluded = source rows, and no row is unaccounted for.")
            else:
                st.error("Row accounting does not balance for month(s) " + ", ".join(k["bad_months"]) +
                         ": kept + excluded differs from source rows, or some rows are unaccounted for. "
                         "Check the table above.")
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
        lat_all, _ = read_csv_safe(RESULTS, "lookup_latency.csv")
        ex_all, _ = read_csv_safe(RESULTS, "explain_q5.csv")
        findings = key_findings(summary, lat_all, ex_all)
        if findings:
            with card("findings"):
                st.subheader("Key findings of this run")
                for col, f in zip(st.columns(len(findings)), findings):
                    col.metric(f["title"], f["value"])
                    col.caption(f["detail"])
        st.caption(
            "Descriptive results of THIS run on THIS machine: largest test size, warm medians. "
            "The two systems differ in architecture (client/server vs in-process) and possibly in "
            "parallelism, so these timings alone do not establish why one engine is faster. "
            "The completed supplementary Q1-Q3 query-plan and parallelism study is documented "
            "in docs/RESULTS.md."
)
        with card("queries"):
            st.subheader("Analytical queries and the selective range query")
            phase = st.radio("Measurement", ["warm", "first"], horizontal=True,
                             format_func=lambda x: {"warm": "Warm (median, bars show min-max)",
                                                    "first": "First execution (single run)"}[x])
            q = st.selectbox("Query", ["Q1", "Q2", "Q3", "Q5"])
            log_y = st.toggle("Logarithmic time axis (points instead of bars, so small DuckDB times stay visible)",
                              value=False, key="log-timings")
            sub = summary[(summary["query"] == q) & (summary["phase"] == phase)
                          & summary["engine"].isin(["postgres", "duckdb"])]
            if log_y:   # a log axis needs strictly positive values
                sub = sub[(sub["median_s"] > 0) & (sub["min_s"] > 0)]
            tips = [alt.Tooltip("engine_name:N", title="Engine"), alt.Tooltip("size:N", title="Test size"),
                    alt.Tooltip("n:Q", title="Runs"), alt.Tooltip("median_s:Q", title="Median (s)", format=".4f"),
                    alt.Tooltip("mean_s:Q", title="Mean (s)", format=".4f"),
                    alt.Tooltip("stdev_s:Q", title="Std dev (s)", format=".4f")]
            base = alt.Chart(sub).encode(
                x=alt.X("size:N", sort=size_order, title="Test size (actual rows)", axis=theme.size_axis()),
                xOffset="engine_name:N",
                color=alt.Color("engine_name:N", title="Engine", scale=theme.scale(sub["engine_name"])))
            if log_y:
                chart = base.mark_point(filled=True, size=110, opacity=1).encode(
                    y=alt.Y("median_s:Q", title="Seconds (log scale)", scale=alt.Scale(type="log")), tooltip=tips)
            else:
                chart = base.mark_bar(**theme.BAR_V).encode(y=alt.Y("median_s:Q", title="Seconds"), tooltip=tips)
            if phase == "warm":
                chart += base.mark_rule(strokeWidth=1.6).encode(y="min_s:Q", y2="max_s:Q", color=alt.value(theme.INK))
            st.altair_chart(chart, width="stretch", theme=None)
            cols = ["engine_name", "size", "phase", "n", "median_s", "mean_s", "stdev_s", "min_s", "max_s"]
            st.dataframe(summary[(summary["query"] == q) & summary["engine"].isin(["postgres", "duckdb"])][cols]
                         .sort_values(["size", "engine_name", "phase"]), width="stretch", hide_index=True)
            for s in ratio_sentences(summary[summary["query"].isin(["Q1", "Q2", "Q3", "Q5"])], labels):
                st.write("- " + s)
            sp = speedup_table(summary, phase)
            if not sp.empty:
                sp["size"] = sp["months"].map(labels)
                st.markdown(f"**Speed ratio = PostgreSQL time / DuckDB time** ({phase} medians, same query and size)")
                points = alt.Chart(sp).mark_point(filled=True, size=120, opacity=1).encode(
                    x=alt.X("size:N", sort=size_order, title="Test size (actual rows)", axis=theme.size_axis()),
                    xOffset="query:N",
                    y=alt.Y("ratio:Q", title="PostgreSQL / DuckDB (log scale)", scale=alt.Scale(type="log")),
                    color=alt.Color("query:N", title="Query", scale=theme.scale(sp["query"])),
                    tooltip=[alt.Tooltip("query:N", title="Query"), alt.Tooltip("size:N", title="Test size"),
                             alt.Tooltip("postgres_s:Q", title="PostgreSQL (s)", format=".4f"),
                             alt.Tooltip("duckdb_s:Q", title="DuckDB (s)", format=".4f"),
                             alt.Tooltip("ratio:Q", title="Ratio", format=".2f"),
                             alt.Tooltip("winner:N", title="Faster"),
                             alt.Tooltip("factor:Q", title="Faster by (x)", format=".1f")])
                parity = alt.Chart(pd.DataFrame({"y": [1.0]})).mark_rule(strokeDash=[5, 4], color=theme.MUTED).encode(
                    y="y:Q")
                st.altair_chart(points + parity, width="stretch", theme=None)
                st.caption("Above the dashed line (ratio > 1) DuckDB was faster by that factor; below it "
                           "PostgreSQL was faster. Only PostgreSQL without the extra index is compared. Q4 is not "
                           "on this axis: it is a client-side lookup latency, shown separately below.")
            st.caption("Ratios describe THIS run on THIS machine. PostgreSQL is reached through a client/server "
                       "connection and DuckDB runs inside the benchmark process, so part of any difference is "
                       "architecture, not only storage layout.")

        with card("q4"):
            st.subheader("Q4: Sequential point lookups (application-level latency)")
            lat, pl = read_csv_safe(RESULTS, "lookup_latency.csv")
            st.caption("Each repetition runs a fixed series of one-after-the-other lookups by trip_id from a Python "
                       "client (N_LOOKUPS in sql/queries.py). The `lookups` column is the POOLED number of per-lookup "
                       "measurements for that engine, size and phase, i.e. lookups per repetition x repetitions; it is "
                       "not the number of lookups in a single repetition. The latency includes Python call overhead "
                       "and, for PostgreSQL, a network round trip per lookup: it is not pure engine processing time.")
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
