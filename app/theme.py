"""Visual layer of the dashboard: colours, CSS, Altair theme, sidebar brand art.

Nothing here reads or computes results. dashboard.py calls apply() once at the top and
uses the small helpers below (colour scales, axis settings) when it builds a chart.
Look: dark navy sidebar with a generated night skyline, light content area, white rounded
cards, pill tabs and pill radio buttons, blue-to-purple chart palette.
"""
from __future__ import annotations

import base64
import html
import random

import altair as alt
import streamlit as st

# --------------------------------------------------------------------- palette
INK = "#0f1b3d"        # headings, strong text
BODY = "#2a3556"       # running text
MUTED = "#5d6b8c"      # captions, axis text (5.4:1 on white)
LINE = "#e3e9f4"       # card border, grid
PAGE = "#f2f5fb"       # page background
CARD = "#ffffff"
NAVY = "#0b1530"       # sidebar
BLUE = "#2f6fed"
PURPLE = "#8a3ffc"
TEAL = "#12a594"
AMBER = "#e8890c"
SLATE = "#8391b0"
FONT = "Inter, 'Segoe UI', system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif"

# One colour per label, the same in every chart.
COLORS = {
    "PostgreSQL": BLUE,
    "PostgreSQL + index on pickup_ts": TEAL,
    "DuckDB": PURPLE,
    "PostgreSQL (tables+PK)": BLUE,
    "PostgreSQL + index": TEAL,
    "DuckDB file": PURPLE,
    "Parquet, cleaned night trips": AMBER,
    "Parquet, original files (all trips)": SLATE,
    "avg_fare": BLUE,
    "avg_tip": PURPLE,
}

VERTICAL_GRADIENT = {"gradient": "linear", "x1": 1, "y1": 1, "x2": 1, "y2": 0,
                     "stops": [{"offset": 0, "color": PURPLE}, {"offset": 1, "color": BLUE}]}
BAR_V = {"cornerRadiusTopLeft": 5, "cornerRadiusTopRight": 5}
BAR_H = {"cornerRadiusTopRight": 5, "cornerRadiusBottomRight": 5}

# "1 mo · 1,628,153 rows (24% of rows)" -> three short lines under each group of bars
_SIZE = "split(datum.label, ' · ')"
SIZE_LABEL_EXPR = (f"indexof(datum.label, ' · ') < 0 ? datum.label : "
                   f"[{_SIZE}[0], split({_SIZE}[1], ' (')[0], '(' + split({_SIZE}[1], ' (')[1]]")


def scale(values) -> alt.Scale:
    """Colour scale with a fixed colour per label (alphabetical legend order, as before)."""
    domain = sorted({str(v) for v in values})
    fallback = [BLUE, PURPLE, TEAL, AMBER, SLATE]
    rng = [COLORS.get(d, fallback[i % len(fallback)]) for i, d in enumerate(domain)]
    return alt.Scale(domain=domain, range=rng)


def size_axis() -> alt.Axis:
    return alt.Axis(labelAngle=0, labelExpr=SIZE_LABEL_EXPR, labelLimit=240, labelPadding=8,
                    labelLineHeight=13, labelFontSize=10.5)


def label_axis(limit: int = 340) -> alt.Axis:
    """Category axis whose long labels (zone names) are not cut."""
    return alt.Axis(labelLimit=limit)


# --------------------------------------------------------------------- altair theme
def _register_altair() -> None:
    @alt.theme.register("nyc_night", enable=True)
    def nyc_night() -> alt.theme.ThemeConfig:
        return alt.theme.ThemeConfig({"config": {
            "background": "transparent",
            "font": FONT,
            "view": {"stroke": None, "continuousWidth": 400, "continuousHeight": 280},
            "axis": {"labelColor": MUTED, "titleColor": MUTED, "labelFont": FONT, "titleFont": FONT,
                     "labelFontSize": 11, "titleFontSize": 11, "titleFontWeight": 600,
                     "gridColor": LINE, "gridDash": [3, 3], "domain": False, "ticks": False,
                     "labelPadding": 6, "titlePadding": 10},
            "axisX": {"grid": False},
            "legend": {"labelColor": BODY, "titleColor": MUTED, "labelFont": FONT, "titleFont": FONT,
                       "labelFontSize": 12, "titleFontSize": 11, "titleFontWeight": 600,
                       "symbolType": "circle", "symbolSize": 90, "padding": 4,
                       "orient": "top", "direction": "horizontal", "titleAnchor": "start"},
            "range": {"category": [BLUE, PURPLE, TEAL, AMBER, SLATE]},
            "bar": {"fill": BLUE},
            "line": {"strokeWidth": 2.75, "stroke": BLUE},
            "point": {"filled": True, "size": 55},
            "rule": {"stroke": INK},
        }})


# --------------------------------------------------------------------- sidebar art
def skyline_data_uri(width: int = 640, height: int = 300, seed: int = 11) -> str:
    """A night skyline drawn from rectangles and lit windows. Generated, not a photo."""
    rnd = random.Random(seed)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMax slice">',
        '<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#050a1c"/><stop offset=".55" stop-color="#14224f"/>'
        '<stop offset="1" stop-color="#3a3f86"/></linearGradient>'
        '<radialGradient id="glow" cx=".78" cy=".2" r=".5"><stop offset="0" stop-color="#8da2ff" stop-opacity=".55"/>'
        '<stop offset="1" stop-color="#8da2ff" stop-opacity="0"/></radialGradient></defs>',
        f'<rect width="{width}" height="{height}" fill="url(#sky)"/>',
        f'<rect width="{width}" height="{height}" fill="url(#glow)"/>',
        '<circle cx="500" cy="62" r="20" fill="#e9edff" opacity=".92"/>',
        '<circle cx="508" cy="57" r="19" fill="#14224f"/>',
    ]
    for _ in range(46):  # stars
        parts.append(f'<circle cx="{rnd.randint(0, width)}" cy="{rnd.randint(0, int(height * .5))}" '
                     f'r="{rnd.choice([.6, .8, 1.1])}" fill="#fff" opacity="{rnd.choice([.35, .55, .8])}"/>')
    for layer, (fill, lo, hi, light) in enumerate((("#0e1838", 70, 150, .12), ("#080f27", 90, 215, .30))):
        x = -10
        while x < width:
            w = rnd.randint(26, 58)
            h = rnd.randint(lo, hi)
            top = height - h
            parts.append(f'<rect x="{x}" y="{top}" width="{w}" height="{h}" fill="{fill}"/>')
            for wy in range(top + 8, height - 6, 11):
                for wx in range(x + 5, x + w - 6, 9):
                    if rnd.random() < light:
                        c = rnd.choice(["#ffd37a", "#ffe9b0", "#9fc2ff"])
                        parts.append(f'<rect x="{wx}" y="{wy}" width="4" height="5" fill="{c}" opacity=".9"/>')
            x += w + rnd.randint(0, 4)
    parts.append("</svg>")
    return "data:image/svg+xml;base64," + base64.b64encode("".join(parts).encode()).decode()


_MOON = ("data:image/svg+xml;base64," + base64.b64encode(
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#5b4bd6" '
    b'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'
).decode())


def sidebar(facts: list[tuple[str, str]], status: tuple[str, str] | None, footer: str) -> None:
    """Brand block + facts. `facts` are (label, value) pairs already read from the result files."""
    esc = html.escape
    rows = "".join(f'<div class="fact"><span>{esc(k)}</span><b>{esc(v)}</b></div>' for k, v in facts)
    pill = ""
    if status:
        state, text = status
        pill = f'<div class="pill pill-{esc(state)}">{esc(text)}</div>'
    st.sidebar.markdown(
        f'<div class="brand" style="background-image:url({skyline_data_uri()})">'
        f'<div class="brand-fade"></div><div class="brand-text"><div class="brand-title">NYC<br>TAXI NIGHTS</div>'
        f'<div class="brand-sub">PostgreSQL vs DuckDB</div></div></div>'
        f'<div class="side-block"><div class="side-label">Dataset</div>{rows}</div>'
        f'{"<div class=side-block><div class=side-label>Benchmark</div>" + pill + "</div>" if pill else ""}'
        f'<div class="side-foot">{esc(footer)}</div>',
        unsafe_allow_html=True)


# --------------------------------------------------------------------- css
def css() -> str:
    return f"""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
:root {{ --ink:{INK}; --body:{BODY}; --muted:{MUTED}; --line:{LINE}; --card:{CARD}; --blue:{BLUE}; --purple:{PURPLE}; }}
.stApp, .stApp p, .stApp li, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp button, .stApp input, .stApp textarea {{ font-family:{FONT}; }}
/* keep the icon font of Streamlit icons (sidebar arrows, toolbar) */
[data-testid="stIconMaterial"], .material-icons, [class*="material-symbols"] {{ font-family:"Material Symbols Rounded" !important; }}
.stApp {{ background:{PAGE}; color:var(--body); }}
[data-testid="stHeader"] {{ background:transparent; }}
[data-testid="stMainBlockContainer"] {{ max-width:1320px; padding-top:2.2rem; padding-bottom:4rem; }}
@media (max-width:640px) {{ [data-testid="stMainBlockContainer"] {{ padding-top:1.4rem; padding-left:1rem; padding-right:1rem; }} }}

/* headings and text */
h1 {{ font-size:2rem !important; font-weight:800 !important; letter-spacing:-.025em; color:var(--ink) !important; line-height:1.15 !important; padding:0 0 .25rem !important; text-wrap:balance; }}
h2, h3 {{ color:var(--ink) !important; font-weight:700 !important; letter-spacing:-.012em; }}
h3 {{ font-size:1.1rem !important; padding:0 0 .25rem !important; }}
[data-testid="stCaptionContainer"], .stCaption {{ color:var(--muted) !important; font-size:.84rem; line-height:1.5; }}
[data-testid="stMarkdownContainer"] li {{ color:var(--body); }}

/* tabs as pills */
[role="tablist"] {{ gap:.5rem !important; padding:.25rem 0 .7rem !important; flex-wrap:wrap; box-shadow:none !important; border-bottom:0 !important; }}
[data-testid="stTabs"] > div, [data-testid="stTabs"] > div > div {{ border-bottom:0 !important; box-shadow:none !important; }}
[role="tablist"] .react-aria-SelectionIndicator, [data-testid="stTabs"] [data-baseweb="tab-highlight"] {{ display:none !important; }}
[data-testid="stTab"] {{ background:#e6ecf8 !important; border-radius:999px !important; padding:.5rem 1.1rem !important; height:auto !important; color:#34426a !important; font-weight:600; transition:background .15s, color .15s; border:0 !important; }}
[data-testid="stTab"]:hover {{ background:#d7e1f6 !important; }}
[data-testid="stTab"][aria-selected="true"] {{ background:var(--blue) !important; box-shadow:0 4px 14px rgba(47,111,237,.32); }}
[data-testid="stTab"] p {{ color:inherit !important; font-size:.92rem; font-weight:600; }}
[data-testid="stTab"][aria-selected="true"] p {{ color:#fff !important; }}
[data-testid="stTab"]:focus-visible {{ outline:2px solid var(--ink); outline-offset:2px; }}

/* cards: every st.container(border=True, key="card-...") */
[class*="st-key-card"] {{ background:var(--card) !important; border:1px solid var(--line) !important; border-radius:18px !important; box-shadow:0 1px 2px rgba(16,27,61,.04), 0 6px 20px rgba(16,27,61,.05); padding:1.15rem 1.3rem 1.2rem !important; }}
@media (max-width:640px) {{ [class*="st-key-card"] {{ padding:.9rem .85rem 1rem !important; border-radius:14px !important; }} }}

/* metric as a KPI card with an icon */
[data-testid="stMetric"] {{ position:relative; background:var(--card); border:1px solid var(--line); border-radius:18px; padding:1.1rem 1.3rem 1.1rem 5.3rem; box-shadow:0 1px 2px rgba(16,27,61,.04), 0 6px 20px rgba(16,27,61,.05); min-height:5.2rem; display:flex; flex-direction:column; justify-content:center; }}
[data-testid="stMetric"]::before {{ content:""; position:absolute; left:1.2rem; top:50%; transform:translateY(-50%); width:3.1rem; height:3.1rem; border-radius:50%; background:#e9e6ff url("{_MOON}") center/1.6rem no-repeat; }}
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] > div, [data-testid="stMetricLabel"] p {{ white-space:normal !important; overflow:visible !important; text-overflow:clip !important; }}
[data-testid="stMetricLabel"] p {{ color:var(--muted); font-size:.86rem; font-weight:500; }}
/* KPI values must never be cut ("15,289,..."). The card is a size container: the value scales with the
   card width (never above 2rem, as before) and wraps at spaces only, so long words such as "PostgreSQL 2.9x faster"
   use two lines instead of an ellipsis. Numbers contain no spaces and stay whole. */
[data-testid="stMetric"] {{ container-type:inline-size; min-width:0; }}
[data-testid="stMetricValue"], [data-testid="stMetricValue"] > div {{ color:var(--ink); font-weight:800; letter-spacing:-.02em; font-size:clamp(1.1rem, 15cqw, 2rem) !important; line-height:1.15 !important; }}
[data-testid="stMetricValue"], [data-testid="stMetricValue"] > div, [data-testid="stMetricValue"] p {{ white-space:normal !important; overflow:visible !important; text-overflow:clip !important; overflow-wrap:break-word; }}
[data-testid="stMetricValue"] p {{ font-size:inherit !important; line-height:inherit !important; margin:0; }}
@media (max-width:1500px) {{
  [data-testid="stMetric"] {{ padding-left:4.1rem; padding-right:.9rem; }}
  [data-testid="stMetric"]::before {{ left:.9rem; width:2.4rem; height:2.4rem; background-size:1.3rem; }}
}}
/* Key findings: the three cards share one height whatever the wrapping of title and value. */
.st-key-card-findings [data-testid="stMetric"] {{ min-height:9.4rem; }}
.st-key-card-quality-summary [data-testid="stMetric"] {{ min-height:6.4rem; }}

/* status messages */
[data-testid="stAlertContainer"] {{ border-radius:14px !important; border:0 !important; padding:.35rem .5rem; }}
[data-testid="stAlertContainer"] p {{ font-size:.92rem; font-weight:500; }}

/* radio as pills */
[data-testid="stRadioGroup"] {{ gap:.5rem; flex-wrap:wrap; }}
[data-testid="stRadioOption"] {{ background:#e6ecf8 !important; border-radius:999px !important; padding:.4rem 1rem !important; margin:0; cursor:pointer; transition:background .15s; }}
[data-testid="stRadioOption"]:hover {{ background:#d7e1f6 !important; }}
[data-testid="stRadioOption"] > div > div:first-child {{ display:none !important; }}
[data-testid="stRadioOption"] p {{ font-weight:600; font-size:.88rem; color:#34426a; }}
[data-testid="stRadioOption"][data-selected="true"] {{ background:var(--blue) !important; box-shadow:0 4px 14px rgba(47,111,237,.3); }}
[data-testid="stRadioOption"][data-selected="true"] p {{ color:#fff !important; }}
[data-testid="stRadioOption"]:has(input:focus-visible) {{ outline:2px solid var(--ink); outline-offset:2px; }}
[data-testid="stWidgetLabel"] {{ background:transparent !important; }}
[data-testid="stWidgetLabel"] p {{ color:var(--muted); font-weight:600; font-size:.78rem; text-transform:uppercase; letter-spacing:.05em; }}

/* select boxes */
[data-testid="stSelectbox"] [role="group"] {{ background:#fff !important; border:1px solid #d5dded !important; border-radius:12px !important; min-height:2.6rem; }}
[data-testid="stSelectbox"] [role="group"]:hover, [data-testid="stSelectbox"] [role="group"]:focus-within {{ border-color:var(--blue); }}
[data-testid="stSelectbox"] {{ max-width:32rem; }}

/* tables */
[data-testid="stDataFrame"] {{ border:1px solid var(--line); border-radius:12px; overflow:hidden; }}

/* charts */
[data-testid="stVegaLiteChart"], [data-testid="stArrowVegaLiteChart"] {{ margin-top:.2rem; }}

/* sidebar */
[data-testid="stSidebar"] {{ background:linear-gradient(180deg,{NAVY} 0%,#0a1228 100%); border-right:0; }}
[data-testid="stSidebarContent"] {{ padding:0; }}
[data-testid="stSidebarHeader"] {{ position:absolute; right:0; top:0; z-index:5; padding:.6rem; background:transparent; }}
[data-testid="stSidebar"] [data-testid="stSidebarHeader"] * {{ color:#cfd8f5; }}
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {{ padding:0 0 1.5rem; }}
.brand {{ position:relative; height:250px; background-size:cover; background-position:center bottom; }}
.brand-fade {{ position:absolute; inset:0; background:linear-gradient(180deg,rgba(11,21,48,0) 35%,{NAVY} 100%); }}
.brand-text {{ position:absolute; left:1.4rem; right:1rem; bottom:.7rem; }}
.brand-title {{ color:#fff; font-weight:800; font-size:2rem; line-height:1.02; letter-spacing:.01em; }}
.brand-sub {{ color:#b9c6ee; font-size:.95rem; margin-top:.45rem; }}
.side-block {{ padding:1.1rem 1.4rem 0; }}
.side-label {{ color:#8ea0d6; font-size:.72rem; font-weight:700; letter-spacing:.12em; text-transform:uppercase; margin-bottom:.5rem; }}
.fact {{ display:flex; justify-content:space-between; gap:.8rem; padding:.42rem 0; border-bottom:1px solid rgba(255,255,255,.08); font-size:.86rem; color:#c7d2f4; }}
.fact b {{ color:#fff; font-weight:600; text-align:right; }}
.pill {{ display:inline-block; padding:.32rem .8rem; border-radius:999px; font-size:.82rem; font-weight:600; }}
.pill-complete {{ background:rgba(52,211,153,.18); color:#7ee8c0; }}
.pill-running {{ background:rgba(96,165,250,.2); color:#a9ccff; }}
.pill-failed {{ background:rgba(248,113,113,.2); color:#ffb4b4; }}
.pill-unknown {{ background:rgba(255,255,255,.1); color:#c7d2f4; }}
.side-foot {{ margin:1.4rem 1.4rem 0; padding-top:.9rem; border-top:1px solid rgba(255,255,255,.08); color:#8ea0d6; font-size:.78rem; line-height:1.5; }}

@media (prefers-reduced-motion:reduce) {{ * {{ transition:none !important; }} }}
"""


def apply() -> None:
    """Inject the theme. Call once, right after st.set_page_config."""
    _register_altair()
    st.markdown(f"<style>{css()}</style>", unsafe_allow_html=True)
