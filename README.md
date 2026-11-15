# durhack-2026

## Sleep maths (`app/services/`)

Every function takes `UserData` (see `app/schemas.py`) and an explicit `now`, and returns a
Pydantic model. Use `.model_dump(mode="json")` to hand it to a template or Chart.js.

| Page | Call |
|---|---|
| Dashboard | `planner.snapshot(data, now)` and `planner.day_curve(data, now)` |
| Nap planner | `nap_recc.recommend_nap(data, now, "tired")`, which includes the with/without-nap curves |
| Sleep planner | `planner.recommend_bedtime(data, now)` and `planner.what_if_bedtime(data, now, bedtime, alarm)` |
| Calendar | `nap_recc.best_nap_slot(data, now)` |
| Sleep history | `sleep_model.nightly_stats(data.sessions, need_h, now)` and `learning.personalise(data, now)` |
| Modes | `modes.effective_mode(data, now)`, `modes.jet_lag_plan(data, now.date(), now=now)` and `modes.back_to_term_plan(data, now)` |

- **Demo data:** `personas.load_persona("maya")` (also `leo`, `priya`, `june`). Dates are relative to today, so pass a fake `now` to demo the 2 p.m. nap at any hour.
- **Several calls in one request:** build `sleep_model.PersonModel(data, now)` once and pass it as `model=`.
- **How the model works:** it's the two-process model of sleep (sleep pressure plus body clock) with sleep inertia, personalised with Bayesian updates. The rules follow the Doze Sleep Science Brief, and section numbers appear in the code comments.

Run the tests with `pytest`.

## Model status and integration

See [the feature/science guide](docs/SCIENCE_AND_FEATURES.md) for plain-language explanations, sources and limitations. Scores are research-informed estimates, not clinically validated measurements. [The brain review](docs/BRAIN_REVIEW.md) records the repaired regressions.

- Create a real trip with `planned_from` set to its creation date in home-local time; persist that value and preserve it on edits. `None` means the legacy/demo full-preparation assumption.
- Sleep records with identical start/end instants are upserted (last wins). Conflicting overlaps and sessions longer than 24 hours are rejected; reconcile source records before ingestion.
- Travel datetimes preserve home/destination wall clocks. Sleep/calendar naive inputs explicitly use Europe/London; elapsed durations use instants and JSON timestamps include offsets to preserve DST folds. Add an explicit per-user timezone contract before supporting local schedules worldwide.
- Routes, templates and the database service are placeholders in this repository.

Use the existing environment: `venv/bin/python -m pytest -q`.

QA repairs: `BedtimeRecommendation.feasible` explicitly marks impossible sleep windows; dashboard curves still render. Hypothetical sleep replaces future recorded sleep while preserving past observations. Precise destination light windows are withheld until phase-aware timing is implemented. See [the QA repair record](docs/QA_REVIEW.md).

Follow-up QA repairs add primary sleep `episode_id` grouping, trip `travel_min` / `pre_departure_buffer_min` fields and bedtime `in_progress` / `remaining_sleep_h` outputs. See [the backend handoff](docs/BACKEND_HANDOFF.md) for required storage, proposed endpoints and integration checks, and [the follow-up repair record](docs/FOLLOWUP_QA_REVIEW.md) for the verified cases.
