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
| Modes | `modes.effective_mode(data, now)`, `modes.jet_lag_plan(data, today)` and `modes.back_to_term_plan(data, now)` |

- **Demo data:** `personas.load_persona("maya")` (also `leo`, `priya`, `june`). Dates are relative to today, so pass a fake `now` to demo the 2 p.m. nap at any hour.
- **Several calls in one request:** build `sleep_model.PersonModel(data, now)` once and pass it as `model=`.
- **How the model works:** it's the two-process model of sleep (sleep pressure plus body clock) with sleep inertia, personalised with Bayesian updates. The rules follow the Doze Sleep Science Brief, and section numbers appear in the code comments.

Run the tests with `pytest`.
