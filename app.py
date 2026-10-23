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
