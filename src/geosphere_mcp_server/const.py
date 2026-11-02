"""Constants for the GeoSphere MCP server.

All dataset ids, parameter lists, thresholds, and API URLs live here; never
inline these literals.
"""

from __future__ import annotations

from datetime import timedelta

# --- GeoSphere Dataset API ---

GEOSPHERE_API_BASE_URL = "https://dataset.api.hub.geosphere.at/v1"
GEOSPHERE_TIMEOUT = 30
# Retry a rate-limited request once when the API asks to wait no longer than
# this (seconds); otherwise surface the limit to the caller immediately.
RATE_LIMIT_RETRY_MAX_S = 5.0
# GeoSphere covers Austria + the Alpine region, all within CET/CEST. Used to
# render its UTC stamps and to match its daily values to a calendar day.
GEOSPHERE_TZ = "Europe/Vienna"

# Datasets (mode, resource id). `AROME` names the deterministic forecast source
# throughout the code; the dataset itself is the control run of the C-LAEF
# AlpeAdria ensemble on a 1 km grid, and `DATASET_ENSEMBLE` is that ensemble.
DATASET_AROME = ("forecast", "nwp-v2-1h-1km")
DATASET_ENSEMBLE = ("forecast", "ensemble-v2-1h-1km")
DATASET_NOWCAST = ("forecast", "nowcast-v1-15min-1km")
DATASET_INCA = ("historical", "inca-v1-1h-1km")
DATASET_CHEM = ("forecast", "chem-v2-1h-3km")
DATASET_CHEM_AQI = ("forecast", "chem_aqi-v1-1d-3km")

# Deterministic forecast parameters. Two stampings, classified by checking
# index 0 of a request that starts at the run's t0 (null there means the value
# covers the interval *ending* at its stamp; `scripts/record_fixtures.py`
# prints the classification):
#   * instantaneous at the stamp: 2t, 2r, 10u/10v, tcc (%), cape, snowlmt;
#   * the interval ending at the stamp: 10fg (peak gust) and tp (per-hour
#     amount, kg m-2, not run-accumulated).
# `sy` is null at t0 too. Its precipitation classes follow `tp` at the same
# stamp, so it describes the interval ending there and is read one stamp on,
# like `tp`; its cloud classes bin `tcc` at that later stamp -- see
# `weather.assemble_hourly_forecast` and the SY_* groups below.
AROME_PARAMETERS = (
    "2t",
    "2r",
    "10u",
    "10v",
    "10fg",
    "tcc",
    "tp",
    "snowlmt",
    "cape",
    "sy",
)
# C-LAEF ensemble percentiles. The API exposes only p10/p50/p90 -- no member
# counts or true probabilities.
#   * tp_p10/p50/p90: per-hour precipitation amounts (kg m-2). Like the
#     deterministic interval parameters these cover the period *ending* at
#     their stamp, so a percentile is keyed to the preceding stamp to describe
#     the hour a forecast row is stamped for. The cadence is read from the
#     series rather than assumed -- see `weather._ensemble_by_timestamp`.
#   * cape_p90 (J/kg): instantaneous, like the deterministic `cape` -- it has
#     a value at the run's t0 -- so it is read at its own stamp.
ENSEMBLE_PARAMETERS = ("tp_p10", "tp_p50", "tp_p90", "cape_p90")
NOWCAST_PARAMETERS = ("t2m", "rh2m", "rr", "pt", "dd", "ff", "fx")
INCA_PARAMETERS = ("T2M", "RH2M", "RR", "P0", "UU", "VV")
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

# INCA analyses trail real time by <1 h; query a window of the last 3 hours.
INCA_LOOKBACK_HOURS = 3
# How old INCA's hourly `RR` may get before the condition derivation stops
# treating it as evidence about *now* (seconds).
#
# This bound catches a slice that has stopped updating, NOT ordinary lag. INCA
# publishes ~30 min after the hour it analyses and serves the previous slice
# until the next appears, so the freshest `RR` is routinely up to ~90 min old;
# a bound at or below that would flap the condition between `rainy` and the
# symbol once an hour through steady rain. By two hours INCA should have
# published two newer analyses, so a slice this old means its pipeline has
# stalled.
INCA_RR_MAX_AGE_SECONDS = 2 * 60 * 60

# Stepped precipitation probability, an estimate derived here from the
# ensemble tp percentiles -- GeoSphere publishes no probability. A percentile
# above PRECIP_MIN_MM bounds the fraction of wet ensemble members (p10 wet ->
# >=90 %, p50 wet -> >=50 %, p90 wet -> >=10 %); the reported value is the
# midpoint of the implied range: 95 / 70 / 30 / 0 %.
POP_P10_WET_PCT = 95
POP_P50_WET_PCT = 70
POP_P90_WET_PCT = 30
POP_DRY_PCT = 0

# Observed-precipitation thresholds for the current condition (see
# condition.py), and the wet-hour floor shared with the probability and the
# ensemble thunderstorm signal.
PRECIP_MIN_MM = 0.1
# GeoSphere's heavy-rain codes (10, 19) start at 5 mm in the hour, so observed
# rain turns `pouring` where the forecast symbol does.
POURING_MM_PER_H = 5.0
# Rain/snow split when the nowcast `pt` code names no kind (absent, 255
# beside an observed rate, or outside PRECIPITATION_TYPES).
SNOW_MAX_T2M_C = 1.0

# Ensemble thunderstorm signal (see outlook.py): an hour whose C-LAEF 90th
# percentiles reach this CAPE and PRECIP_MIN_MM of precipitation.
THUNDER_CAPE_JKG = 1000.0
# How old a C-LAEF run may be and still feed the thunderstorm outlook
# (`outlook.ensemble_is_current`). GeoSphere publishes the 3-hourly runs well
# after the hour they are stamped for, so the newest run in service is
# routinely 6-9 h old; twelve hours is that lag plus a missed rerun, with
# margin.
ENSEMBLE_MAX_AGE = timedelta(hours=12)
# Which signal called an hour a thunderstorm, as the storm outlook names it.
THUNDER_SOURCE_SYMBOL = "symbol"
THUNDER_SOURCE_ENSEMBLE = "ensemble"
THUNDER_SOURCE_BOTH = "both"

# GeoSphere's `sy` weather-symbol code table, published in German in
# https://github.com/Geosphere-Austria/dataset-api-docs/issues/30#issuecomment-2042539848
# (confirmed there for nwp-v1; nwp-v2 is assumed to follow it, see
# docs/domain/CONDITION-DERIVATION.md). Codes are grouped by the condition
# they map to. 1-2 map to `sunny`, or `clear-night` at night.
SY_CLEAR = frozenset({1, 2})  # Wolkenlos, Heiter
SY_PARTLY_CLOUDY = frozenset({3})  # Wolkig
SY_CLOUDY = frozenset({4, 5})  # Stark bewölkt, Bedeckt
SY_FOG = frozenset({6, 7})  # Bodennebel, Hochnebel
# Leichter / Mäßiger Regen, Regenschauer
SY_RAIN = frozenset({8, 9, 17, 18})
# Starker Regen, Starker Regenschauer
SY_POURING = frozenset({10, 19})
# Schneeregen, Schneeregenschauer
SY_SLEET = frozenset({11, 12, 13, 20, 21, 22})
# Schneefall, Schneeschauer
SY_SNOW = frozenset({14, 15, 16, 23, 24, 25})
# Gewitter, Starkes Gewitter, either with Schneeregen or Schneefall
SY_THUNDER = frozenset(range(26, 33))
# Codes saying precipitation falls in the hour, thunderstorms included.
SY_PRECIPITATION = SY_RAIN | SY_POURING | SY_SLEET | SY_SNOW | SY_THUNDER
# Every published code.
SY_CODES = SY_CLEAR | SY_PARTLY_CLOUDY | SY_CLOUDY | SY_FOG | SY_PRECIPITATION

# Where within a forecast row's hour day/night is judged for the clear
# symbols: the middle, so an hour that is mostly dark reads `clear-night`.
FORECAST_ROW_MIDPOINT = timedelta(minutes=30)

# Nowcast `pt` (precipitation type) code table. Not in the API's `/metadata`;
# confirmed by GeoSphere against the data producer:
# https://github.com/Geosphere-Austria/dataset-api-docs/issues/71#issuecomment-6125846140
# 1/3/5/7 match GRIB2 Code Table 4.201, but 255 means "no precipitation", not
# GRIB2's "Missing". The names are GeoSphere's keys.
PT_RAIN = 1
PT_FREEZING_RAIN = 3
PT_SNOW = 5
PT_RAIN_AND_SNOW = 7
PT_NO_PRECIPITATION = 255
PRECIPITATION_TYPES: dict[int, str] = {
    PT_RAIN: "rain",
    PT_FREEZING_RAIN: "freezing_rain",
    PT_SNOW: "snow",
    PT_RAIN_AND_SNOW: "rain_and_snow",
    PT_NO_PRECIPITATION: "no_precipitation",
}
# How get_current_weather names a kind that is falling; "no_precipitation" has
# no entry, since a dry reading gets no line at all.
FALLING_NOW_LABELS: dict[str, tuple[str, str]] = {
    "rain": ("🌧️", "rain"),
    "freezing_rain": ("🧊", "freezing rain"),
    "snow": ("🌨️", "snow"),
    "rain_and_snow": ("🌨️", "rain and snow"),
}

# How far back the current precipitation rate may look for a wet 15-min
# nowcast bucket. A single bucket can round to 0.0 in the gap between cells of
# an active storm, so the rate takes the peak across this window rather than
# the matched bucket alone. Deliberately short: INCA's hourly `RR` would be the
# obvious wider source, but it is a *total* over the past hour, and using it as
# an instantaneous rate keeps a shower that ended 40 min ago driving the
# condition. Anything inside this window is still falling.
RATE_LOOKBACK = timedelta(minutes=30)

# How far back the nowcast request reaches, anchored to a bucket boundary.
# Without a `start` the endpoint begins at the bucket covering `now`, leaving
# one stamp at or before it, and the `RATE_LOOKBACK` peak needs more than the
# matched bucket. Like `HOURLY_LOOKBACK_HOURS` the anchor is what matters: the
# API rounds a mid-interval `start` *up* to the next stamp, so it is floored to
# the 15-min grid before this is subtracted. One bucket of slack past
# `RATE_LOOKBACK`; the API clamps to the newest run's own t0 regardless, which
# sits ~25-35 min back, so asking for more buys nothing.
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
# What actually protects it is the *anchor*, not this margin. Where an
# unbounded request begins is the API's choice and is not documented, so
# `start` has to be named. The API honours a `start` that lands exactly on a
# stamp and rounds a mid-hour one *up* to the next -- so `start = now` at
# 19:33 comes back at 20:00 and the in-progress hour is gone, while
# `start = 19:00` comes back at 19:00. Anchoring to the top of the hour is
# therefore load-bearing; this lookback is slack on top of it. A `start`
# before the run's t0 comes back at t0. Assembly drops whatever precedes the
# cutoff.
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
# `condition.py` maps the `sy` groups above onto these and returns nothing
# else. HA's `windy`, `windy-variant`, `lightning` (dry thunder), `hail` and
# `exceptional` have no `sy` code, so they never occur.
CONDITION_SUNNY = "sunny"
CONDITION_CLEAR_NIGHT = "clear-night"
CONDITION_PARTLYCLOUDY = "partlycloudy"
CONDITION_CLOUDY = "cloudy"
CONDITION_FOG = "fog"
CONDITION_RAINY = "rainy"
CONDITION_POURING = "pouring"
CONDITION_SNOWY = "snowy"
CONDITION_SNOWY_RAINY = "snowy-rainy"
CONDITION_LIGHTNING_RAINY = "lightning-rainy"
