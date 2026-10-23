# FX & Real Exchange Rate Explorer

A Streamlit app for exploring monthly nominal and real exchange rates using IMF CPI and exchange-rate series. Designed for Python 3.12.

## Features

- Choose Country 1 and Country 2 from a country list, including the IMF Euro area reference area (`G163`).
- Requests monthly CPI and exchange-rate observations from January 1976 onward where available.
- Defines `S(t)` as units of Country 2 currency per one unit of Country 1 currency.
- Calculates `RER(t) = S(t) * P1(t) / P2(t)`.
- Recalculates `RER*` as the average of valid RER observations inside the selected sample period.
- Calculates `Real Index(t) = 100 * RER(t) / RER*`.
- Interactive Real, Nominal, and Both chart modes. Both mode uses separate left/right axes.
- Displays and downloads the observations and calculations for the selected sample period as CSV.
- Handles incomplete country coverage by retaining missing values and calculating RER only for valid months.

## Files

- `app.py` — Streamlit application
- `requirements.txt` — Python dependencies
- `README.md` — setup and usage instructions

## Requirements

- Python 3.12
- Internet access to the IMF API

## Run locally

Create and activate a virtual environment, then install dependencies.

### macOS / Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

Streamlit will print a local URL, typically `http://localhost:8501`.

## Data and formula conventions

The series keys supplied in the assignment brief are:

- CPI: `{ISO3}.CPI._T.IX.M`
- Exchange rate: `{ISO3}.XDC_USD.PA_RT.M`
- Euro area reference area: `G163`

The app treats the IMF exchange-rate series as local-currency units per USD. If `FX1` is Country 1 currency units per USD and `FX2` is Country 2 currency units per USD, then:

`S(t) = FX2(t) / FX1(t)`

This gives units of Country 2 currency per one unit of Country 1 currency.

`RER*` is recalculated from the valid monthly RER observations in the currently selected date window. It is not a fixed historical base. Nominal `S(t)` is displayed in actual currency units and is not normalized.

## Important IMF API note

The IMF SDMX 3.0 API can expose series under dataset/dataflow identifiers that may change or differ by domain. The app tries several common URL shapes using the series keys provided in the brief. If data retrieval fails, the app shows the attempted endpoints and error details rather than silently presenting fabricated data.

If the IMF API requires a different dataflow identifier for the current CPI or exchange-rate dataset, update the candidate URLs in `fetch_imf_series()` in `app.py` to match the current IMF SDMX 3.0 dataflow. Also confirm the API's current content-negotiation and key syntax. Coverage may vary by country and may not extend back to January 1976.

## Deploy on Streamlit Community Cloud

1. Create a GitHub repository and add `app.py`, `requirements.txt`, and `README.md`.
2. Open Streamlit Community Cloud and create a new app from the repository.
3. Set the main file path to `app.py`.
4. Deploy. The app needs outbound internet access to query the IMF API.

## Limitations

- The country selector is a curated list of ISO3 codes, not a live IMF-maintained country catalogue.
- Some IMF series may be missing, discontinued, or reported under a different key.
- The first API fetch can take time; responses are cached for six hours.
- CPI index conventions can differ across countries. The formula follows the assignment specification and uses each selected CPI series as supplied by the IMF.
