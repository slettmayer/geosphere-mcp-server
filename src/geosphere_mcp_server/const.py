"""Constants for the GeoSphere MCP server.

All dataset ids, parameter lists, thresholds, and API URLs live here; never
inline these literals.
"""

from __future__ import annotations

from datetime import timedelta

# --- GeoSphere Dataset API ---

GEOSPHERE_API_BASE_URL = "https://dataset.api.hub.geosphere.at/v1"
GEOSPHERE_TIMEOUT = 30
# GeoSphere covers Austria + the Alpine region, all within CET/CEST. Used to
# render its UTC stamps and to match its daily values to a calendar day.
GEOSPHERE_TZ = "Europe/Vienna"

# Datasets (mode, resource id).
DATASET_AROME = ("forecast", "nwp-v1-1h-2500m")
DATASET_ENSEMBLE = ("forecast", "ensemble-v1-1h-2500m")
DATASET_NOWCAST = ("forecast", "nowcast-v1-15min-1km")
DATASET_INCA = ("historical", "inca-v1-1h-1km")
DATASET_CHEM = ("forecast", "chem-v2-1h-3km")
DATASET_CHEM_AQI = ("forecast", "chem_aqi-v1-1d-3km")

# AROME hourly point forecast parameters.
AROME_PARAMETERS = (
    "t2m",
    "rh2m",
    "u10m",
    "v10m",
    "ugust",
    "vgust",
    "tcc",
    "rr_acc",
    "snow_acc",
    "snowlmt",
    "grad",
    "cape",
    "cin",
)
# C-LAEF ensemble precipitation percentiles (per-hour amounts, kg m-2; the
# API exposes only p10/p50/p90 — no member counts or true probabilities).
# Like AROME's interval parameters these cover the period *ending* at their
# stamp ("in the last forecast period"), so a percentile is keyed to the
# preceding stamp to describe the hour a forecast row is stamped for. The
# cadence is read from the series rather than assumed -- see
# `_pop_by_timestamp`.
ENSEMBLE_PARAMETERS = ("rr_p10", "rr_p50", "rr_p90")
NOWCAST_PARAMETERS = ("t2m", "td", "rh2m", "rr", "pt", "dd", "ff", "fx")
INCA_PARAMETERS = ("T2M", "TD2M", "RH2M", "RR", "P0", "GL", "UU", "VV")
# The four pollutants WRF-Chem publishes, in display order: (uniform key,
# display label, WRF-Chem parameter name). This one table drives both the
# request parameters and the renderer's labels — the lists below are derived
# from it, never hand-synced.
AIR_QUALITY_POLLUTANTS = (
    ("nitrogen_dioxide", "NO₂", "no2surf"),
    ("ozone", "O₃", "o3surf"),
    ("pm10", "PM10", "pm10surf"),
    ("pm2_5", "PM2.5", "pm25surf"),
)
# Pollutant key -> WRF-Chem parameter name.
CHEM_POLLUTANTS = {key: parameter for key, _, parameter in AIR_QUALITY_POLLUTANTS}
# WRF-Chem surface concentrations (µg/m³) and the daily European AQI (1-6).
CHEM_PARAMETERS = tuple(CHEM_POLLUTANTS.values())
CHEM_AQI_PARAMETERS = ("aqi",)

# How old the newest cached INCA analysis may get before a re-fetch (seconds).
INCA_MAX_AGE_SECONDS = 55 * 60
# INCA analyses trail real time by <1 h; query a window of the last 3 hours.
INCA_LOOKBACK_HOURS = 3
# How old INCA's hourly `RR` may get before the condition derivation stops
# treating it as evidence about *now* (seconds). Only that derivation consults
# it; `is_precipitating` never does.
#
# This bound catches a slice that has stopped updating, NOT ordinary lag. INCA
# publishes ~30 min after the hour it analyses and the previous slice is served
# until the next appears, so the freshest `RR` in existence is routinely up to
# ~90 min old (see `merge_current_conditions`, which says the same of
# `observed_at`). A bound at or below that would reject the best data the
# source has for part of every publish cycle, flapping the condition between
# `rainy` and cloud-derived once an hour through steady rain -- worse than the
# staleness it set out to fix. Two hours is comfortably past the normal worst
# case: by then INCA should have published two newer analyses, so a slice this
# old means GeoSphere's own pipeline has stalled and is serving the same
# analysis to every caller. That is the unbounded case this exists for.
INCA_RR_MAX_AGE_SECONDS = 2 * 60 * 60

# Stepped precipitation probability from the ensemble rr percentiles: the
# wettest percentile above PRECIP_MIN_MM bounds the share of wet members and
# the midpoint of that range is reported: 95 / 70 / 30 / 0 %.
POP_P10_WET_PCT = 95
POP_P50_WET_PCT = 70
POP_P90_WET_PCT = 30
POP_DRY_PCT = 0

# Condition-derivation thresholds (see condition.py).
THUNDER_CAPE_JKG = 1000.0
# Convective inhibition cap. AROME publishes `cin` as NEGATIVE J/kg: 0.0 means
# uncapped, more negative means a stronger lid. CAPE only counts as thunder
# potential when inhibition is weaker than this magnitude. 50 J/kg is a
# standard boundary for weak inhibition; discrimination against real capped
# situations is unconfirmed. A missing `cin` counts as uncapped, so an hour
# AROME leaves blank degrades to the pre-CIN, CAPE-only behaviour.
CAP_CIN_JKG = 50.0
PRECIP_MIN_MM = 0.1
POURING_MM_PER_H = 4.0
WINDY_GUST_MS = 15.0
CLOUDY_TCC_PCT = 62.5
CLEAR_TCC_PCT = 12.5
WINDY_CLOUD_TCC_PCT = 60.0
# Fog heuristic (current condition only); disable by setting to False.
FOG_HEURISTIC_ENABLED = True
FOG_MIN_RH_PCT = 98.0
FOG_MAX_WIND_MS = 2.0
FOG_MIN_TCC_PCT = 87.5
# Rain/snow split when the nowcast precipitation-type code is unknown.
SNOW_MAX_T2M_C = 1.0

# Nowcast `pt` (precipitation type): 255 = no precipitation. The remaining
# code table is undocumented; codes are therefore only used as a
# "precipitating" signal, with rain/snow decided by temperature.
PT_NO_PRECIPITATION = 255

# How far back the current precipitation rate may look for a wet 15-min
# nowcast bucket. A single bucket can round to 0.0 in the gap between cells of
# an active storm, so the rate takes the peak across this window rather than
# the matched bucket alone. Deliberately short: INCA's hourly `RR` would be the
# obvious wider source, but it is a *total* over the past hour, and using it as
# an instantaneous rate keeps a shower that ended 40 min ago driving the
# condition. Anything inside this window is still falling.
RATE_LOOKBACK = timedelta(minutes=30)

# How far back the nowcast request reaches, anchored to a bucket boundary.
# Without a `start` the endpoint begins at the bucket covering `now`, so the
# series carries exactly one stamp at or before it -- measured 2026-09-11
# against the live API, 11 buckets per response, ten of them in the future.
# That silently disabled the `RATE_LOOKBACK` peak, which needs more than the
# matched bucket to mean anything. Like `HOURLY_LOOKBACK_HOURS` the anchor is
# what matters: the API rounds a mid-interval `start` *up* to the next stamp,
# so it is floored to the 15-min grid before this is subtracted. One bucket of
# slack past `RATE_LOOKBACK`; the API clamps to the newest run's own t0
# regardless, which sits ~25-35 min back, so asking for more buys nothing.
NOWCAST_LOOKBACK = RATE_LOOKBACK + timedelta(minutes=15)

# Nowcast `rr` buckets carry the millimetres that fell within one 15-min step,
# so an hourly rate is the bucket value times this. Tied to the nowcast
# cadence: a move to 10-min buckets makes it 6.
NOWCAST_BUCKETS_PER_HOUR = 4.0

# Horizon of the AROME hourly forecast (hours). Used to clamp the hourly tool.
AROME_MAX_HOURS = 60

# Hours of history requested alongside the forecast, as margin so the series is
# certain to reach back to the hour already under way. Losing that hour would
# break the outlook's "first entry is the hour under way" contract (see
# outlook.py).
#
# What actually protects it is the *anchor*, not this margin. An unbounded
# request begins well after the current hour (measured 2026-08-12 05:54Z:
# first stamp 07:00), so `start` has to be named. The API then honours a
# `start` that lands exactly on a stamp and rounds a mid-hour one *up* to the
# next -- so `start = now` at 19:33 comes back at 20:00 and the in-progress
# hour is gone, while `start = 19:00` comes back at 19:00. Anchoring to the
# top of the hour is therefore load-bearing; this lookback is slack on top of
# it. Assembly drops whatever precedes the cutoff.
HOURLY_LOOKBACK_HOURS = 1

# Forecast-outlook horizons (see outlook.py). The window rounds up to whole
# hourly steps, so an N-hour horizon spans the in-progress hour plus N more.
OUTLOOK_SHORT_HORIZON_HOURS = 1
OUTLOOK_LONG_HORIZON_HOURS = 12

# --- European Air Quality Index ---

# EEA band index (1-6) -> label. GeoSphere's `aqi` parameter is already this
# index, so it is rendered directly with no banding step in between.
AQI_BAND_LABELS = {
    1: "good",
    2: "fair",
    3: "moderate",
    4: "poor",
    5: "very poor",
    6: "extremely poor",
}


def aqi_label(band: int | None) -> str | None:
    """Label for an EEA band index (1-6); None for unknown or out-of-range."""
    if band is None:
        return None
    return AQI_BAND_LABELS.get(int(band))


# --- Shared condition vocabulary ---
# The complete set of Home Assistant condition strings this server can emit,
# spelled as plain literals to keep the package free of homeassistant imports.
# `condition.py` returns these values inline as the branch it takes decides
# them; the names exist so the vocabulary is declared in one place and so
# `outlook.py` can match the lightning prefix without restating it.
CONDITION_SUNNY = "sunny"
CONDITION_CLEAR_NIGHT = "clear-night"
CONDITION_PARTLYCLOUDY = "partlycloudy"
CONDITION_CLOUDY = "cloudy"
CONDITION_FOG = "fog"
CONDITION_RAINY = "rainy"
CONDITION_POURING = "pouring"
CONDITION_SNOWY = "snowy"
CONDITION_SNOWY_RAINY = "snowy-rainy"
CONDITION_LIGHTNING = "lightning"
CONDITION_LIGHTNING_RAINY = "lightning-rainy"
CONDITION_WINDY = "windy"
CONDITION_WINDY_VARIANT = "windy-variant"
