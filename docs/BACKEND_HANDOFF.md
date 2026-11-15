# Backend handoff for the Doze brain

This is the integration contract for the implemented planning services. The local FastAPI application connects the pages to a SQLite adapter in `app/services/db.py`, with account-owned records in the ignored `instance/` directory. It now includes email/password authentication, revocable sessions and Gemini-assisted initial profile setup; it is not yet a Tiger Data adapter. Do not put the sleep equations or Bayesian updates into Gemini or the database: the existing Python services calculate those outputs from persisted inputs.

## Implemented local integration

Public entry is `GET /` (Welcome), followed by `/signup` or `/login`. New or
unfinished accounts enter `/onboarding`, then `/onboarding/review`. An explicit
validated `POST /onboarding/confirm` saves the profile and marks setup complete.
The dashboard at `/dashboard` and all existing planning/history/calendar pages
require completed setup. APIs return 401 when signed out and 403 when setup is
unfinished. Completed returning accounts skip setup; `POST /logout` revokes the
current server-side session. Existing unassigned prototype data is preserved
without automatically assigning it to any new account.

Gemini requests run server-side with bounded input, validated structured fields
and supporting answer quotes. A change to an existing draft field must cite the
current answer, so an old answer cannot undo a correction. The user reviews all
fields before they influence planning. Provider failures preserve the answer and
offer retry or explicit manual review; no fabricated AI reply or sleep record is
used. `GEMINI_API_KEY` and optional `GEMINI_MODEL` belong in the ignored `.env`.

| Endpoint | Behaviour |
|---|---|
| `GET /api/me/dashboard` | Saved inputs, current snapshot and alertness curve. |
| `POST /api/me/nap-recommendation` | JSON `tiredness`: `fine`, `tired`, `very_tired` or null. |
| `GET /api/me/bedtime-recommendation` | Current bedtime plan, including reasons, warnings and feasibility. |
| `POST /api/me/what-if` | JSON bedtime/alarm; validates one future sleep window within seven days and at most 24 hours. Does not persist hypothetical sleep. |
| `GET /api/me/sleep-history` | Canonical records, completed-night summaries, learned estimates and legacy rows excluded from calculations. |
| `GET`, `PATCH /api/me/profile` | Read/update the validated local profile. |
| `PUT`, `DELETE /api/me/sleep-sessions/{source_id}` | Save/update/delete completed sleep by stable source ID. Use 1–200 ASCII letters, digits, `_`, `-`, `.`, `:` (excluding `.` and `..` alone). Overlaps and future completed records are rejected. |
| `PATCH /api/me/sleep-sessions/{source_id}/rating` | Update the existing record's 1–5 check-in. |
| `GET /auth/google`, `GET /auth/callback` | Read-only Google OAuth with server-side state, PKCE and credential storage. |
| `POST /calendar/sync`, `POST /calendar/disconnect` | Replace the saved Google snapshot or disconnect. Failed imports preserve the previous snapshot and show an error. |

The calculation clock is controlled by the server. Pages do not fetch Google
on every render; users explicitly sync after schedule changes. Only busy timed
events become constraints; all-day/free/declined/cancelled entries do not.
Imports preserve actual instants but currently use category `other` and zero
travel minutes. There is no automatic event classification or routing estimate.
Existing date/duration-only SQLite history is preserved without guessing its
missing bedtime or wake-up timestamps.

New travel profiles get a server-controlled `planned_from` creation date; edits
preserve it. Clear the old trip before creating a different trip. Browser writes
from other origins are rejected in addition to account authentication.

The local wrapper is verified with temporary databases and simulated Google
responses. Real account consent, a matching registered redirect URI and actual
Tiger connectivity still require external setup and verification.

## What we need from the backend team first

1. A reachable Tiger Data database from the Python application's runtime, verified with an authenticated `SELECT 1` (console-only success does not prove application connectivity).
2. A database adapter returning `UserData` from `app/schemas.py` for the authenticated user, plus profile/session/event upserts and rating updates.
3. Persistent source IDs and sleep episode IDs, with one morning rating per complete primary sleep episode.
4. Offset-bearing timestamps, named timezone metadata and a server-controlled calculation time.
5. API handlers wrapping the existing brain functions and returning `.model_dump(mode="json")` output, including reasons/warnings and infeasible/in-progress states.
6. Calendar ingestion and event categorisation, with real/user-supplied travel and airport-preparation buffers.

The first integration milestone is to save one demo persona, load it through the database adapter, and get the same model results at the same explicit `now`. Then wire the nap and bedtime endpoints to the UI.

## Data the adapter must return

```python
UserData(
    profile=UserProfile(...),
    sessions=[SleepSession(...), ...],
    events=[CalendarEvent(...), ...],
)
```

| Input | Required integration behaviour |
|---|---|
| Profile | Persist age band, usual bedtime/wake, chronotype, sleep latency, morning routine, optional user sleep-need setting, selected mode and term date. Use the schema's accepted enum values. |
| Sleep record | Supply actual start/end, `is_nap`, efficiency and any available HRV, onset latency, rating and display-only stage data. Efficiency is a fraction (0.9), not a percentage (90). |
| Source record identity | Upsert by authenticated user + source/provider record ID. An edited watch record replaces its previous version even if timestamps changed. Do not append it as new sleep. |
| `SleepSession.episode_id` | Give all primary-night fragments from the same episode the same stable ID; do not reuse the ID for a different night. Preserve real awake gaps and the raw fragments. |
| Morning rating | Persist one 1–5 report against the complete primary episode. The adapter can attach it to the final fragment; the brain uses the final available rating once. Update a check-in rather than creating another sleep interval. |
| Post-nap rating | Update the existing nap record's rating. A neutral morning rating does not update sleep need; nap grogginess learning uses its own rating likelihood. |
| Calendar event | Supply actual start/end, title, validated category, ground `travel_min`, location if available, and optional stress fraction 0–1. Keep external source IDs for sync updates/deletions. |
| Travel plan | Supply `home_tz`, `destination_tz`, departure/arrival timestamps, creation date `planned_from`, ground `travel_min` and extra `pre_departure_buffer_min` for check-in/security. |

Never invent missing HRV or ratings. Leave optional values `None`. Do not replace unknown sleep nights with zero-hour records. Fetch the history required by the learning services; do not globally restrict reads to fourteen days (that is the debt window, not the whole learning history). The engine filters completed evidence as of `now`.

### Episode aggregation policy

With an explicit `episode_id`, the engine aggregates primary fragments from that episode before calculating sleep need, clock observations, last-night summaries and HRV baselines. Sleep pressure still uses the individual actual sleep intervals. A known ongoing episode does not become a completed night merely because an earlier fragment ended.

Legacy records without episode IDs have a fallback grouping policy: same 14:00-to-14:00 sleep-day label, gap at most two hours, whole span at most 24 elapsed hours. Those are compatibility rules, not scientific thresholds. Explicit provider/episode identity is the preferred integration.

The episode's actual sleep is the sum of its fragments. Gaps reduce its summary efficiency. The latest available rating and HRV are used once; onset latency belongs to the first fragment. Conflicting overlaps or interleaved episode IDs must be reconciled before calling the brain.

## Time contract

- Store actual instants in PostgreSQL `TIMESTAMPTZ` columns, and keep named IANA zones separately. Do not strip timezone offsets before storage.
- Input/output JSON must retain offsets. Use the model's JSON serializers, not manually formatted naive timestamps.
- Ordinary legacy local schedules currently use **Europe/London**. Convert the server's current UTC time with `app.time_utils.local_datetime()` before building the model. The server should choose `now`, not accept arbitrary client time for live recommendations; a fixed clock is useful in tests/demo mode.
- Travel departure/arrival fields use their own home/destination zones; their chronological validation compares actual instants. A valid westbound arrival can have an earlier wall clock than departure.
- Recurring generated schedule clocks falling in the spring DST gap move forward by the gap before labels/results are built. Invalid observed naive timestamps in the missing hour are rejected; send the real offset-bearing instant.
- Worldwide ordinary scheduling and unambiguous in-flight/post-landing clock context still need an explicit per-user timezone implementation. Storing a user timezone column alone does not provide it. Keep the MVP ordinary schedule contract UK-local until that work is agreed and tested.

## Travel and holiday requirements

Set `planned_from` to the current **home-local creation date** the first time a trip is added, and preserve it on edits. `None` means the legacy/demo assumption that all preparation nights were available, not that preparation was actually followed.

Supply ground travel and additional check-in/security buffers. Zero is a compatibility default and does not mean the app knows the user needs no airport preparation. Departure is now a binding planner commitment without needing a duplicate calendar flight event; separately imported events can still impose stricter deadlines.

Holiday progress uses the latest completed actual primary wake. Save/sync each completed night so following a plan advances the next step. Without new evidence, the engine does not pretend the user followed a previous recommendation.

For standalone jet-lag plans, pass `now` explicitly:

```python
modes.jet_lag_plan(data, now.date(), now=now)
```

Date-only calls deliberately use midnight at the start of the requested date as the evidence cutoff. Bedtime planning supplies its own fitted time-in-bed value.

## Proposed API surface

Authentication must determine ownership; do not let a client choose another user's records by supplying a user ID.

| Proposed endpoint | Responsibility / existing call |
|---|---|
| `GET /api/me/dashboard` | Load inputs, build `PersonModel`, return `planner.snapshot(...)` and `planner.day_curve(...)`. |
| `POST /api/me/nap-recommendation` | Validate `tiredness` as `fine`, `tired` or `very_tired`; call `nap_recc.recommend_nap(...)`. |
| `GET /api/me/bedtime-recommendation` | Call `planner.recommend_bedtime(...)`. |
| `POST /api/me/what-if` | Validate future bedtime/alarm instants, call `planner.what_if_bedtime(...)`; surface input errors. |
| `GET /api/me/sleep-history` | Return primary-night summaries and optional learned-belief summaries. |
| `PATCH /api/me/profile` | Validate/store profile changes. |
| `PUT /api/me/sleep-sessions/{source_id}` | Idempotently ingest/update a completed sleep record with its episode identity. |
| `PUT /api/me/sleep-episodes/{episode_id}/rating` | Save/update a morning check-in, then load fresh data for later model calls. |
| `PUT /api/me/naps/{source_id}/rating` | Update the completed nap's rating. |
| `PUT /api/me/travel` | Validate/store trip fields; preserve original creation date. |
| Calendar connect/sync endpoints | OAuth, persist credentials securely, import/update/delete events and populate the shared event schema. |

Input errors should be returned as validation responses (for example HTTP 400/422), not unhandled 500s. Avoid leaking database URLs, passwords or provider tokens in responses/logs.

### Recommendation handler pattern

```python
from datetime import datetime, timezone
from app.schemas import UserData
from app.time_utils import local_datetime
from app.services import planner
from app.services.sleep_model import PersonModel

# Authentication and the DB adapter are supplied by the backend team.
inputs = adapter.load_user_data(authenticated_user_id)
data = UserData.model_validate(inputs)
now = local_datetime(datetime.now(timezone.utc))
model = PersonModel(data, now)
response = planner.snapshot(data, now, model=model).model_dump(mode="json")
```

Build a fresh model after updates, and reuse one model only across calls for the same user/data/time in one request. Re-read profile/history after a saved check-in rather than caching stale personalised results indefinitely.

## Optional live sleep state

Completed-history ingestion and live timer state are different. Keep an active sleep/timer record separately with an actual start and an optional expected end; do not persist the expected end as an actual completed watch observation. If an expected end is known and live sleep-in-progress support is implemented, the adapter may append a transient `SleepSession` with that future end and the same episode identity when constructing `UserData`. The engine excludes the known incomplete episode from completed-history learning. When the user wakes, close/update the source record with the actual end once.

The proposed completed-session endpoint above does not by itself implement live timer ingestion. If the backend does not implement active-state storage, integrate completed-history recommendations first; do not imply the app knows that someone is currently asleep.

## UI/response fields that must be respected

- `should_nap=False` is a valid result. Do not always start an alarm just because an optional alarm time is present.
- `BedtimeRecommendation.feasible=False` means no sleep window fits; display its explanation rather than treating the dates as an executable sleep plan.
- `in_progress=True` means sleep is already underway. `sleep_h` is the estimated total for the current night; `remaining_sleep_h` is the expected remaining portion. Do not display another 'start sleeping' action for the existing interval.
- Keep all `reasons` and `warnings`; do not discard them when mapping response fields.
- `alertness=None` means asleep/no awake score at that time; preserve chart gaps.
- Destination bright-light windows remain deliberately withheld pending phase-aware timing. Do not invent them in frontend text or Gemini output.

## Calendar/Gemini boundary

The backend should import calendar events, handle cancellations/edits, and normalise timezones. Gemini can propose a category from `EventCategory` and a bounded stress value. Validate the result and support correction; do not ask Gemini to replace the Python sleep calculations, assert actual mood from a calendar, or invent travel time. Use user-supplied travel values initially, or a separately integrated routing source.

## Ownership and acceptance checklist

Backend: authenticated data ownership, persistence, import/sync, input validation, API wrappers and error handling. Brain: aggregation, Bayesian estimates, pressure simulation, recommendations and explanations. Frontend/device: input forms, rendering, actual alarm/timer/audio and platform notification behaviour.

Before saying integration is complete, demonstrate:

1. Python backend connects to Tiger Data successfully.
2. Save/load a demo user preserves offset-bearing timestamps and predictions at fixed `now`.
3. Reimporting the same source record does not duplicate sleep or events.
4. A rating update affects the next recommendation without creating extra sleep.
5. Fragmented and whole representations of one night yield equivalent learned/summary results.
6. DST storage round trips preserve elapsed time; future records do not influence today's recommendations.
7. A real trip creation preserves `planned_from` through edits, and a 03:00 departure constrains the alarm.
8. Routes return the real service outputs and handle infeasible/in-progress/no-nap cases.
9. The frontend actually schedules/rings the selected alarm; a calculated timestamp alone is not an alarm integration.
