# durhack-2026

## Run Doze locally

From the project directory:

```sh
source venv/bin/activate
python -m pip install -r requirements.txt
python -m app.main
```

Open **http://127.0.0.1:8000** (or the host/port configured in your `.env`).
Stop the server with `Ctrl+C`. If you need a new environment, create one first
with `python3 -m venv venv`.

The local app starts without Google or Tiger Data. It generates a persistent
session secret in the ignored `instance/` directory when `SESSION_SECRET` is
unset. `.env.example` contains placeholders only; keep real credentials in your
ignored `.env`. Do not overwrite an existing `.env` when using the example.

1. Open the **Welcome** page and select **Personalise my plan**. Create an account
   or log in. New accounts enter a Doze conversation powered by Gemini, then an
   editable profile review. Only **Confirm profile and open my dashboard** saves
   the routine and unlocks the dashboard. Returning users with a completed profile
   go straight to **Today**; unfinished conversations resume after login.
2. Open **Profile** (top right) to change your usual schedule, then **History** to
   log completed sleep with the date/time pickers. A 1–5 check-in updates the saved record
   and feeds the next personalised estimate. Neutral morning ratings do not
   update sleep need.
3. **Today** shows the active mode, next useful action, bedtime plan and graphs
   when completed sleep is available. Use **Plan → Nap / Tonight / What-if** to
   explore recommendations. Set suggested alarms on your device.
4. Optionally open **Calendar**, connect Google and sync. Register the exact
   `GOOGLE_REDIRECT_URI` in Google Cloud and use the same browser hostname for
   login and callback. Sync again after changing calendar events; pages show
   the last sync or any failure. All-day/free/declined entries do not become
   timed sleep constraints. Automatic event categorisation and travel-time
   estimation are not implemented; imports default to `other` with no travel buffer.

Set `GEMINI_API_KEY` in `.env` for the actual conversation. `GEMINI_MODEL` defaults
to `gemini-3.5-flash-lite` and can be changed for your provider access. Keys stay on the
server. Gemini proposes profile fields from the conversation; the Python brain
still calculates sleep plans. Missing settings or a provider failure show a real
error and preserve the answer for retry. Users can explicitly enter and review
their routine themselves instead; this is not presented as an AI response.

Each signed-in account uses its own profile, sleep, calendar, setup conversation
and Google connection. Without recorded sleep, estimates use the confirmed
profile; fake personas are never silently used. A usual routine is not saved as
actual sleep. Hypothetical previews never save sleep.

This is a **local prototype with account-owned data**, using `instance/doze.db` (SQLite).
On first launch it copies the repository's older `doze.db` into `instance/`
without modifying the original. Google tokens stay in that private local
database; browser sessions hold random references. Passwords use salted scrypt,
and account session tokens are stored as hashes, expire and are revoked on logout.
Unassigned records from the old single-user prototype are preserved separately
and are never automatically given to a newly created account. Tiger Data, watch ingestion
and phone/system alarms still require backend/device
integration. `DATABASE_URL` and `DB_PASSWORD` do not affect this SQLite adapter.
Times currently use Europe/London.

All pages share the same blue/pink styling and navigation. On phones the main
navigation sits at the bottom; Today prioritises the next-action card. Date/time
pickers preserve offsets internally and ask which occurrence you mean when an
autumn clock time repeats; nonexistent spring times are blocked. Without
JavaScript the original offset-bearing timestamp fields remain usable. Older
sleep records and optional measurements stay available in expandable sections.
Charts distinguish missing records from zero and have accessible value tables.

Health check: `GET /health`. API documentation: `GET /docs` after signing in.
The dashboard is now `GET /dashboard`; `/` is the Welcome entry point.

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
- Local routes, templates and the SQLite adapter now connect these services;
  production backend requirements are described in the handoff below.

Use the existing environment: `venv/bin/python -m pytest -q`.

QA repairs: `BedtimeRecommendation.feasible` explicitly marks impossible sleep windows; dashboard curves still render. Hypothetical sleep replaces future recorded sleep while preserving past observations. Precise destination light windows are withheld until phase-aware timing is implemented. See [the QA repair record](docs/QA_REVIEW.md).

Follow-up QA repairs add primary sleep `episode_id` grouping, trip `travel_min` / `pre_departure_buffer_min` fields and bedtime `in_progress` / `remaining_sleep_h` outputs. See [the backend handoff](docs/BACKEND_HANDOFF.md) for required storage, proposed endpoints and integration checks, and [the follow-up repair record](docs/FOLLOWUP_QA_REVIEW.md) for the verified cases.
