# Doze: features, calculations and scientific limits

Reviewed 15 November 2026. The logic is research-informed; the app's numerical predictions are not clinically validated. Automated tests establish software behaviour and consistency, not biological accuracy. The referenced Doze Sleep Science Brief is not included in this repository, so this review checked selected original research and official guidance instead of certifying every sentence against that brief.

## Features in simple words

| Feature | What it does | How the brain builds it |
|---|---|---|
| Personal sleep-need estimate | Starts with an age-based estimate and adjusts with check-ins | Keeps a grid of possible needs. A poor rating favours higher needs; a good rating favours lower needs. Neutral ratings add no information. A prior keeps a small number of reports from making extreme changes. |
| Body-clock estimate | Estimates when your body tends to be sleepy | Blends your usual sleep midpoint and chronotype with the last 14 recorded night midpoints. Clock times are aligned around a common reference so midnight/noon boundaries do not distort the average. |
| Alertness curve | Shows a predicted rise and fall during the day | Combines sleep pressure, a daily clock wave, a grogginess term, a temporary nap boost, estimated debt and a small HRV adjustment. The displayed number is a relative model score, not a probability of being safe or productive. |
| Bedtime and alarm | Works backwards from when you need to wake | Considers preparation and travel deadlines, estimates time in bed from actual sleep needed divided by efficiency, adds limited debt repayment, and checks calendar conflicts. Impossible schedules return `feasible=False` with warnings and a usable chart. |
| Nap recommendation | Compares a nap with staying awake | Simulates 10, 20 and sometimes 90 minutes of actual sleep, allowing extra elapsed time for falling asleep and efficiency. Scores predicted alertness during available awake time (lying down, including sleep latency, scores zero), with more weight on demanding events and a penalty for reducing bedtime sleep pressure. It can choose no nap. |
| Best calendar nap slot | Finds an available early-afternoon nap opportunity | Checks future candidate slots before 15:00, applies the nap decision at each, and selects the lowest predicted pre-nap alertness among helpful options. It does not guarantee a global optimal schedule. |
| Exam mode | Plans a consistent alarm ahead of exams | Activates when an exam is within 14 days; includes routine/travel time and a grogginess buffer. The 02:00 bedtime limit is a product rule. |
| Holiday mode | Helps move a late schedule back toward term time | Estimates drift from recent actual wake times and moves alarms by up to 30 minutes per day. |
| Travel mode | Plans preparation and approximate adjustment after a trip | Uses IANA timezone offsets, up to three available preparation nights, and shared progress calculations. It remembers when planning started rather than resetting progress every day. Preparation is assumed to have been followed, not verified. |
| What-if bedtime | Compares hypothetical sleep schedules | Replays pressure through a proposed sleep block and produces a curve and event forecasts. Fatigue, debt penalty and body-clock phase remain fixed at the time the model was built; this is a simplified short-term forecast. |
| History and readiness | Summarises sleep, estimated debt and HRV changes | Uses completed records. Exact duplicate spans are upserted, ambiguous overlaps rejected, and completed naps can repay outstanding debt immediately. Unknown nights are not invented as sleepless nights. |
| Explanations and wellbeing prompts | Explains tradeoffs and flags persistent poor check-ins | Applies transparent thresholds. Optional life-stage settings affect tips, not mathematical scores; gender and wearable sleep-stage percentages do not affect the model. These prompts do not diagnose conditions. |

## What the actual maths means

The sleep-pressure variable is a number between zero and one. During wakefulness it approaches one:

`S_after = 1 - (1 - S_before) * exp(-hours_awake / 18.2)`

During a sleep block it approaches zero:

`S_after = S_before * exp(-hours_in_bed * efficiency / 4.2)`

These are exact solutions of the app's chosen exponential equations. The pressure/clock structure comes from the two-process sleep literature; the 18.2-hour and 4.2-hour constants appear in published modelling. Applying average efficiency uniformly throughout a block is our approximation to intermittent sleep, not a measurement of actual sleep stages. See [Daan, Beersma and Borbély (1984)](https://research.rug.nl/en/publications/timing-of-human-sleep-recovery-process-gated-by-a-circadian-pacem/) and the equations in [Van Dongen et al. (2011)](https://pmc.ncbi.nlm.nih.gov/articles/PMC3260461/).

For bedtime planning, `time in bed = target sleep / efficiency`. For example, eight hours asleep at 90% efficiency needs about eight hours 53 minutes in bed. This arithmetic is correct under that assumption; real nightly efficiency varies.

Need learning is a Bayesian calculation: start with plausible values, multiply their probabilities by how compatible each observed check-in is, then normalise the probabilities. The method is mathematically coherent. The assumptions linking a rating to sufficient sleep, the 0.6-hour logistic scale, prior widths and bounded grids are our choices. A poor rating may reflect stress, illness or other factors. The posterior is an estimate within that model, not a validated test of biological sleep need.

Readiness and alertness contain chosen weighted sums. For example, readiness gives 50% weight to last-night sleep, 30% to debt and 20% to HRV. The alertness coefficients, nap boost/cost, grogginess-depth curve, daily-wave shape and HRV penalty are not fitted to a real validation dataset. Confidence intervals describe uncertainty within our assumed model, not proven real-world coverage.

## What published evidence supports, and where claims stop

- **Sleep duration:** adult recommendations support at least seven hours regularly; teen recommendations support eight to ten. Exact defaults and an individual's learned need remain estimates. Seven-to-nine-hour bounds are app policy, not a universal maximum. [AASM/SRS adult consensus](https://pmc.ncbi.nlm.nih.gov/articles/PMC4434546/), [AASM age guidance](https://www.aasm.org/resources/pdf/pressroom/healthy-sleep-duration.pdf).
- **Short naps:** Brooks and Lack tested precisely 5, 10, 20 and 30 minutes after restricted night sleep. Ten minutes had immediate benefits; benefits after twenty minutes emerged later. This supports offering short options in comparable conditions, not identical responses for everyone or our exact curve. Their experiment did not validate a 90-minute option. [Brooks and Lack (2006)](https://pubmed.ncbi.nlm.nih.gov/16796222/).
- **Sleep cycles:** cycles commonly span roughly 80–100 minutes. A 90-minute option approximates a typical cycle; it cannot guarantee waking in light sleep. Excluding all 30–60-minute naps is our product rule, not proof that those naps are always harmful. [NHLBI sleep stages](https://www.nhlbi.nih.gov/health/sleep/stages-of-sleep).
- **Grogginess:** post-wake impairment varies and can persist well beyond twenty minutes, especially after restricted sleep. Our twenty-minute exponential constant means about 37% of the initial modelled grogginess remains at twenty minutes. A twenty-minute scheduling buffer is not a guarantee of fitness to drive. [McHill et al. (2019)](https://pmc.ncbi.nlm.nih.gov/articles/PMC6519907/).
- **Regularity and grades:** Phillips and Okano reported associations with academic performance. They do not establish that the app will improve grades, or that 02:00 is a universal biological cutoff. [Phillips et al. (2017)](https://www.nature.com/articles/s41598-017-03171-4), [Okano et al. (2019)](https://www.nature.com/articles/s41539-019-0055-z).
- **Sleep debt:** repeated restriction can impair performance while subjective sleepiness changes less. This supports not letting a 'feel fine' report erase recorded deficits; it does not validate linear debt arithmetic or exact hour-for-hour repayment. [Van Dongen et al. (2003)](https://pubmed.ncbi.nlm.nih.gov/12683469/).
- **Jet lag:** gradual pre-trip sleep shifts and appropriately timed light can help. Our exact daily adaptation rates, older-age multiplier and pre-trip morning/evening light windows are heuristics. Precise destination light windows are withheld throughout adjustment pending a phase-aware algorithm; wrong light timing can shift the clock in the wrong direction. [CDC Yellow Book](https://www.cdc.gov/yellow-book/hcp/travel-air-sea/jet-lag-disorder.html).
- **HRV:** sleep loss can affect HRV, but the reading is influenced by multiple factors and protocols. Comparing with the person's recent baseline is more defensible than using a population threshold. The conversion into alertness points and the 'readiness' weighting remain unvalidated. [Research trial on sleep deprivation and HRV](https://pubmed.ncbi.nlm.nih.gov/33897355/).
- **Caffeine:** the 8.8-hour coffee and 13.2-hour pre-workout examples come from a meta-analysis with specified doses (about 107 mg and 217.5 mg). These are not personalised universal cutoffs or a guarantee of unaffected sleep. Later experimental work also shows dose dependence. [Gardiner et al. (2023)](https://doi.org/10.1016/j.smrv.2023.101764), [Gardiner et al. trial](https://pubmed.ncbi.nlm.nih.gov/39377163/).
- **Evening screens:** the cited experiment compared repeated four-hour evening e-reader use with print; it does not predict the effect of a particular person's short phone session. [Chang et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC4313820/).

## Integration requirements and remaining limits

1. The app routes, templates and database service are still empty here. Features above are implemented service functions, not a finished connected application.
2. On trip creation, persist `planned_from` as the current home-local date. Preserve it on edits. `None` deliberately retains the full-preparation assumption for legacy/demo records. There is no implemented form/database code here to complete this wiring.
3. Aware travel arrivals now convert to destination time and departures to home time, and invalid timezone names are rejected. Ordinary session/event ingestion uses an explicit Europe/London default for legacy naive inputs and preserves DST folds from aware timestamps. Elapsed arithmetic uses instants and JSON timestamps include offsets, so repeated clock hours survive storage. Before supporting local schedules in other zones, the team must add an explicit per-user timezone contract. Naive `now` is interpreted as home local before landing and destination local after landing; ambiguous in-flight/westbound transition times are not fully resolved by the current API.
4. Missing nights are unknown. The debt calculation is a recent-record estimate, not a complete debt measurement when history is sparse. Duplicate identity currently uses exact start/end instants; the database should ultimately upsert by a stable source record ID.
5. Forecasts do not fully update physiology after hypothetical recovery or travel. Clock learning from destination sleep can also mix with a separate jet-lag adjustment; robust travel-aware training history needs a timezone-aware data contract.
6. Watch accuracy, quiet wakefulness, medication, illness, sleep disorders and light exposure are not comprehensively modelled. A body-clock midpoint is a schedule-based proxy, not a melatonin measurement.
7. Synthetic personas are demonstration fixtures. Their generated ratings are not independent validation evidence. Numerical accuracy needs prospective real-data comparisons with predefined measures and error reporting.

A defensible demo description: **“Doze combines published sleep principles with transparent planning rules to estimate sleep need, compare nap options and fit sleep around your calendar. Its scores and timings are model estimates, not clinical measurements.”**

## Software validation after QA repairs

The suite contains 196 passing cases, including the ten originally failing independent acceptance cases. An additional 648 combinations of persona, date and time completed dashboard, bedtime, curve and nap calls without exceptions, including both UK clock-change dates. Generated demo nights move nonexistent spring clock times forward through the gap; real input in that missing hour is rejected unless supplied as a valid timestamp. These checks are software validation, not evidence of clinical prediction accuracy.
