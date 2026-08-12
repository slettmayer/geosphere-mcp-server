# Storm Outlook

## Purpose
Documents the severe-weather outlook: what `get_storm_outlook` reports, the window semantics behind every
figure, and how a thunderstorm hour is decided.

## Responsibilities
- Specifying the derivations in `outlook.py` and the horizons they use
- Documenting the round-up window and the "storm in progress" timestamp
- Explaining the two-branch thunderstorm test and its false-positive guard
- Recording how the outlook degrades when AROME leaves a field blank

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

**The horizon rounds up to whole hourly steps.** The window starts at the hour already under way — the
forecast series is stamped at the start of each hour, so that hour is stamped up to 59 minutes in the past
and must still count — and ends at `now + hours`. An `hours`-hour window therefore spans `hours + 1`
stamps. A "1 hour" window covers the in-progress hour plus the next one, and can report an event up to
~2 h out. A caller that needs a strict "within the next 60 minutes" answer must compare the returned
timestamps itself.

The bound is `row > now - 1 h` (`outlook._from`), **not** `now` floored to the top of the hour. AROME does
stamp on whole UTC hours, so flooring would work today — but it fails in the one direction that matters. A
stamp off the hour grid would put the floor *above* the in-progress row and drop it, rendering a storm
already under way as a clean all-clear. The relative form costs nothing and keeps the grid assumption out
of the contract.

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

Every window semantic above rests on the series' first entry being the hour already under way, and that
does **not** come for free. An unbounded request begins well after the current hour, so
`async_fetch_hourly_forecast` names an explicit `start`, anchored to the top of the hour and backed off by
`HOURLY_LOOKBACK_HOURS` (1). The anchor is the load-bearing half: the API honours a `start` that lands
exactly on a stamp but rounds a *mid-hour* one up to the next, so anchoring to `now` at 15:30 comes back
starting 16:00 and the in-progress hour is gone — the 1 h window collapses to a single stamp and a storm
happening right now is invisible. The lookback is margin on top of that. Assembly drops whatever precedes
the cutoff.

The row's precipitation is a *forward* delta (`rr_acc[i+1] - rr_acc[i]`), so the in-progress hour reports
the rain still to fall in it rather than the rain that already fell — which matters here, because
`_is_lightning`'s branch (b) corroborates CAPE with precipitation. Reading it backwards would let the
outlook call a storm that had just ended.

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

`get_storm_outlook` asks for `AROME_MAX_HOURS`, so the scan spans everything AROME publishes — nominally
~60 h. A run that is stale or truncated hands over fewer, so `horizon_hours` measures what actually came
back and the renderer prints `none in the next {N} h` rather than an unqualified "forecast horizon". "No
storm for 4 h" and "no storm for 60 h" are different claims, and only one of them is usually true.

### Degradation and Timestamps

`outlook.py` reads the hourly **row dicts** that `weather.assemble_hourly_forecast` produces, through
`.get` rather than by indexing — so an hour AROME left a field blank is judged on what it does carry
rather than raising. A missing inhibition value reads as uncapped, degrading those hours to CAPE-only
gating.

AROME publishes inhibition **negative** (`0.0` uncapped, more negative a stronger lid), which is the
convention `is_thunder` expects and does not verify. A future source publishing it as a positive magnitude
must be negated before it reaches the gate; getting that backwards turns the gate inside out, suppressing
exactly the storms it should pass.

Rows are aware **UTC** and the derivation runs on them directly; only the *reported* timestamps are
localized to Vienna for display. That ordering is load-bearing: `aware + timedelta` is wall-clock
arithmetic *within* a zone, so deriving in a DST-observing zone would make a 12-hour horizon cover 11 or
13 real hours — on a spring-forward night the peak-gust scan silently loses an hour off the end. UTC has
no transitions, so a timedelta there is a true duration. `outlook.window` documents this as a requirement
on its callers.

## Dependencies
- `outlook.py` depends only on `condition.is_thunder` and `PRECIP_MIN_MM` from `const.py`
- Needs AROME `cape` and `cin` plus one hour of lookback
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
- The inhibition sign convention is invisible in the output; a source or refactor that reversed it would
  silently invert the thunder gate rather than raise anything.
- The precipitation guard on branch 2 will miss a genuinely dry thunderstorm — rare, and the alternative is
  a storm signal on most summer afternoons.

## Extension Guidelines
- New derivation: add a pure function to `outlook.py` reading the row dict, then surface it through
  `format._outlook`.
- Changing a horizon: edit the constant in `const.py`; the rendered labels are built from it.
- Keep `outlook.py` free of I/O and of any source-specific branching.
