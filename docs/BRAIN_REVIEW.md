# Brain review — 15 November 2026

## Repair status

Initial repair batch (before independent QA): **125 tests passed**. Dashboard, bedtime, curve and nap calls also completed without exceptions across **216 persona/date/time combinations**. These checks validate software behaviour, not clinical prediction accuracy.

The eight reproduced bugs below are now covered by passing regression tests in `tests/test_brain_regressions.py`. The shared jet-lag calculation and `planned_from` field have been implemented directly, preserving the pending check-in and wording fixes. Additional repairs cover overnight fallback, recent debt/HRV windows, circular clock averaging, nap duration/slot boundaries, event-weight ordering, travel-field conversion and input validation. Scientific overclaims in nap/exam explanations were softened.

See [SCIENCE_AND_FEATURES.md](SCIENCE_AND_FEATURES.md) for the current feature guide, research sources, assumptions and remaining integration limits. Subsequent QA repairs are tracked in [QA_REVIEW.md](QA_REVIEW.md). DST arithmetic/storage now preserve instants with an explicit UK default; global per-user timezone support and persistence wiring still require integration work. Frozen future physiology remains a modelling limitation.

The following is the **original audit snapshot before repair**, retained to explain the bugs and reproduction cases. Its failing-test counts and unapplied-patch statements describe that earlier snapshot, not the current implementation.


Scope: all nonempty Python source and tests, schemas, README, persona data and generator; checked the remaining application files for implementation. This is a software logic audit, not a clinical validation or independent verification of the science brief (the brief is not in this repository).

## Results and repository state

- `venv/bin/python -m pytest -q`: **98 passed** with the pending check-in, bedtime-limit and wording changes.
- Eight additional behavioural probes reproduce failures the suite misses. They are intentionally outside the default test collection; see `docs/brain_regression_probes.py`.
- Run them from the repository root with `PYTHONPATH=tests:. venv/bin/python -m pytest -q docs/brain_regression_probes.py`. Current result: **8 failed**. These failures document unresolved bugs, not new production changes.
- No additional production code was changed during this review. The previously requested changes remain uncommitted. The jet-lag patch remains unapplied; nothing was pushed.
- `app/main.py`, all page modules, all templates, CSS/JS and `tiger_data.py` are empty. End-to-end routes, persistence, trip creation and UI behaviour cannot yet be tested here.

## Fix first: calendar constraints

### 1. Sleep can overlap a known commitment

Location: `app/services/planner.py:231`.

Only an event containing the initial bedtime is considered. A 23:30–02:00 shift is ignored when bedtime is 22:30: the proposed sleep overlaps the shift. There is also no second check after moving bedtime: an event ending 23:00 moves lights-out to 23:15, even if another event runs 23:10–01:00.

Repair: evaluate commitments across the proposed sleep interval, including boundaries, and repeat conflict resolution until stable. Define what happens when no feasible sleep interval remains; do not return an unexplained bedtime after the alarm. Include travel/home-settling time consistently.

Probes: `test_bedtime_does_not_overlap_a_later_commitment`, `test_moving_bedtime_rechecks_calendar`.

### 2. Wake-up deadlines use only the first event's start time

Locations: `app/services/planner.py:79`, `app/services/planner.py:161`.

A 09:00 remote appointment followed by a 09:30 appointment with 150 minutes of travel returns a 07:30 alarm, although the latter requires 06:15 with the default 45-minute routine. Events starting before 04:00 are excluded entirely: a 03:00 flight still produces 07:30.

Repair: compute preparation/travel deadlines for all relevant commitments and select the earliest binding deadline. Include overnight and very early travel. Separately flag mutually incompatible events rather than implying that waking earlier solves them.

Probes: `test_alarm_accounts_for_later_event_travel`, `test_three_am_flight_is_not_ignored`.

## Fix next: sleep accounting

### 3. Debt ignores today's afternoon nap; duplicates can erase debt

Location: `app/services/sleep_model.py:352`.

Sleep totals are grouped at 14:00, but only keys containing a night session are processed. A 6-hour night produces 2 hours of debt; a 14:00–15:00 nap leaves it at 2 hours at 16:00. The nap can count later, once the next night has been logged, so this is delayed credit rather than universal omission.

Logging the same 6-hour night twice changes debt from 2 to 0. Pressure simulation clips overlaps, but debt, learning, nightly stats and HRV do not share that normalisation.

Repair: establish one duplicate/overlap policy for incoming sleep records and apply it consistently before all calculations. Credit completed naps against outstanding debt without charging a full new night's need to a partial day. Missing logs must stay distinguishable from confirmed no-sleep nights.

Probes: `test_afternoon_nap_reduces_existing_debt`, `test_duplicate_log_does_not_erase_debt`.

### 4. Current sleep is discarded by future `state_at()` calls

Location: `app/services/sleep_model.py:509`.

For a recorded 23:00–07:00 session and `now=03:00`, the model is asleep. `state_at(04:00)` nevertheless says awake, while `curve(04:00, 04:00)` returns no alertness because it correctly replays the ongoing block. `state_at()` advances with an empty block list.

Repair: preserve the remainder of sleep already underway, and make point predictions and curves use the same forecast history. Avoid recommendations to nap while already asleep.

Probe: `test_current_sleep_state_agrees_with_curve`.

## Then finish travel

### 5. The pre-trip plan resets earlier progress

Locations: `app/services/modes.py:228`, `app/services/sleep_model.py:291`.

The plan filters out past preparation nights before numbering its shifts. For the same Tokyo trip, checking day 0 gives 8 recovery days; checking day 2 gives 11, despite no change in the trip. Meanwhile the model continues assuming three hours of preparation.

The supplied patch's shared night calculation and stored `planned_from` address this. Apply the changes to the current working files, preserving the wording edits, instead of repeating the failing three-way application. Add the proposed agreement/progress tests.

Integration requirement: set `planned_from` once when a real trip is created, persist it, and preserve it on edits. `None` is a legacy/demo assumption of full preparation, not evidence of actual adherence. There is no implemented trip form or persistence service here to wire up yet. Do not claim the user followed a plan merely because the plan existed.

Probe: `test_jet_plan_retains_completed_preparation`.

### 6. Travel datetime conventions conflict with aware input

Location: `app/schemas.py:26`, `TravelPlan`.

All aware datetimes are converted to the host's local timezone and stripped of timezone information. Travel comments instead promise departure in home time and arrival in destination time. On this host, `2026-11-20 10:00 Asia/Tokyo` becomes naive `2026-11-20 01:00`; the model subsequently treats that as Tokyo local time.

Repair: choose a consistent time contract with the integration owner. Prefer storing actual instants plus named user/trip zones and converting for display/calculation. At minimum, use field-specific home/destination conversion for travel rather than server-local conversion. Avoid comparing home and destination naive clocks to decide whether the flight has landed, especially for westbound travel.

## Other findings and modelling limitations

- **No-history overnight state:** `_assumed_last_night()` always selects a completed usual night. At 03:00, an empty-history user is treated as awake since yesterday at 07:30, with pressure about 0.694. At 08:00 the inferred pressure abruptly falls to about 0.132. Choose whether to infer the current usual sleep interval or represent sleep status as unknown.
- **Sparse history:** the debt window is the last 14 logged night keys, not the last 14 calendar nights. A single short night 30 days ago still contributes 2 hours. Decide whether this is intentional; expose missing data rather than treating it as a complete recent window. HRV similarly uses available records, potentially much older than a week.
- **Circular body-clock averaging:** the noon unwrap works for ordinary night sleepers, but observations on opposite sides of that boundary can give misleading results for daytime sleep. With usual sleep 08:00–16:00 and two observed sleeps 08:00–16:00 and 13:00–21:00, the posterior midpoint becomes 08:01, earlier than every input midpoint (12:00 and 17:00). Use circular residuals relative to a common reference if shift workers are supported.
- **Nap minutes differ from simulated minutes asleep:** the alarm is onset + 10/20/90 minutes, then `NAP_EFFICIENCY=0.95` is applied, so a stated 90 minutes asleep simulates 85.5. Decide whether lengths mean elapsed nap time or actual sleep and keep output labels consistent.
- **Forecast physiology is frozen:** curves use today's body-clock phase, fatigue and debt even after hypothetical recovery sleep or across travel adaptation. Useful as a short-term heuristic, but the what-if simulator does not fully update tomorrow's physiology.
- **Input validation:** invalid timezone names are accepted by the schema and crash `ZoneInfo` later. There is no plausible maximum sleep duration; an accepted 199-day session produces a division-by-zero at onset reconstruction because the exponential underflows. Validate at ingestion, or reject unsupported input explicitly.
- **Overlapping event weights:** nap scoring uses the first matching event, so input ordering can determine whether an overlapping exam receives its intended higher weight. Define a consistent maximum/priority policy.
- **Slot boundaries:** `best_nap_slot()` rounds seconds down and includes 15:00, so a slot can start just before `now` or at the older-user cutoff. Treat this as lower priority than the above correctness bugs.
- **Claims in persona generator:** `data/make_personas.py` still describes ratings generated as continuous soft labels and says learning recovers true needs. Direction-only learning changes that relationship. Synthetic personas generated from the same assumed model demonstrate behaviour, not real-data validation.

## What is sound in the current implementation

- Pressure update functions correctly implement the exponential equations documented in the module and compose consistently when a sleep block is split across simulation steps (checked directly).
- Need learning uses log probabilities and max-subtraction normalisation, avoiding ordinary posterior-weight underflow.
- The pending direction-only change excludes neutral observations from both updates and evidence counts. In direct probes, seven 5-hour nights rated 1 or 2 give 8.02 hours, neutral gives 8.00 with zero observations, and ratings 4 or 5 give 7.10. It fixes the direction issue; it does not establish that ratings identify biological need.
- The pending bedtime-limit fix consistently converts learned need to time in bed before subtracting half from the midpoint.
- Completed-history filtering is used by `personalise()`, and pressure simulation and night display distinguish naps from night sleep.
- Existing tests cover age ranges, mode behaviour, nap option restrictions, warnings, persona outputs and basic model trends. They remain useful, but do not cover the reproduced boundary and integration cases.

## Recommended work split

Your brain work: calendar feasibility/deadlines; consistent sleep accounting; simulation consistency; shared jet-lag progress; regression tests for those behaviours. Keep tuned constants and the deep-sleep curve unchanged while repairing correctness.

Agree with the integration teammate: sleep record identities/upserts and overlap handling; timestamps/timezones; setting and preserving trip creation dates; explicit handling of missing data; stable JSON output contracts. The routes/database implementation belongs with that integration work.

Suggested sequence: (1) calendar regressions and fixes; (2) debt/session consistency and current-sleep forecasts; (3) jet-lag patch and its tests; (4) timezone contract with the teammate; (5) lower-priority refinements. Use separate reviewable commits. Do not push the current state merely because the original 98 tests pass.
