"""Constants for the GeoSphere MCP server.

All dataset ids, parameter lists, thresholds, API URLs, and the WMO weather
code -> Home Assistant condition map live here; never inline these literals.
"""

from __future__ import annotations

# --- GeoSphere Dataset API ---

GEOSPHERE_API_BASE_URL = "https://dataset.api.hub.geosphere.at/v1"
GEOSPHERE_TIMEOUT = 30

# Datasets (mode, resource id). Air-quality / WRF-Chem datasets are out of
# scope for this server and deliberately omitted.
DATASET_AROME = ("forecast", "nwp-v1-1h-2500m")
DATASET_ENSEMBLE = ("forecast", "ensemble-v1-1h-2500m")
DATASET_NOWCAST = ("forecast", "nowcast-v1-15min-1km")
DATASET_INCA = ("historical", "inca-v1-1h-1km")

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
)
# C-LAEF ensemble precipitation percentiles (per-hour amounts, kg m-2; the
# API exposes only p10/p50/p90 — no member counts or true probabilities).
ENSEMBLE_PARAMETERS = ("rr_p10", "rr_p50", "rr_p90")
NOWCAST_PARAMETERS = ("t2m", "td", "rh2m", "rr", "pt", "dd", "ff", "fx")
INCA_PARAMETERS = ("T2M", "TD2M", "RH2M", "RR", "P0", "GL", "UU", "VV")

# How old the newest cached INCA analysis may get before a re-fetch (seconds).
INCA_MAX_AGE_SECONDS = 55 * 60
# INCA analyses trail real time by <1 h; query a window of the last 3 hours.
INCA_LOOKBACK_HOURS = 3

# Stepped precipitation probability from the ensemble rr percentiles: the
# wettest percentile above PRECIP_MIN_MM bounds the share of wet members and
# the midpoint of that range is reported: 95 / 70 / 30 / 0 %.
POP_P10_WET_PCT = 95
POP_P50_WET_PCT = 70
POP_P90_WET_PCT = 30
POP_DRY_PCT = 0

# Condition-derivation thresholds (see condition.py).
THUNDER_CAPE_JKG = 1000.0
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

# Horizon of the AROME hourly forecast (hours). Used to clamp the hourly tool.
AROME_MAX_HOURS = 60

# --- Open-Meteo API (worldwide fallback + always-on daily forecast) ---

OPENMETEO_API_BASE_URL = "https://api.open-meteo.com/v1/forecast"
OPENMETEO_TIMEOUT = 15
# Open-Meteo hourly fallback horizon (hours).
OPENMETEO_MAX_HOURS = 48
OPENMETEO_MAX_DAYS = 16

# Wind comes back in m/s to stay consistent with GeoSphere.
OPENMETEO_WIND_SPEED_UNIT = "ms"

OPENMETEO_CURRENT_VARIABLES = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "dew_point_2m",
    "precipitation",
    "weather_code",
    "cloud_cover",
    "pressure_msl",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "is_day",
)
OPENMETEO_CURRENT_DAILY_VARIABLES = ("sunrise", "sunset")

OPENMETEO_HOURLY_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "precipitation_probability",
    "snowfall",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
)

OPENMETEO_DAILY_VARIABLES = (
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "apparent_temperature_max",
    "apparent_temperature_min",
    "precipitation_sum",
    "snowfall_sum",
    "precipitation_probability_max",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "sunrise",
    "sunset",
    "uv_index_max",
)

# --- Shared condition vocabulary ---
# Home Assistant condition strings, used as plain literals to keep this
# package free of homeassistant imports.
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

# WMO weather code (0-99) -> base HA condition. "sunny" is swapped to
# "clear-night" by the caller using the Open-Meteo `is_day` flag. Codes not
# emitted by Open-Meteo are mapped by their WMO 4677 present-weather meaning
# so every integer 0-99 resolves to a valid condition.
WMO_CONDITION_MAP: dict[int, str] = {
    # 0-3: cloud development (Open-Meteo: 0 clear, 1 mainly clear,
    # 2 partly cloudy, 3 overcast)
    0: CONDITION_SUNNY,
    1: CONDITION_SUNNY,
    2: CONDITION_PARTLYCLOUDY,
    3: CONDITION_CLOUDY,
    # 4-12: haze, smoke, dust, mist
    4: CONDITION_FOG,
    5: CONDITION_FOG,
    6: CONDITION_FOG,
    7: CONDITION_WINDY,
    8: CONDITION_WINDY,
    9: CONDITION_WINDY,
    10: CONDITION_FOG,
    11: CONDITION_FOG,
    12: CONDITION_FOG,
    # 13-19: lightning, precipitation in sight, squalls, thunderstorm
    13: CONDITION_LIGHTNING,
    14: CONDITION_RAINY,
    15: CONDITION_RAINY,
    16: CONDITION_RAINY,
    17: CONDITION_LIGHTNING,
    18: CONDITION_WINDY,
    19: CONDITION_WINDY,
    # 20-29: recent precipitation (within the past hour)
    20: CONDITION_RAINY,
    21: CONDITION_RAINY,
    22: CONDITION_SNOWY,
    23: CONDITION_SNOWY_RAINY,
    24: CONDITION_SNOWY_RAINY,
    25: CONDITION_RAINY,
    26: CONDITION_SNOWY,
    27: CONDITION_POURING,
    28: CONDITION_FOG,
    29: CONDITION_LIGHTNING_RAINY,
    # 30-39: duststorm, sandstorm, blowing/drifting snow
    30: CONDITION_WINDY,
    31: CONDITION_WINDY,
    32: CONDITION_WINDY,
    33: CONDITION_WINDY_VARIANT,
    34: CONDITION_WINDY_VARIANT,
    35: CONDITION_WINDY_VARIANT,
    36: CONDITION_SNOWY,
    37: CONDITION_SNOWY,
    38: CONDITION_SNOWY,
    39: CONDITION_SNOWY,
    # 40-49: fog (Open-Meteo: 45 fog, 48 depositing rime fog)
    40: CONDITION_FOG,
    41: CONDITION_FOG,
    42: CONDITION_FOG,
    43: CONDITION_FOG,
    44: CONDITION_FOG,
    45: CONDITION_FOG,
    46: CONDITION_FOG,
    47: CONDITION_FOG,
    48: CONDITION_FOG,
    49: CONDITION_FOG,
    # 50-59: drizzle (Open-Meteo: 51/53/55 drizzle, 56/57 freezing drizzle)
    50: CONDITION_RAINY,
    51: CONDITION_RAINY,
    52: CONDITION_RAINY,
    53: CONDITION_RAINY,
    54: CONDITION_RAINY,
    55: CONDITION_RAINY,
    56: CONDITION_SNOWY_RAINY,
    57: CONDITION_SNOWY_RAINY,
    58: CONDITION_RAINY,
    59: CONDITION_RAINY,
    # 60-69: rain (Open-Meteo: 61/63/65 rain, 66/67 freezing rain)
    60: CONDITION_RAINY,
    61: CONDITION_RAINY,
    62: CONDITION_RAINY,
    63: CONDITION_RAINY,
    64: CONDITION_POURING,
    65: CONDITION_POURING,
    66: CONDITION_SNOWY_RAINY,
    67: CONDITION_SNOWY_RAINY,
    68: CONDITION_SNOWY_RAINY,
    69: CONDITION_SNOWY_RAINY,
    # 70-79: solid precipitation (Open-Meteo: 71/73/75 snowfall,
    # 77 snow grains)
    70: CONDITION_SNOWY,
    71: CONDITION_SNOWY,
    72: CONDITION_SNOWY,
    73: CONDITION_SNOWY,
    74: CONDITION_SNOWY,
    75: CONDITION_SNOWY,
    76: CONDITION_SNOWY,
    77: CONDITION_SNOWY,
    78: CONDITION_SNOWY,
    79: CONDITION_SNOWY_RAINY,
    # 80-89: showers (Open-Meteo: 80/81/82 rain showers,
    # 85/86 snow showers)
    80: CONDITION_RAINY,
    81: CONDITION_RAINY,
    82: CONDITION_POURING,
    83: CONDITION_SNOWY_RAINY,
    84: CONDITION_SNOWY_RAINY,
    85: CONDITION_SNOWY,
    86: CONDITION_SNOWY,
    87: CONDITION_SNOWY_RAINY,
    88: CONDITION_SNOWY_RAINY,
    89: CONDITION_POURING,
    # 90-99: thunderstorm (Open-Meteo: 95 thunderstorm,
    # 96/99 thunderstorm with hail)
    90: CONDITION_LIGHTNING_RAINY,
    91: CONDITION_LIGHTNING_RAINY,
    92: CONDITION_LIGHTNING_RAINY,
    93: CONDITION_LIGHTNING_RAINY,
    94: CONDITION_LIGHTNING_RAINY,
    95: CONDITION_LIGHTNING,
    96: CONDITION_LIGHTNING_RAINY,
    97: CONDITION_LIGHTNING_RAINY,
    98: CONDITION_LIGHTNING_RAINY,
    99: CONDITION_LIGHTNING_RAINY,
}


def wmo_to_condition(code: int | None, *, night: bool = False) -> str | None:
    """Map an Open-Meteo WMO weather code to an HA condition string.

    Returns ``clear-night`` in place of ``sunny`` when ``night`` is True.
    Unknown / out-of-range codes return None.
    """
    if code is None:
        return None
    condition = WMO_CONDITION_MAP.get(int(code))
    if condition == CONDITION_SUNNY and night:
        return CONDITION_CLEAR_NIGHT
    return condition
