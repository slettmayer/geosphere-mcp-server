#!/usr/bin/env python3
"""Record live GeoSphere responses as test fixtures.

Not shipped with the package. Run manually from the repository root:

    uv run python scripts/record_fixtures.py [--lat-lon LAT,LON]

Writes to tests/fixtures/:

* ``nwp.json`` / ``ensemble.json`` -- the deterministic forecast and the
  C-LAEF ensemble, each requested from its own run's t0, so index 0 keeps the
  null that marks an interval-ending parameter;
* ``nowcast.json`` / ``inca.json`` -- requested exactly as
  ``weather.async_fetch_current_conditions`` requests them;
* ``manifest.json`` -- the point and the instant everything was recorded at,
  which the tests use as their frozen clock.

It also prints each forecast parameter's stamping: requested from t0, a
parameter that is null at index 0 covers the interval ENDING at its stamp,
one with a value there is instantaneous. A new parameter must be classified
this way before it is wired in -- the metadata's wording is not reliable.

Pörtschach (the default) is a good place to catch rain south of the Alps.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from geosphere_mcp_server.const import (  # noqa: E402
    AROME_PARAMETERS,
    DATASET_AROME,
    DATASET_ENSEMBLE,
    DATASET_INCA,
    DATASET_NOWCAST,
    ENSEMBLE_PARAMETERS,
    GEOSPHERE_API_BASE_URL,
    INCA_LOOKBACK_HOURS,
    INCA_PARAMETERS,
    NOWCAST_LOOKBACK,
    NOWCAST_PARAMETERS,
)

FIXTURES = ROOT / "tests" / "fixtures"


def _stamp(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")


def fetch(
    dataset: tuple[str, str],
    parameters: tuple[str, ...],
    lat_lon: str,
    **bounds: datetime,
) -> dict:
    """GET one point timeseries and return the decoded GeoJSON body."""
    mode, resource_id = dataset
    query = {
        "parameters": ",".join(parameters),
        "lat_lon": lat_lon,
        "output_format": "geojson",
        **{key: _stamp(value) for key, value in bounds.items()},
    }
    url = (
        f"{GEOSPHERE_API_BASE_URL}/timeseries/{mode}/{resource_id}?"
        f"{urllib.parse.urlencode(query)}"
    )
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
        return json.load(response)


def from_t0(dataset: tuple[str, str], parameters: tuple[str, ...], lat_lon: str):
    """The newest run of a forecast dataset, requested from its own t0."""
    probe = fetch(dataset, parameters[:1], lat_lon)
    t0 = datetime.fromisoformat(probe["reference_time"])
    return fetch(dataset, parameters, lat_lon, start=t0)


def classify(name: str, body: dict) -> None:
    """Print each parameter's stamping, judged at index 0 of a t0 request."""
    print(f"{name}: run {body['reference_time']}, {len(body['timestamps'])} stamps")
    for param, series in body["features"][0]["properties"]["parameters"].items():
        first = series["data"][0]
        kind = "interval-ending" if first is None else "instantaneous"
        print(f"  {param:<10} {kind:<16} unit {series.get('unit', '')!r}")


def save(body: dict, name: str) -> None:
    path = FIXTURES / f"{name}.json"
    path.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {path.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lat-lon", default="46.63,14.15")
    args = parser.parse_args()

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    bucket = now.replace(minute=now.minute - now.minute % 15)

    nwp = from_t0(DATASET_AROME, AROME_PARAMETERS, args.lat_lon)
    ensemble = from_t0(DATASET_ENSEMBLE, ENSEMBLE_PARAMETERS, args.lat_lon)
    nowcast = fetch(
        DATASET_NOWCAST,
        NOWCAST_PARAMETERS,
        args.lat_lon,
        start=bucket - NOWCAST_LOOKBACK,
    )
    inca = fetch(
        DATASET_INCA,
        INCA_PARAMETERS,
        args.lat_lon,
        start=now - timedelta(hours=INCA_LOOKBACK_HOURS),
        end=now,
    )

    classify("nwp", nwp)
    classify("ensemble", ensemble)

    FIXTURES.mkdir(parents=True, exist_ok=True)
    latitude, longitude = (float(part) for part in args.lat_lon.split(","))
    save(nwp, "nwp")
    save(ensemble, "ensemble")
    save(nowcast, "nowcast")
    save(inca, "inca")
    save(
        {
            "recorded_at": now.isoformat(),
            "latitude": latitude,
            "longitude": longitude,
        },
        "manifest",
    )


if __name__ == "__main__":
    main()
