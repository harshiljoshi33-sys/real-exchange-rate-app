from __future__ import annotations

from datetime import date
from io import BytesIO, StringIO
import json
import re
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(page_title="FX & Real Exchange Rate Explorer", page_icon="🌍", layout="wide")

IMF_BASE = "https://api.imf.org/external/sdmx/3.0"
START_DEFAULT = date(1976, 1, 1)
TIMEOUT = 45

# ISO3 country list is intentionally broad; users can also select the Euro area.
# IMF's Euro area reference area is G163.
COUNTRIES = {
    "Australia": "AUS", "Austria": "AUT", "Belgium": "BEL", "Brazil": "BRA",
    "Canada": "CAN", "Chile": "CHL", "China": "CHN", "Colombia": "COL",
    "Czechia": "CZE", "Denmark": "DNK", "Egypt": "EGY", "Euro area": "G163",
    "Finland": "FIN", "France": "FRA", "Germany": "DEU", "Greece": "GRC",
    "Hong Kong SAR": "HKG", "Hungary": "HUN", "Iceland": "ISL", "India": "IND",
    "Indonesia": "IDN", "Ireland": "IRL", "Israel": "ISR", "Italy": "ITA",
    "Japan": "JPN", "Malaysia": "MYS", "Mexico": "MEX", "Netherlands": "NLD",
    "New Zealand": "NZL", "Nigeria": "NGA", "Norway": "NOR", "Pakistan": "PAK",
    "Peru": "PER", "Philippines": "PHL", "Poland": "POL", "Portugal": "PRT",
    "Romania": "ROU", "Russia": "RUS", "Saudi Arabia": "SAU", "Singapore": "SGP",
    "South Africa": "ZAF", "South Korea": "KOR", "Spain": "ESP", "Sri Lanka": "LKA",
    "Sweden": "SWE", "Switzerland": "CHE", "Taiwan Province of China": "TWN",
    "Thailand": "THA", "Türkiye": "TUR", "Ukraine": "UKR", "United Arab Emirates": "ARE",
    "United Kingdom": "GBR", "United States": "USA", "Vietnam": "VNM",
}

HEADERS = {
    "Accept": "application/vnd.sdmx.data+csv;version=2.0.0, text/csv, application/vnd.sdmx.data+json;version=2.0.0, application/json",
    "User-Agent": "FX-RER-Explorer/1.0 (educational project)",
}

def _find_records(obj: Any) -> list[dict]:
    """Find a list of observation-like records in common SDMX JSON shapes."""
    found: list[dict] = []
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            found.extend(obj)
        else:
            for item in obj:
                found.extend(_find_records(item))
    elif isinstance(obj, dict):
        # Common compact JSON and data-message shapes
        for key in ("data", "observations", "series", "value", "values", "dataSets", "dataSet"):
            if key in obj:
                found.extend(_find_records(obj[key]))
        if not found and any(k in obj for k in ("TIME_PERIOD", "time_period", "period", "TIME", "OBS_VALUE", "obs_value", "value")):
            found.append(obj)
        if not found:
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    found.extend(_find_records(value))
    return found

def _parse_sdmx_json(payload: Any) -> pd.DataFrame:
    records = _find_records(payload)
    if records:
        df = pd.json_normalize(records)
        # Flattened SDMX-JSON can encode period/value in nested observation fields.
        return df
    raise ValueError("The IMF response was JSON, but no recognizable observation records were found.")

def _parse_response(response: requests.Response) -> pd.DataFrame:
    content_type = response.headers.get("Content-Type", "").lower()
    text = response.text.strip()
    if not text:
        raise ValueError("The IMF API returned an empty response.")
    if "json" in content_type or text.startswith("{") or text.startswith("["):
        return _parse_sdmx_json(response.json())
    # IMF SDMX CSV responses can contain metadata rows or standard CSV headers.
    try:
        return pd.read_csv(StringIO(text), comment="#")
    except Exception as exc:
        raise ValueError(f"Could not parse IMF response as CSV: {exc}") from exc

def _column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    normalized = {re.sub(r"[^a-z0-9]", "", str(c).lower()): str(c) for c in df.columns}
    for candidate in candidates:
        key = re.sub(r"[^a-z0-9]", "", candidate.lower())
        if key in normalized:
            return normalized[key]
    # fallback for SDMX columns with prefixes/suffixes
    for candidate in candidates:
        key = re.sub(r"[^a-z0-9]", "", candidate.lower())
        for norm, original in normalized.items():
            if key in norm:
                return original
    return None

def _normalize_observations(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        raise ValueError("The IMF API returned no observations.")
    time_col = _column(df, ["TIME_PERIOD", "time_period", "period", "TIME", "date", "observation_date"])
    value_col = _column(df, ["OBS_VALUE", "obs_value", "observation_value", "value", "OBSERVATION_VALUE"])
    if time_col is None or value_col is None:
        raise ValueError(
            "Could not identify time and value columns in the IMF response. "
            f"Columns received: {', '.join(map(str, df.columns[:30]))}"
        )
    result = pd.DataFrame({
        "date": pd.to_datetime(df[time_col].astype(str).str.slice(0, 7) + "-01", errors="coerce"),
        "value": pd.to_numeric(df[value_col].astype(str).str.replace(",", "", regex=False), errors="coerce"),
    }).dropna()
    result = result[result["value"] > 0].sort_values("date")
    result = result.drop_duplicates("date", keep="last").reset_index(drop=True)
    if result.empty:
        raise ValueError("No valid positive numeric monthly observations were found.")
    return result

@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def fetch_imf_series(iso3: str, series_kind: str) -> pd.DataFrame:
    """
    Fetch an IMF SDMX 3.0 series.

    The IMF's API dataflow identifiers and content negotiation can vary by dataset.
    This function tries the supplied series key against common SDMX 3.0 URL shapes.
    If IMF changes its dataflow identifier, the error lists attempted URLs so the
    dataflow can be updated without changing the calculations below.
    """
    if series_kind == "CPI":
        key = f"{iso3}.CPI._T.IX.M"
    elif series_kind == "FX":
        key = f"{iso3}.XDC_USD.PA_RT.M"
    else:
        raise ValueError("Unknown series kind")

    # These dataflow names are tried because IMF SDMX APIs expose data under
    # dataset/dataflow identifiers. The requested key itself follows the assignment brief.
    candidates = [
        f"{IMF_BASE}/data/IMF.STA,{series_kind},1.0/{key}",
        f"{IMF_BASE}/data/IMF.STA,{series_kind},1.0/{key}?startPeriod=1976-01&dimension_at_observation=TIME_PERIOD",
        f"{IMF_BASE}/data/{series_kind}/{key}",
        f"{IMF_BASE}/data/{key}",
    ]
    errors = []
    session = requests.Session()
    for url in candidates:
        try:
            response = session.get(url, headers=HEADERS, timeout=TIMEOUT)
            if response.status_code >= 400:
                errors.append(f"{response.status_code} at {url}")
                continue
            raw = _parse_response(response)
            normalized = _normalize_observations(raw)
            normalized["series_key"] = key
            normalized["source_url"] = url
            return normalized
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError(
        f"Could not retrieve {series_kind} data for {iso3} from the IMF SDMX API. "
        "The endpoint may require a different IMF dataflow identifier or the series may be unavailable. "
        "Attempt details: " + " | ".join(errors[-4:])
    )

def fetch_pair(country1_name: str, country2_name: str) -> pd.DataFrame:
    c1, c2 = COUNTRIES[country1_name], COUNTRIES[country2_name]
    with st.spinner(f"Retrieving monthly IMF CPI and exchange-rate series for {country1_name} and {country2_name}..."):
        p1 = fetch_imf_series(c1, "CPI").rename(columns={"value": "P1"})
        p2 = fetch_imf_series(c2, "CPI").rename(columns={"value": "P2"})
        # IMF XDC_USD is local currency units per USD.
        # S = units of Country 2 currency per Country 1 currency:
        # (Country 2 units per USD) / (Country 1 units per USD).
        fx1 = fetch_imf_series(c1, "FX").rename(columns={"value": "LCU1_per_USD"})
        fx2 = fetch_imf_series(c2, "FX").rename(columns={"value": "LCU2_per_USD"})
    merged = p1[["date", "P1"]].merge(p2[["date", "P2"]], on="date", how="outer")
    merged = merged.merge(fx1[["date", "LCU1_per_USD"]], on="date", how="outer")
    merged = merged.merge(fx2[["date", "LCU2_per_USD"]], on="date", how="outer")
    merged = merged.sort_values("date").reset_index(drop=True)
    merged["S"] = merged["LCU2_per_USD"] / merged["LCU1_per_USD"]
    merged["RER"] = merged["S"] * merged["P1"] / merged["P2"]
    merged["country1"] = country1_name
    merged["country2"] = country2_name
    return merged

def build_chart(df: pd.DataFrame, view: str, c1: str, c2: str) -> go.Figure:
    fig = go.Figure()
    if view in ("Real", "Both"):
        fig.add_trace(go.Scatter(
            x=df["date"], y=df["Real Index"], mode="lines", name="Real Index (base = 100)",
            yaxis="y", line={"width": 2}
        ))
    if view in ("Nominal", "Both"):
        fig.add_trace(go.Scatter(
            x=df["date"], y=df["S"], mode="lines", name=f"Nominal S (units of {c2} per 1 {c1})",
            yaxis="y2" if view == "Both" else "y", line={"width": 2}
        ))
    layout = {
        "title": f"Real and nominal exchange rates: {c1} → {c2}",
        "xaxis": {"title": "Month", "rangeslider": {"visible": True}},
        "hovermode": "x unified",
        "legend": {"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
        "margin": {"l": 25, "r": 25, "t": 80, "b": 30},
    }
    if view == "Both":
        layout["yaxis"] = {"title": "Real Index (base = 100)", "side": "left"}
        layout["yaxis2"] = {"title": f"Nominal S ({c2} per {c1})", "overlaying": "y", "side": "right", "showgrid": False}
    elif view == "Real":
        layout["yaxis"] = {"title": "Real Index (base = 100)"}
    else:
        layout["yaxis"] = {"title": f"Nominal S ({c2} per {c1})"}
    fig.update_layout(**layout)
    return fig

st.title("🌍 FX & Real Exchange Rate Explorer")
st.caption("Monthly IMF CPI and exchange-rate data · January 1976 onward where available")

with st.sidebar:
    st.header("Sample & countries")
    country_names = sorted(COUNTRIES.keys())
    default1 = country_names.index("United Kingdom")
    default2 = country_names.index("United States")
    country1 = st.selectbox("Country 1 (base currency)", country_names, index=default1)
    country2 = st.selectbox("Country 2 (quote currency)", country_names, index=default2)
    start_date = st.date_input("Sample start month", value=date(1976, 1, 1), min_value=date(1976, 1, 1), max_value=date.today())
    end_date = st.date_input("Sample end month", value=date.today().replace(day=1), min_value=date(1976, 1, 1), max_value=date.today())
    view = st.radio("Chart view", ["Real", "Nominal", "Both"], index=2, horizontal=True)
    load = st.button("Load / refresh data", type="primary", use_container_width=True)

if country1 == country2:
    st.error("Choose two different countries/areas.")
    st.stop()
if start_date > end_date:
    st.error("Sample start month must be on or before sample end month.")
    st.stop()

cache_key = f"{COUNTRIES[country1]}_{COUNTRIES[country2]}"
if load or "fx_pair_data" not in st.session_state or st.session_state.get("fx_cache_key") != cache_key:
    try:
        st.session_state["fx_pair_data"] = fetch_pair(country1, country2)
        st.session_state["fx_cache_key"] = cache_key
        st.session_state.pop("fx_load_error", None)
    except Exception as exc:
        st.session_state["fx_load_error"] = str(exc)

if st.session_state.get("fx_load_error"):
    st.error("Unable to load IMF data")
    st.code(st.session_state["fx_load_error"])
    st.markdown(
        "If the IMF API rejects the candidate dataflow paths, check the IMF SDMX 3.0 API's current dataflow identifier "
        "for the CPI and exchange-rate datasets. The series keys used by this app are the keys supplied in the assignment brief."
    )
    st.stop()

raw = st.session_state["fx_pair_data"].copy()
raw["date"] = pd.to_datetime(raw["date"])
sample = raw[
    (raw["date"].dt.date >= date(start_date.year, start_date.month, 1)) &
    (raw["date"].dt.date <= date(end_date.year, end_date.month, 1))
].copy()
sample = sample.sort_values("date").reset_index(drop=True)

# RER* uses valid months only, recalculated from the selected sample on every rerun.
valid_rer = sample["RER"].replace([float("inf"), float("-inf")], pd.NA).dropna()
valid_rer = valid_rer[valid_rer > 0]
rer_star = float(valid_rer.mean()) if not valid_rer.empty else float("nan")
sample["RER_star"] = rer_star
sample["Real Index"] = 100 * sample["RER"] / rer_star if pd.notna(rer_star) and rer_star != 0 else pd.NA

valid_months = int(sample["RER"].notna().sum())
missing_months = int(len(sample) - valid_months)
m1, m2, m3, m4 = st.columns(4)
m1.metric("Months in selected sample", f"{len(sample):,}")
m2.metric("Valid RER months", f"{valid_months:,}")
m3.metric("Sample mean RER (RER*)", f"{rer_star:,.5g}" if pd.notna(rer_star) else "N/A")
m4.metric("Months missing one or more inputs", f"{missing_months:,}")

if sample.empty:
    st.warning("No observations fall within the selected sample period.")
    st.stop()
if pd.isna(rer_star):
    st.warning("No months have valid CPI and exchange-rate observations for both countries in this selected period.")
    st.stop()

st.plotly_chart(build_chart(sample, view, country1, country2), use_container_width=True)

with st.expander("Methodology and data coverage", expanded=False):
    st.markdown(
        f"""
        - **Exchange-rate definition:** S(t) is units of **{country2} currency per one unit of {country1} currency**.
        - **IMF FX conversion:** IMF XDC_USD series are treated as local-currency units per USD. Therefore, S(t) = (Country 2 local-currency units per USD) / (Country 1 local-currency units per USD).
        - **Real exchange rate:** RER(t) = S(t) × P1(t) / P2(t).
        - **Base:** RER* is the arithmetic average of valid monthly RER observations within the currently selected sample. Real Index(t) = 100 × RER(t) / RER*.
        - **Nominal series:** S(t) is shown in actual currency units and is not normalized.
        - **Missing data:** CPI and FX series are merged by month. RER is available only where all required inputs are available and positive. Coverage varies by country and series.
        """
    )

st.subheader("Observations and calculations")
display_cols = ["date", "P1", "P2", "LCU1_per_USD", "LCU2_per_USD", "S", "RER", "RER_star", "Real Index"]
table = sample[display_cols].copy()
table = table.rename(columns={
    "date": "Month",
    "P1": f"CPI: {country1}",
    "P2": f"CPI: {country2}",
    "LCU1_per_USD": f"{country1} currency per USD",
    "LCU2_per_USD": f"{country2} currency per USD",
    "S": f"S: {country2} per {country1}",
    "RER": "RER",
    "RER_star": "RER* (sample mean)",
})
st.dataframe(table, use_container_width=True, hide_index=True)

csv_bytes = table.to_csv(index=False, date_format="%Y-%m").encode("utf-8")
st.download_button(
    label="⬇ Download selected sample as CSV",
    data=csv_bytes,
    file_name=f"fx_rer_{COUNTRIES[country1]}_{COUNTRIES[country2]}_{start_date:%Y%m}_{end_date:%Y%m}.csv",
    mime="text/csv",
    use_container_width=True,
)

st.caption("Source: IMF SDMX 3.0 API. Data availability and API access can vary by series and reference area.")
