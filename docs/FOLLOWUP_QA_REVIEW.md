# Follow-up logic and fact audit — 15 November 2026

## Repair status

All eleven findings below have been addressed in the current working tree. **240 tests pass**, including the ten original follow-up acceptance cases now in `tests/test_followup_acceptance.py`. Additional regressions cover fragmented and ongoing episodes, multi-day holiday adherence, travel buffers, DST midpoint/clock consistency and factual explanations. Dashboard, bedtime, curve and nap calls also completed without exceptions across **648 persona/date/time combinations**, including both UK clock-change dates.

- Shared `primary_nights()` aggregates primary fragments consistently for learning, summaries, HRV and debt; raw intervals still drive physical simulation. Stable episode IDs are supported and complete-episode cutoffs prevent partial/future evidence leakage.
- Known trip departures constrain alarms, with explicit ground-travel and extra check-in/security buffer fields.
- Holiday replanning advances from the latest actual completed wake rather than restarting an older median.
- Completed events no longer bind future sleep; ongoing nights include observed sleep and expose remaining sleep separately.
- Generated DST clocks are resolved before output labels. Date-only mode calls use a documented as-of cutoff; callers can supply explicit `now`.
- Travel light precautions apply to generic tips too. The awareness/alcohol explanations now reflect the cited evidence more carefully.

The backend contract, proposed endpoints and acceptance checklist are in [BACKEND_HANDOFF.md](BACKEND_HANDOFF.md). The application/database adapter and external integrations still need implementation by that team.

Run the regular suite:

```sh
venv/bin/python -m pytest -q
```

## Original audit snapshot

The findings below describe the original review of `b20f1be`. Its existing suite passed 196 tests while the ten additional acceptance cases failed. Original line references identify that reviewed revision. The source has since been repaired; the examples are retained to explain the regression coverage. This is software verification, not clinical validation or an exhaustive proof of correctness.

P1 means high-priority behavioural correctness; P2 means material functionality or factual issue; P3 means lower-priority wording correction.

## Findings

### F01 — P1: a known flight departure can be missed by the alarm

Location: `app/services/planner.py:168`, `:182`.

A Tokyo trip with departure on day 3 at 03:00, usual wake 07:00 and full preparation produces an alarm at **04:00**, `feasible=True` and no warnings when checked the previous evening. `TravelPlan.departure` affects preparation scheduling, but never becomes a required wake deadline unless the caller separately duplicates the flight as a calendar event.

**Change:** turn trip departure into a binding commitment (or enforce a documented ingestion invariant creating the calendar event), with explicit preparation and airport-travel buffers. A recommendation must not call waking after the known departure feasible.

Probe: `test_trip_departure_is_a_wake_deadline_without_duplicate_event`.

### F02 — P1: generic daylight advice bypasses the jet-lag precaution

Location: `app/services/planner.py:240`–243.

The travel plan now withholds destination light windows, but a different output path still instructs jet-lagged users to seek daylight soon after waking to shift their clock earlier. A Tokyo traveller roughly five hours behind local time receives that instruction alongside a body-clock warning after arrival.

Under the model's own residual-lag assumptions, local waking may precede the biological nadir, so this light timing can produce the opposite shift. That phase estimate is an inference, not an individual measurement. Light timing must depend on biological phase, not only local morning. [CDC guidance](https://www.cdc.gov/yellow-book/hcp/travel-air-sea/jet-lag-disorder.html).

**Change:** apply the travel light precaution across every explanation/tip path, not only the explicit shift-plan windows.

Probe: `test_generic_light_tip_does_not_bypass_jetlag_precaution`.

### F03 — P2: split records distort personalisation and last-night summaries

Locations: `app/services/learning.py:112`, `:164`; `app/services/sleep_model.py:547`.

Represent seven identical eight-hour sleeps either as whole 23:00–07:00 records or contiguous 23:00–03:00 plus 03:00–07:00 records, with efficiency 1. Rate each whole night, or only the last fragment, 4/5:

| Output | Whole records | Split records |
|---|---:|---:|
| Physical sleep pressure | 0.310722665 | 0.310722665 |
| Estimated debt | 0 | 0 |
| Last-night duration | 8 h | 4 h |
| Readiness | 90 | 74 |
| Learned need | 7.41 h | 7.10 h |

Debt and pressure account for the full interval, while learning pairs the morning rating with only the last fragment and readiness treats that fragment as the whole night. Rating both fragments separately also increases the apparent observation count, even though the physical history is unchanged.

**Change:** define aggregation of primary-night episodes before duration-based learning, body-clock observations and summaries. Preserve the actual sleep/wake intervals for pressure simulation, and define how one morning rating attaches to a whole night rather than a fragment.

Probes: `test_splitting_same_sleep_does_not_change_readiness`, `test_final_morning_rating_uses_whole_sleep_not_last_fragment`.

### F04 — P2: holiday progression restarts after a completed step

Location: `app/services/modes.py:283`–304.

With recent 01:30–10:30 sleep and term six days away, the first plan gives 10:00, 09:30, 09:00, 08:30, 08:00, 07:30 alarms. After logging the first prescribed 01:00–10:00 night, the next day's plan begins with **10:00 again**, not 09:30. Its seven-record median remains 10:30, and the step count restarts at one.

**Change:** anchor plan progress or base the next step on actual recent adherence. Replanning must not erase a followed step or silently promise an earlier alarm than it actually schedules.

Probe: `test_holiday_plan_does_not_repeat_a_completed_step`.

### F05 — P2: repeated-hour midpoint can be about twelve hours wrong

Location: `app/services/learning.py:138`–142.

The valid sleep span `2026-10-25T01:30+01:00` to `2026-10-25T01:15+00:00` lasts 45 minutes and has actual midpoint **01:52:30 BST**. Duration validation now handles it correctly. Midpoint learning still compares only wall clocks, adds 24 hours when the end clock is earlier and reports **13:22:30** instead. One such observation can move an adult neutral clock estimate from 03:30 to roughly 06:07.

**Change:** calculate datetime midpoints between actual instants and convert back to local clock time. Keep the overnight clock-wrap rule only for time-only profile settings.

Probe: `test_repeated_hour_midpoint_uses_elapsed_instants`.

### F06 — P2: completed events can incorrectly eliminate the remaining sleep window

Location: `app/services/planner.py:83`–93, `:182`.

At 02:00, a call that already finished at 00:45 is still selected as the binding commitment for that sleep-night. Its 23:30 preparation alarm lies in the past, so the planner reports no sleep window. The same input without that completed event gives a feasible window until 07:30.

**Change:** distinguish completed commitments from upcoming/ongoing commitments when calculating required wake deadlines. Retain explanations for genuinely missed or ongoing events without letting completed events constrain future sleep.

Probe: `test_completed_event_does_not_prevent_sleep_for_rest_of_night`.

### F07 — P2: generated alarms can disagree with their own serialized timestamps

Location: `app/services/planner.py:175`–181; `app/time_utils.py:43`.

For a usual 01:30 wake on the spring clock-change date, the planner returns a nonexistent London `2026-03-29 01:30` alarm and says 01:30 in its headline. JSON serialization instead yields `2026-03-29T02:30+01:00` because it normalizes through the gap. Input validation is strict, but derived schedule clocks bypass that policy.

**Change:** resolve nonexistent generated times using an explicit policy before constructing fields, reasons or headlines. Serialization should preserve the chosen schedule rather than silently alter its displayed clock.

Probe: `test_generated_dst_alarm_matches_its_serialized_clock`.

### F08 — P2: an ongoing night is assessed as if earlier sleep did not count

Location: `app/services/planner.py:249`–273.

Given ongoing sleep from 23:00 to 07:00 and `now=03:00`, the model correctly marks the person asleep. Bedtime planning nevertheless gives a new 03:10 bedtime, 07:30 alarm, 3.9 hours of sleep and a warning that the night is below the seven-hour minimum. Roughly four already-slept hours are omitted from that minimum assessment.

**Change:** make sleep-in-progress behaviour explicit and distinguish remaining sleep from total sleep in the current night. Avoid advising an already-asleep person to go to bed as soon as possible.

Probe: `test_ongoing_night_counts_already_slept_hours_toward_minimum`.

### F09 — P2: standalone mode plans use future observations

Location: `app/services/modes.py:196`–200.

The default `_in_bed_h()` calculation learns from all supplied records and selects efficiency without an as-of filter. Appending rated sleep sessions ten or more days in the future changes today's standalone jet-lag plan even though today's evidence is unchanged. `PersonModel` correctly excludes those observations, so the direct mode endpoint and model-backed planning can disagree.

**Change:** pass an explicit cutoff through the mode-plan defaults and share completed-history filtering with the model. This was an earlier documented concern; the repair now supplies an as-of cutoff.

Probe: `test_future_sessions_do_not_change_todays_standalone_plan`.

### F10 — P2: tiredness-awareness wording overstates the cited experiment

Location: `app/services/nap_recc.py:234`–237.

The explanation assigns a one-week point after which people stop noticing tiredness. Van Dongen et al. found an acute increase in subjective sleepiness followed by smaller increases while objective deficits accumulated. It supports underestimated performance impairment, not the stated universal one-week loss of tiredness awareness. [Original paper](https://www.med.upenn.edu/uep/assets/user-content/documents/VanDongen2003CumulativeCost.pdf).

**Change:** say that with repeated short nights, people can underestimate their growing performance impairment. Keep the model's refusal to erase debt based on a reassuring report; correct its explanation.

### F11 — P3: alcohol's sleep-onset effect is stated too universally

Location: `app/services/planner.py:287`–290.

The generic evening-social-event tip presents alcohol as helping sleep onset without qualifying dose. A 27-study meta-analysis found shorter onset latency only at high doses, while lower doses could disrupt REM sleep. This does not support presenting faster sleep onset as a general outcome of having drinks. [Study and abstract](https://research.monash.edu/en/publications/the-effect-of-alcohol-on-subsequent-sleep-in-healthy-adults-a-sys/).

**Change:** say alcohol can disrupt sleep even if it makes someone feel sleepy, with effects varying by dose. Do not suggest drinking as a sleep aid.

## What the review does not classify as newly discovered defects

- Readiness/alertness weights, Bayesian likelihood scales, nap utility penalties and approximate adaptation rates are disclosed model choices, not validated individual physiological measurements.
- Excluding the full lying-down period from nap utility is now an explicit opportunity-cost policy.
- Sparse data, the explicit UK default, frozen physiology in what-if forecasts and incomplete app/database integrations remain known limits.
- The basic exponential pressure functions and their elapsed-time arithmetic are consistent with the documented equations; that does not validate the app's whole prediction system.

## Recommended repair sequence

1. Make all known departures binding and apply the jet-lag light precaution consistently.
2. Define primary-night aggregation and correct datetime midpoint calculations.
3. Preserve holiday progress across requests.
4. Handle completed events, ongoing sleep and generated DST clocks explicitly.
5. Filter future records consistently and correct the remaining factual wording.

After each repair, move the corresponding probes into the normal suite and add integration-level invariants (same physical night represented differently, plan followed across days, all advice paths consistent). A passing unit suite alone cannot establish that all cross-feature behaviours or scientific claims are correct.
