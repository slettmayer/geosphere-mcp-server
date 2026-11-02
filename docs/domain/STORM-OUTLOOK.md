# Storm Outlook

## Purpose
Documents the severe-weather outlook: what `get_storm_outlook` reports, the window semantics behind every
figure, and how a thunderstorm hour is decided.

## Responsibilities
- Specifying the derivations in `outlook.py` and the horizons they use
- Documenting the round-up window and the "storm in progress" timestamp
- Explaining the two thunderstorm signals, the ensemble's 12 h age rule, and the `signal` attribution

## Non-Responsibilities
- The `sy` code table and `is_thunder_symbol` — see [CONDITION-DERIVATION.md](CONDITION-DERIVATION.md)
- Which dataset supplies the symbol, gusts, CAPE and the ensemble percentiles, and how each is stamped —
  see [DATA-SOURCES-AND-COVERAGE.md](DATA-SOURCES-AND-COVERAGE.md)
- The rendered line format — see [OUTPUT-CONTRACT.md](OUTPUT-CONTRACT.md)

## Overview

The outlook answers the two questions an hourly table makes a caller work for: *how hard will it blow* and
*when is the next thunderstorm*. It is deliberately **threshold-free** for gusts — it reports figures and
never a severity verdict, because what counts as dangerous depends on whether the caller is closing a
skylight or cancelling a flight.

### What Counts As a Thunderstorm Hour

Simple by design, and the same rule as `ha-geosphere-next` 0.16.0. An hour is a thunderstorm hour when
**either** signal fires (`outlook._storm_signals`):

- **Symbol**: the row's `sy` is a thunderstorm code (26–32) — GeoSphere's own verdict for the
  deterministic run. The row reads `sy` from the next stamp, so the code describes the row's own hour.
- **Ensemble**: the ensemble is current, and the row's `ensemble_cape_p90_jkg` ≥ `THUNDER_CAPE_JKG`
  (1000 J/kg) **and** its `ensemble_precipitation_p90_mm` ≥ `PRECIP_MIN_MM` (0.1 mm). So at least a tenth
  of the C-LAEF members reach 1000 J/kg of CAPE, and at least a tenth reach 0.1 mm of precipitation — not
  necessarily the same members. `cape_p90` is instantaneous and read at the row's own stamp; `tp_p90` covers the row's
  hour. Requiring precipitation keeps a dry high-CAPE afternoon from raising a signal.

**Ensemble current** is one rule in one helper, `outlook.ensemble_is_current`: the ensemble run's
`reference_time` is less than `ENSEMBLE_MAX_AGE` (12 h) before now. No ensemble run, or one without a
stamp, is not current — it fails closed, and the outlook then rests on the symbol alone. Twelve hours
covers GeoSphere's publication lag (the newest ensemble run in service is routinely 6–9 h old) plus a
missed rerun. There is no other staleness machinery: the server is stateless and always fetches the newest
run.

The deterministic `cape` decides nothing. It is reported as `Max CAPE next 12 h`, a plain diagnostic.
GeoSphere's thunder codes fire at far lower CAPE than 1000 J/kg (median ~160 J/kg in `ha-geosphere-next`'s
grid-wide sample), so in practice the symbol is the season-round signal and the ensemble adds high-CAPE
summer situations the deterministic run does not convect on.

Every answer names its **signal** — `symbol`, `ensemble` or `both` — rather than a CAPE figure, so a caller
can tell GeoSphere's own thunderstorm forecast from a member-spread possibility.

### The Derivations

`window(rows, hours, now)` slices the series once; the window readers below take the slice rather than
re-deriving it. `format._outlook` decides `ensemble_is_current` once and hands the same flag to both
thunderstorm readers, so they cannot disagree about it.

| Function | Reports | Input |
|----------|---------|-------|
| `max_gust` | Peak gust (m/s) and the hour it falls in | A window |
| `max_cape` | Peak deterministic CAPE (J/kg), diagnostic only | A window |
| `thunderstorm_outlook` | Tri-state `True` / `False` / `None`, plus the signal across the window | A window |
| `next_thunderstorm` | First storm hour and the signal in that hour | The **whole** series |
| `horizon_hours` | How far ahead the series still reaches | The **whole** series |

Windows are cut at two fixed horizons — `OUTLOOK_SHORT_HORIZON_HOURS` (1 h) and
`OUTLOOK_LONG_HORIZON_HOURS` (12 h).

`next_thunderstorm` deliberately ignores the horizon: "no storm for two days" and "storm in 40 hours" are
both useful answers, and truncating the scan would turn the second into the first. It applies the same
per-row test as `thunderstorm_outlook`, so whenever the 1 h answer is `yes`, the next thunderstorm names a
row inside that window.

### Tri-State, but Only for a Missing Window

`thunderstorm_outlook` returns `None` (unknown) **only when its window holds no forecast hour at all**. An
hour whose symbol is missing or outside GeoSphere's table counts as no storm unless the ensemble says
otherwise: a symbol gap is rare, and blanking the answer on it would make "unknown" the norm rather than
the exception. `next_thunderstorm` has no unknown: with rows ahead and no storm it is an all-clear over
the scanned horizon, and with no rows at all the tool renders `No forecast hours available` instead.

### Window Semantics

Two behaviours look like bugs and are not. Both are inherited from `ha-geosphere-next`, where the same
functions back Home Assistant entities.

**The horizon rounds up to whole hourly steps.** The window starts at the hour already under way — the
forecast series is stamped at the start of each hour, so that hour is stamped up to 59 minutes in the past
and must still count — and ends at `now + hours`. An `hours`-hour window therefore spans `hours + 1`
stamps. A "1 hour" window covers the in-progress hour plus the next one, and can report an event up to
~2 h out. A caller that needs a strict "within the next 60 minutes" answer must compare the returned
timestamps itself.

The bound is `row > now - 1 h`: a row counts until the hour it describes has ended.

**`next_thunderstorm` can return a timestamp in the past**, by up to 59 minutes, when the storm hour is the
one already under way. That is the encoding of "a storm is in progress". Lead-time arithmetic downstream
must clamp a non-positive lead time to zero rather than assume the timestamp is in the future.

### The In-Progress Hour Is Not Free

Every window semantic above rests on the series' first entry being the hour already under way, and that
does **not** come for free. Where an unbounded request begins is the API's choice, so
`async_fetch_hourly_forecast` names an explicit `start`, anchored to the top of the hour and backed off by
`HOURLY_LOOKBACK_HOURS` (1). The anchor is the load-bearing half: the API honours a `start` that lands
exactly on a stamp but rounds a *mid-hour* one up to the next, so anchoring to `now` at 15:30 comes back
starting 16:00 and the in-progress hour is gone — the 1 h window collapses to a single stamp and a storm
happening right now is invisible. The lookback is margin on top of that. Assembly drops whatever precedes
the cutoff.

The row's symbol and precipitation are read *forward* (the next stamp), so the in-progress hour reports
the weather still to come in it rather than the hour that already ended — reading them backwards would let
the outlook call a storm that had just ended.

### An All-Clear Names Its Horizon

`get_storm_outlook` asks for `AROME_MAX_HOURS`, so the scan spans everything the run publishes — nominally
~60 h, but publication lag alone leaves ~52–55 h ahead of now, and a stale or truncated run less. So
`horizon_hours` measures what actually came back and the renderer prints `none in the next {N} h` rather
than an unqualified "forecast horizon". "No storm for 4 h" and "no storm for 60 h" are different claims.

### Degradation and Timestamps

`outlook.py` reads the hourly **row dicts** that `weather.assemble_hourly_forecast` produces. Every row
has a `time`; its values are read through `.get`, so a missing value is judged on what the row does carry.
A failed ensemble fetch leaves the ensemble fields `None` and `ensemble_reference_time` unset, so
the ensemble is not current and the outlook rests on the symbol; the renderer says so in an
`ℹ️ Ensemble signal: unavailable` line.

Rows are aware **UTC** and the derivation runs on them directly; only the *reported* timestamps are
localized to Vienna for display. That ordering is load-bearing: `aware + timedelta` is wall-clock
arithmetic *within* a zone, so deriving in a DST-observing zone would make a 12-hour horizon cover 11 or
13 real hours — on a spring-forward night the peak-gust scan silently loses an hour off the end. UTC has
no transitions, so a timedelta there is a true duration. `outlook.window` documents this as a requirement
on its callers.

## Dependencies
- `outlook.py` depends only on `condition.is_thunder_symbol` and the thresholds in `const.py`
- Needs the AROME `sy`, `10fg` and `cape`, the C-LAEF `cape_p90` and `tp_p90`, and one hour of lookback
- The tool fetches the ensemble alongside AROME (two requests), for its storm signal

## Design Decisions
- **Threshold-free gust reporting**: figures, not verdicts. Severity policy belongs to the caller.
- **GeoSphere's symbol plus one ensemble rule, nothing else**: no CAPE/CIN gate, no cloud corroboration,
  no staleness bookkeeping beyond the 12 h ensemble age. Either signal suffices, because a false alarm
  costs less than a miss.
- **Report the signal, not the reasoning**: `signal: symbol|ensemble|both` instead of a CAPE figure, which
  described the old trigger and no longer decides anything.
- **Fixed 1 h / 12 h horizons** matching the `ha-geosphere-next` entities, so the two projects answer the
  same question the same way.
- **A separate tool rather than a section on the hourly forecast**: an agent asking "should I close the
  skylight" should not have to pull 60 rows, and the hourly output contract stays unchanged.

## Known Risks
- The round-up window means a "1 hour" answer can describe an event nearly two hours out. Documented in the
  rendered output as well as here, because it will otherwise surprise a caller.
- The whole window contract silently depends on the one-hour lookback in `async_fetch_hourly_forecast`.
  Removing it, or re-anchoring it to `now`, breaks every horizon here without failing a type check.
- The ensemble signal pairs two independent 90th percentiles, so it can fire when no single member has both
  the CAPE and the precipitation — a deliberate lean towards false alarms.
- GeoSphere flags thunderstorms at far lower CAPE than the previous 1000 J/kg rule, so the outlook turns on
  more often in convective weather than it used to.
- A missing or unknown symbol counts as no storm; only the one-time log warning shows it.

## Extension Guidelines
- New derivation: add a pure function to `outlook.py` reading the row dict, then surface it through
  `format._outlook`.
- Changing a horizon: edit the constant in `const.py`; the rendered labels are built from it.
- Changing a threshold: edit `const.py`; the rendered note and `test_storm_outlook_description_states_the_thresholds`
  keep the tool description honest.
- Keep `outlook.py` free of I/O and of any source-specific branching.
