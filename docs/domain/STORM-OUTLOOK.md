# Storm Outlook

## Purpose
Documents the severe-weather outlook: what `get_storm_outlook` reports, the window semantics behind every
figure, and how a thunderstorm hour is decided.

## Responsibilities
- Specifying the five derivations in `outlook.py` and the horizons they use
- Documenting the round-up window and the "storm in progress" timestamp
- Explaining the two-branch thunderstorm test and its false-positive guard
- Recording how the outlook degrades on the Open-Meteo path

## Non-Responsibilities
- The thunder gate itself (`is_thunder`) — see [CONDITION-DERIVATION.md](CONDITION-DERIVATION.md)
- Which dataset supplies CAPE, CIN, or gusts — see
  [DATA-SOURCES-AND-COVERAGE.md](DATA-SOURCES-AND-COVERAGE.md)
- The rendered line format — see [OUTPUT-CONTRACT.md](OUTPUT-CONTRACT.md)

## Overview

The outlook answers the two questions an hourly table makes a caller work for: *how hard will it blow* and
*when is the next thunderstorm*. It is deliberately **threshold-free** — it reports figures and never a
severity verdict, because what counts as dangerous depends on whether the caller is closing a skylight or
cancelling a flight.

### The Derivations

`window(rows, hours, now)` slices the series once; the three window readers below take the slice rather
than re-deriving it, so `format._outlook` walks a series three times (two windows plus the full scan)
instead of six.

| Function | Reports | Input |
|----------|---------|-------|
| `max_gust` | Peak gust (m/s) and the hour it falls in | A window |
| `max_cape` | Peak CAPE (J/kg) | A window |
| `thunderstorm_outlook` | Tri-state `True` / `False` / `None` | A window |
| `scan_thunderstorm` | First storm hour, its CAPE, and whether the scan counts | The **whole** series |
| `horizon_hours` | How far ahead the series still reaches | The **whole** series |

Windows are cut at two fixed horizons — `OUTLOOK_SHORT_HORIZON_HOURS` (1 h) and
`OUTLOOK_LONG_HORIZON_HOURS` (12 h).

`scan_thunderstorm` deliberately ignores the horizon: "no storm for two days" and "storm in 40 hours" are
both useful answers, and truncating the scan would turn the second into the first.

### Window Semantics

Two behaviours look like bugs and are not. Both are inherited from `ha-geosphere-next`, where the same
functions back Home Assistant entities.

**The horizon rounds up to whole hourly steps.** The window starts at the top of the *current* hour — the
forecast series is stamped at the top of each hour, so the hour already under way is stamped up to 59
minutes in the past and must still count — and ends at `now + hours`. An `hours`-hour window therefore
spans `hours + 1` stamps. A "1 hour" window covers the in-progress hour plus the next one, and can report
an event up to ~2 h out. A caller that needs a strict "within the next 60 minutes" answer must compare the
returned timestamps itself.

**`scan_thunderstorm` can return a timestamp in the past**, by up to 59 minutes, when the storm hour is the
one already under way. That is the encoding of "a storm is in progress". Lead-time arithmetic downstream
must clamp a non-positive lead time to zero rather than assume the timestamp is in the future.

### What Counts As a Thunderstorm Hour

`_is_lightning` accepts an hour on either of two branches:

1. **The derived condition starts with `lightning`** — the model's own judgement that convection is
   occurring, and the primary signal.
2. **The raw CAPE/CIN predicate holds *and* the hour is forecast to produce precipitation** (>=
   `PRECIP_MIN_MM`).

The second branch exists because the condition string alone misses real storms: `derive_condition` returns
`snowy` / `snowy-rainy` before it ever looks at thunder, so thundersnow reads as plain snow, and it returns
`None` when cloud cover is missing, so a data gap reads as calm.

That branch requires precipitation as a **false-positive guard**. CAPE >= 1000 J/kg with weak inhibition
and no forecast rain is a routine summer afternoon in Vienna; without precipitation the model is not saying
convection is happening.

### The In-Progress Hour Is Not Free

Every window semantic above rests on the series' first entry being the hour already under way. That does
**not** come for free: the GeoSphere API trims the forecast to the current hour, and
`assemble_hourly_forecast` skips the first step because an accumulated parameter has no predecessor to
difference against. Left alone, those two facts cancel the in-progress hour out entirely — the 1 h window
collapses to a single stamp and a storm happening right now is invisible.

`async_fetch_hourly_forecast` therefore requests `HOURLY_LOOKBACK_HOURS` (1) of history, so the current
hour arrives with a predecessor and a real precipitation delta. The lookback is anchored to the **top of
the hour**, not to `now`: the API rounds `start` up to the next whole stamp, so `now - 1h` at 15:30 comes
back as 15:00 and the problem returns unchanged.

### Tri-State Answers

`thunderstorm_outlook` returns `None` — not `False` — when its window holds no hour that can be judged. An
hour is *decidable* when it carries either a derived condition or a CAPE value; an hour with neither says
nothing about thunder, and answering "no storm" there would be a guess rather than an answer. This makes a
data gap distinguishable from genuine calm.

`scan_thunderstorm` needs the same distinction and returns it as a third element, because the first two
are `(None, None)` both for "no storm in the horizon" and for "nothing here can be read". The renderer
consults it before printing an all-clear — otherwise a response could declare the window `unknown` on one
line and assert a confident 60-hour all-clear on the next. It comes out of the *same* pass as the storm
hour rather than a second one, so the two answers cannot drift apart: an hour the scan just judged as a
storm is by construction an hour it counts as readable.

### An All-Clear Names Its Horizon

The two paths hand the outlook series of quite different lengths — AROME runs ~60 h, while the Open-Meteo
fallback counts forecast days from *local midnight*, so 48 h requested at 20:00 leaves only ~28 h ahead.
`get_storm_outlook` therefore asks the fallback for `OUTLOOK_FALLBACK_HOURS` (72 h, three days), which
keeps at least 48 h ahead at any hour of the day, and `horizon_hours` measures what actually came back so
the renderer can print `none in the next {N} h` rather than an unqualified "forecast horizon".

### Both Source Paths, One Implementation

`outlook.py` reads the hourly **row dicts** that `weather.assemble_hourly_forecast` and
`format.openmeteo_hourly_rows` both produce, so the same functions serve GeoSphere and Open-Meteo. Rows are
read through `.get`, so a source that omits a key degrades rather than raising.

On the Open-Meteo path the outlook is two variables (`cape`, `convective_inhibition`) added to the
existing hourly request — no extra call. **The two sources report inhibition with opposite signs**: AROME
publishes it negative (`0.0` uncapped, more negative a stronger lid) and Open-Meteo a positive magnitude,
so `openmeteo_hourly_rows` negates the value to match the convention `is_thunder` expects. Inverting that
would turn the gate inside out — suppressing exactly the storms it should pass.

A missing inhibition value still reads as uncapped, so a point where Open-Meteo returns nulls degrades to
CAPE-only gating for those hours rather than failing.

Timestamps differ between paths: GeoSphere rows are aware UTC and Open-Meteo rows are naive local. The
normalizer in `format.py` converts `now` into whichever convention its rows use before any comparison, and
only the *reported* timestamps are localized for display.

## Dependencies
- `outlook.py` depends only on `condition.is_thunder` and `PRECIP_MIN_MM` from `const.py`
- The GeoSphere path needs AROME `cape` and `cin` plus one hour of lookback; the Open-Meteo path needs the
  `cape` and `convective_inhibition` hourly variables
- The tool fetches AROME **without** the C-LAEF ensemble — the outlook reports no precipitation
  probability, so that request would be wasted

## Design Decisions
- **Threshold-free reporting**: figures, not verdicts. Severity policy belongs to the caller.
- **Fixed 1 h / 12 h horizons** matching the `ha-geosphere-next` entities, so the two projects answer the
  same question the same way.
- **A separate tool rather than a section on the hourly forecast**: an agent asking "should I close the
  skylight" should not have to pull 60 rows, and the hourly output contract stays unchanged.
- **Tri-state instead of boolean**: "unknown" is a real answer and must not masquerade as "no" — which is
  why the storm-scan result is paired with a decidability check rather than reported on its own.

## Known Risks
- The round-up window means a "1 hour" answer can describe an event nearly two hours out. Documented in the
  rendered output as well as here, because it will otherwise surprise a caller.
- The whole window contract silently depends on the one-hour lookback in `async_fetch_hourly_forecast`.
  Removing it, or re-anchoring it to `now`, breaks every horizon here without failing a type check.
- On the Open-Meteo fallback the scanned horizon is shorter than GeoSphere's ~60 h (Open-Meteo's
  `forecast_days` counts from local midnight, so it shrinks as the day advances) and the output does not
  state how far it looked.
- The sign flip on Open-Meteo's inhibition is invisible in the output; a regression there would silently
  invert the thunder gate rather than raise anything. `test_openmeteo_rows_negate_the_inhibition_sign`
  and `test_openmeteo_capped_hour_is_not_a_storm` are the only guards.
- The precipitation guard on branch 2 will miss a genuinely dry thunderstorm — rare, and the alternative is
  a storm signal on most summer afternoons.

## Extension Guidelines
- New derivation: add a pure function to `outlook.py` reading the row dict, then surface it through
  `format._outlook` so both source paths get it at once.
- Changing a horizon: edit the constant in `const.py`; the rendered labels are built from it.
- Keep `outlook.py` free of I/O and of any source-specific branching.
