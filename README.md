# ☾ Doze: sleep smarter

**Sleep trackers tell you how you slept. Doze tells you what to do about it: today, around your actual schedule.**

Doze is a contextual sleep and nap planner for students. It predicts how alert you'll be through *your* day, using your sleep, your body clock and your calendar. Then it tells you exactly when to wind down, when to sleep, and whether (and for how long) to nap. It learns your personal sleep need from a one-tap morning check-in, and switches into special modes for exam weeks, holidays and jet lag.

Built in 24 hours at **DurHack 2026**.

---

## The problem

Students' sleep falls apart exactly when it matters most: exam weeks, deadlines, late nights and travelling home. Research links better grades to sleep across the revision weeks, not just the night before ([Okano et al. 2019](https://www.nature.com/articles/s41539-019-0055-z)), and to *regular* sleep times ([Phillips et al. 2017](https://www.nature.com/articles/s41598-017-03171-4)).

But the tools don't help much:

- **Sleep trackers look backwards.** They score last night, but don't tell you what to do today.
- **Generic advice ignores your life.** "Get 8 hours" doesn't know about your 9 a.m. exam, your gym session or your lecture across campus.
- **Naps are a gamble.** Most people guess the length, and waking from deeper sleep can leave you groggier than before ([Brooks & Lack 2006](https://pubmed.ncbi.nlm.nih.gov/16796222/)).

## What Doze does

### 🌙 Today: one screen for your day
- **Tonight:** your wind-down and sleep time, worked back from tomorrow's first commitment, your sleep need and your recent nights.
- **Sleep now:** a countdown to your wake-up time. When it ends, the alarm rings and opens a short [Zooniverse](https://www.zooniverse.org/) citizen-science task, so you wake up helping researchers.
- **Nap:** the best nap time today, picked around your events, plus **Nap now**, which sets the alarm for you and explains why it chose that timing.
- **What if…?:** tap *"stay up till 2 a.m."* or *"nap at 3 p.m."* to see the trade-off instantly. You get the change in sleep and your predicted alertness at each of tomorrow's events, side by side with Doze's plan.
- **Personal tips** for reaching your sleep goal, and a **one-tap morning check-in** that teaches Doze about you.

### 📊 My sleep: history and insights
Charts of your last two weeks, including a bedtime-to-wake-up chart that makes irregular sleep and weekend lie-ins visible at a glance. Plain-language insights like *"Your wake time varies by about 1h 08m"* or *"On weekends your sleep sits about 2h later: social jet lag."*

### 📚 Modes for the weeks that matter
| Mode | When | What changes |
|---|---|---|
| **Exam** | Switches on automatically when an exam is within two weeks | A fixed wake time early enough for your earliest exam, bed before 2 a.m., never an all-nighter, and naps weighted towards exams |
| **Holiday** | Between terms | Lie-ins allowed, but wake time stays near term time, then moves back 30 min a day before term starts |
| **Travel** | Crossing 3+ time zones | A day-by-day jet-lag plan: shift 1 h a day for 3 nights before you fly, then about a day per time zone after landing (slower going east) |

### ✦ Dreams and days
Speak or type last night's dream (or how today went). Doze gives it a title, a mood and its own illustrated postcard, and links it to the night of sleep before it.

### 💬 Talk to set up
New users describe their routine in a short Gemini-powered conversation, then review every value before anything is saved.

## How it works

Doze's recommendations come from a **real sleep model in Python**, not from a language model.

- **The two-process model of sleep** ([Daan, Beersma & Borbély 1984](https://research.rug.nl/en/publications/timing-of-human-sleep-recovery-process-gated-by-a-circadian-pacem/)): sleep pressure builds while you're awake and drains while you sleep, and a ~24 h body clock rises and falls. Doze combines them, plus sleep inertia (grogginess after waking, [McHill et al. 2019](https://pmc.ncbi.nlm.nih.gov/articles/PMC6519907/)) and accumulated sleep debt ([Van Dongen et al. 2003](https://pubmed.ncbi.nlm.nih.gov/12683469/)):

  ```
  alertness = 97 + 25·C − 85·(S + fatigue) − W + nap boost − debt penalty   (0–100)
  ```
  where `C` is the body clock, `S` the sleep pressure and `W` the post-wake grogginess.
- **Bayesian personalisation.** Your sleep need, your nap grogginess and your body-clock timing each start from evidence-based defaults (by age band, and whether you're an early bird or a night owl). They update as you log nights. Morning check-ins count only as direction ("not enough sleep" vs "enough"), because ratings are noisy.
- **The nap algorithm** simulates your day with and without each candidate nap: 10 or 20 min, or 90 min only if you're very tired in the early afternoon, and never 30–60 min. It weights alertness during your events (exams count most), subtracts the cost to tonight's sleep, and leaves a 20-minute grogginess buffer before exams and driving.
- **Bedtime planning** works backwards from your first commitment tomorrow, your morning routine and your learned sleep need. It never plans below 7 hours (8 for teenagers) without saying so.

**Where AI fits in.** Gemini reads your onboarding conversation, explains nap timings in plain words, writes personal tips, and titles and illustrates journal entries. It only rewords facts the maths produced, and every AI feature has a non-AI fallback. Gemini never decides when you sleep.

Every rule is documented in the code against our **Sleep Science Brief**, and the [science guide](docs/SCIENCE_AND_FEATURES.md) explains each feature, its sources and its limits.

## Try it

You need Python 3.11+.

```sh
git clone <this repo>
cd durhack-2026
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in what you need (see below)
python -m app.main
```

Open **http://127.0.0.1:8000**, create an account and finish the short setup.

**Demo tips**
- At the bottom of **Today**, **Try a mode with sample data** signs you in to a demo student (Normal, Exam, Holiday or Travel) with two weeks of sleep and a calendar already filled in. Your own account isn't changed; log out to return to it.
- After pressing **Sleep now**, use **Demo: wake in 10 s** to hear the alarm without waiting all night. Allow pop-ups so the wake-up task can open.
- On **Calendar**, **Connect Google Calendar** loads a sample week (lectures, gym, a driving lesson and two exams), which switches Exam mode on.

### Configuration

Everything runs without any keys; AI features then fall back to Doze's own wording.

| Variable | Needed for |
|---|---|
| `GEMINI_API_KEY` | AI onboarding, nap explanations, tips and dream titles |
| `GEMINI_MODEL` | Which Gemini model to use (e.g. `gemini-flash-lite-latest`); onboarding needs it set |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` | Connecting a real Google Calendar (OAuth flow at `/auth/google`) |
| `SESSION_SECRET` | Optional; a random one is generated in `instance/` if blank |

Keep real keys only in your `.env`, which is ignored by git.

## Tech stack

**Python · FastAPI · Pydantic · Jinja2 · SQLite · vanilla JavaScript · Gemini API · Google Calendar API · Web Speech API · Web Audio API**

No front-end framework: pages are server-rendered, with small vanilla-JavaScript modules for the countdowns, voice input and instant What-if results. Charts are drawn on a plain canvas, each with an accessible table of values.

## Project structure

```
app/
├── services/          the brain: pure Python, no web code
│   ├── sleep_model.py     two-process model, alertness, sleep debt
│   ├── learning.py        Bayesian personalisation
│   ├── nap_recc.py        nap algorithm
│   ├── planner.py         bedtime planning and What-if
│   ├── modes.py           exam, holiday and travel rules
│   ├── sleep_insights.py  My sleep insights
│   └── …                  Gemini helpers, calendar import, storage, accounts
├── pages/             FastAPI routes
├── templates/         Jinja2 pages
└── static/            CSS and JavaScript
data/personas.json     demo students (dates relative to today)
tests/                 400+ tests
docs/                  science guide, developer notes and reviews
```

Run the tests with `pytest`. Developer and integration notes are in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Limitations

- **Doze is a wellbeing prototype, not a medical device.** Scores are research-informed model estimates, not clinically validated measurements. Doze gives no medication advice, and suggests talking to a GP after two weeks of very poor check-ins.
- **The alarm only rings while the Doze tab is open;** a browser can't set your phone's alarm clock.
- **Times currently use the Europe/London timezone.**
- **Sleep is logged by hand** (morning check-in); wearable integration is future work.

## Credits

- Wake-up task: [Squirrel Mapper](https://www.zooniverse.org/projects/bcosentino/squirrelmapper) on Zooniverse.
- Research: the studies linked above, plus [Gardiner et al. 2023](https://doi.org/10.1016/j.smrv.2023.101764) and [Van Dongen et al. 2011](https://pmc.ncbi.nlm.nih.gov/articles/PMC3260461/). Full references are in the [science guide](docs/SCIENCE_AND_FEATURES.md).

## Team

Made at DurHack 2026 by a team of three. <!-- Add your names and links here. -->

## License

No license has been chosen yet, so all rights are reserved by the authors. Add a `LICENSE` file before others reuse the code.
