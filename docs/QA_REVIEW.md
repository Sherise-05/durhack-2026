# Independent QA review — 15 November 2026

**Repair status: all 13 findings below have been addressed.** The original cases are now part of the regular test suite in `tests/test_qa_acceptance.py`; the fixes also have dedicated planner, simulation, mode and time-validation tests. Findings below describe the original failures and their repair rationale, rather than unresolved defects.

## Current changes

- Infeasible schedules expose `feasible=False` and render dashboard curves without constructing invalid sleep blocks. Passed alarm deadlines are explained.
- Destination bright-light windows are withheld throughout post-flight adjustment until a phase-aware algorithm exists; pre-trip home windows remain.
- Duration, ordering and simulation grids use actual instants with a deterministic Europe/London default for legacy naive inputs. DST folds survive offset-bearing JSON storage. Profile clocks require local times without offsets; physiological measurements must be finite; trip chronology is checked in UTC.
- Observed sleep takes precedence over inferred fallback intervals. What-if scenarios replace future sleep while preserving observed history. Exact sleep-onset boundaries are covered too.
- Daily charts retain the morning after naps, short events receive forecasts and proposed sleep/event conflicts are warned about.
- Wellbeing prompts require distinct consecutive dates; holiday alarms use observed wake drift; teen guidance and recovery-sleep wording have been corrected.

Run `venv/bin/python -m pytest -q` for the complete suite. The baseline before these repairs was 125 passing tests; the original ten extra acceptance cases all failed then. Final validation: **196 tests passed**, including all ten original acceptance cases. Dashboard, bedtime, curve and nap calls also completed without exceptions across **648 persona/date/time combinations**, covering normal dates and both UK clock-change dates.

No commits or pushes were made. Routes, templates and database services remain placeholders. Test success checks implementation behaviour and does not establish clinical accuracy.

## Original QA findings

P1 = high-priority correctness issue; P2 = material functional/factual issue; P3 = wording correction. These are review priorities, not claims of clinical risk assessment.

## Findings

### QA-01 — P1: impossible schedules crash the dashboard curve

Location: `app/services/planner.py:128` (construction), `:249` (infeasible result).

A 23:00–09:00 night shift checked at 20:00 produces bedtime 09:15, alarm 07:30 and zero sleep. The recommendation includes a warning, but `day_curve()` still constructs `SleepBlock(bedtime, alarm)`, which raises `ValueError: a sleep block must last more than zero and at most 24 hours`. A 03:00 flight checked at 03:00 similarly leaves a past 02:15 alarm and crashes the curve.

Change: explicitly represent an infeasible plan, render a curve without a planned sleep block, and separately handle already-passed deadlines. Test dashboard/curve generation after generating infeasible recommendations; checking only recommendation warnings is insufficient.

Probe: `test_dashboard_handles_no_feasible_sleep`.

### QA-02 — P1: fixed jet-lag light windows can point in the wrong direction

Location: `app/services/modes.py:185`, `:252`.

Destination light windows use the target local wake time, regardless of residual body-clock lag. With usual wake 07:30, London–Tokyo and three preparation nights, the landing plan says six hours behind yet prescribes light at 07:30–09:30. Under the plan's own lag assumption, biological wake is approximately 13:30 destination time and the circadian nadir roughly 09:30–11:30. Much of the prescribed window precedes the nadir, when light can delay rather than advance the clock.

The window/nadir comparison is an inference from the model's assumptions, not a measured individual phase. The timing dependence itself is established: light after the nadir promotes advances, and timing must account for the zones crossed. [CDC Yellow Book](https://www.cdc.gov/yellow-book/hcp/travel-air-sea/jet-lag-disorder.html).

Change: derive light timing from an appropriate phase model, or withhold precise windows when phase uncertainty is too large. The general caveat in the summary does not correct a specific contradictory instruction.

### QA-03 — P1: stripping timezone offsets corrupts elapsed sleep across DST

Location: `app/schemas.py:31`, `:108`, `:132`.

In `Europe/London`, both of these valid timestamp pairs are reduced to eight hours:

| Input start | Input end | Actual elapsed | Reported asleep at efficiency 1 |
|---|---|---:|---:|
| 2026-10-24 23:00 +01:00 | 2026-10-25 07:00 +00:00 | 9 h | 8 h |
| 2026-03-28 23:00 +00:00 | 2026-03-29 07:00 +01:00 | 7 h | 8 h |

Actual elapsed time is the difference between the supplied instants. The conversion discards offset/fold information before subtraction. Sleep duration then feeds debt, learning and scores. A span crossing the repeated autumn hour can also be rejected as ending before it starts despite positive real elapsed time.

Change: retain actual instants for duration/ordering calculations, with separate local clock/zone information for circadian and display purposes. The documented global timezone limitation remains open; this is a concrete correctness failure within it.

Probes: both `test_elapsed_sleep_survives_dst` cases.

### QA-04 — P2: what-if alarms do not override conflicting future sleep

Location: `app/services/sleep_model.py:552`, `:223`.

Given an ongoing 23:00–07:00 block and `now=03:00`, simulating sleep 03:10–05:00 reports the requested 05:00 alarm but the curve remains asleep (`None`) through 06:45. The real and hypothetical blocks are simply combined, so the existing block takes precedence until 07:00.

Change: define observed-versus-hypothetical interval precedence. Preserve observations before `now`, and replace/truncate conflicting future portions; reject unsupported retrospective simulations explicitly.

Probe: `test_what_if_alarm_overrides_future_part_of_ongoing_sleep`.

### QA-05 — P2: inferred sleep silently discards a real nap

Location: `app/services/sleep_model.py:511`, `:514`, `:223`.

At noon, an empty-history user and a user with a real 06:00–07:00 nap both get pressure 0.303441842 and zero nap boost. The synthetic fallback night (23:30–07:30) overlaps the real nap and advances beyond it; simulation then skips the nap. Actual input must take precedence over inferred history.

Change: reconcile inferred blocks against observations before simulation. Do not concatenate overlapping synthetic and real intervals.

Probe: `test_inferred_sleep_does_not_discard_real_nap`.

### QA-06 — P2: logging a nap removes the morning from the day chart

Location: `app/services/planner.py:127`.

With a normal night plus a 14:00–14:20 nap and `now=16:00`, the daily chart starts at 14:20. Without the nap, it starts at 07:30. The chart uses the most recent wake instead of the main sleep's wake.

Change: use the main sleep wake for the day-chart origin; retain the most recent wake separately for sleep inertia.

Probe: `test_day_chart_keeps_morning_after_nap`.

### QA-07 — P2: short events silently receive no what-if forecast

Location: `app/services/planner.py:313`.

An exam at 09:02–09:12 after a hypothetical 07:30 wake is absent from `WhatIf.events`. No 15-minute chart point falls within the event. Forecast coverage depends accidentally on chart resolution and alignment.

Change: evaluate event-specific samples or interpolate/integrate at event boundaries. Verify every relevant event is either forecast or explicitly explained as unavailable.

Probe: `test_short_exam_gets_a_forecast`.

### QA-08 — P2: sleeping into a commitment produces no conflict warning

Location: `app/services/planner.py:311`.

For work 07:00–09:00 and hypothetical wake 07:30, the what-if result has neither a work forecast nor any warning. Every event starting before wake is skipped, even when it is still underway.

Change: detect sleep/commitment overlaps before filtering forecasts and explain the conflict. Optionally forecast the remaining attended portion.

Probe: `test_sleeping_into_work_gets_a_warning`.

### QA-09 — P2: seven days of split sleep trigger a false fortnight claim

Location: `app/services/sleep_model.py:465`, `:480`.

Fourteen poor-rated, non-overlapping night records spanning seven days trigger the text saying poor sleep has persisted for two weeks. Reproduction uses 22:00–01:00 and 02:00–07:00 each day. The threshold counts sessions rather than distinct nights/dates.

Change: aggregate distinct sleep days and enforce the intended date coverage/consecutiveness, or accurately describe the number of check-ins without asserting a fortnight.

Probe: `test_seven_days_is_not_two_weeks`.

### QA-10 — P2: holiday alarm progression differs from its explanation

Location: `app/services/modes.py:281`, `:296`, `:301`.

Recent sleep 01:30–10:30, usual sleep 23:30–07:30 and term in five days yield an initial 09:30 alarm. That is one hour earlier than recent actual wake, while the note promises a thirty-minute change. Midpoint drift is 2.5 hours, but wake drift is three hours because the sleep durations differ.

Change: base a promised alarm progression on recent wake times, or explicitly describe midpoint progression instead. Subsequent steps alone do not establish a correct initial step.

Probe: `test_first_holiday_alarm_matches_thirty_minute_promise`.

### QA-11 — P2: teenagers receive adult duration advice

Location: `app/services/modes.py:83`, consumed by `app/services/planner.py:117`.

A teen snapshot estimates need at nine hours but includes the generic tip recommending at least seven hours. That tip is not age-appropriate: guidance for teens is eight to ten hours. [NIH/AASM guidance](https://www.nhlbi.nih.gov/health/sleep-deprivation/how-much-sleep).

Change: render sleep-duration guidance by age band and test the text in snapshots, not only the numerical planning minimum.

### QA-12 — P2: accepted input can crash downstream calculations

Location: `app/schemas.py:121`, `:165`, `:200`.

- `usual_wake='07:30:00Z'` is accepted, but `recommend_bedtime()` raises a naive/aware datetime `TypeError`.
- Infinite `hrv_ms` values are accepted; four such records crash `PersonModel` in `statistics.pstdev` with `AttributeError: 'float' object has no attribute 'numerator'`.
- A trip arriving six days before departure is accepted. Chronology must be checked using instants in the home/destination zones; simply comparing naive wall clocks would wrongly reject valid westbound trips.

Change: validate or deliberately normalise timezone-bearing profile clocks, require finite numeric measurements, and validate trip chronology in a shared instant representation. Return input errors at the boundary rather than internal calculation exceptions.

### QA-13 — P3: the holiday tip overstates recovery-sleep evidence

Location: `app/services/modes.py:104`.

The tip says big lie-ins do not repay a week of short nights. The cited Depner trial found weekend recovery did not prevent metabolic dysregulation when insufficient sleep resumed; that does not establish that extra sleep cannot repay any deficit or provide benefits. [Original trial](https://pubmed.ncbi.nlm.nih.gov/30827911/).

Change to: “Weekend catch-up sleep may not undo all the effects of repeated short nights; regular sufficient sleep matters.”

## Scoring policy clarification

Nap utility intentionally measures available awake time. Candidate utility excludes the entire lying-down period, including estimated sleep latency; the no-nap baseline remains available during that time. This opportunity-cost policy is now explicit in the module documentation. The scoring behaviour was retained rather than changing a product choice under the label of a mathematical repair.

## Repair order and acceptance gate

1. Prevent infeasible-plan chart crashes and unsupported accepted-input crashes.
2. Resolve instant/zone duration handling and the specific light-timing contradiction.
3. Define precedence for observed, inferred and hypothetical sleep.
4. Fix chart/event coverage and date-versus-session thresholds.
5. Correct age-specific guidance and remaining misleading explanations.

The existing 125 baseline tests and the new acceptance cases are retained. Extend rendered-output checks to teenagers, split sleep, short events, late checks of early flights, impossible schedules and DST. The passing persona smoke checks were useful, but did not exercise these boundary conditions.

The research-informed numerical weights, fixed physiology in what-if forecasts and missing application integration remain disclosed limitations. They are not newly discovered code defects in this report, and passing the repaired tests would still not establish clinical accuracy.
