"""Sample journal entries for the demo students, so Dreams isn't empty in a demo.

Entries are written in each persona's voice with hand-picked postcard colours
(no Gemini call, so demos load instantly). Brain dumps still go through the real
planner, so their "tomorrow" plan is computed from the persona's calendar.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from app.schemas import UserData
from app.services import brain_dump, db
from app.services.dream_insights import _STYLE
from app.time_utils import instant


def _entry(kind, days_ago, at, title, content, mood, summary, tags, symbols=None, palette=None):
    return {"kind": kind, "days_ago": days_ago, "at": at, "title": title, "content": content,
            "details": {"title": title, "summary": summary, "mood": mood, "tags": tags,
                        "palette": palette or list(_STYLE[mood][0]), "symbols": symbols or list(_STYLE[mood][1]),
                        "source": "doze"}}


JOURNALS = {
    "june": [
        _entry("dream", 0, time(7, 5), "The Library That Was a Lighthouse",
               "I was in the uni library but it was a lighthouse, and every time I turned a page the light swung round over the sea.",
               "calm", "You read by the slow sweep of a lighthouse beam over a quiet sea.", ["library", "sea", "light"],
               ["🗼", "📖", "🌊"], ["#a8d8ea", "#cdb4db", "#fef9ef"]),
        _entry("day", 1, time(21, 30), "A Good, Ordinary Tuesday",
               "Aqua aerobics in the morning, lab write-up done by four, cooked pasta with my flatmates. Felt on top of things.",
               "joyful", "A steady day that ended round a shared pot of pasta.", ["routine", "friends", "lab"],
               ["🏊", "🧪", "🍝"]),
        _entry("dream", 3, time(7, 10), "Cats Running the Seminar",
               "Three cats were running my seminar and kept asking me to define 'entropy' while knocking pens off the table.",
               "strange", "A feline panel grilled you on entropy while redistributing the stationery.", ["seminar", "cats", "entropy"],
               ["🐈", "🖊️", "🌀"]),
    ],
    "maya": [
        _entry("dream", 0, time(8, 20), "The Exam Hall With No Exits",
               "I was in the exam hall and the paper was in a language I didn't know. Every door I tried led back to my desk.",
               "anxious", "Every door circled back to a desk and a paper you couldn't read.", ["exam", "doors", "lost"],
               ["📝", "🚪", "⏰"], ["#6c757d", "#b7a7d9", "#e9c46a"]),
        _entry("day", 1, time(22, 40), "Graphs, Graphs, Graphs",
               "Revised Dijkstra and spanning trees all afternoon. Got two past-paper questions right, one badly wrong. Library closed on me.",
               "curious", "An afternoon of shortest paths, ending when the library did.", ["revision", "algorithms", "library"],
               ["🕸️", "📚", "💡"]),
        _entry("dream", 2, time(8, 15), "Falling Through the Spanning Tree",
               "I was climbing a giant tree made of graph edges and the branches kept deleting themselves when I stood on them.",
               "strange", "You climbed a tree of edges that pruned itself under your feet.", ["tree", "falling", "graphs"],
               ["🌳", "🪢", "🌀"]),
    ],
    "leo": [
        _entry("dream", 0, time(10, 55), "Surfing on the Kitchen Table",
               "I was surfing a wave across the kitchen table at home and Dad was timing me with an egg timer.",
               "joyful", "You rode a kitchen-table wave while Dad timed it with an egg timer.", ["home", "surf", "dad"],
               ["🏄", "🥚", "🌊"]),
        _entry("day", 1, time(23, 50), "Lazy Holiday Sunday",
               "Slept in till half ten, gym with Dad, gaming till late. Need to start thinking about term though.",
               "calm", "A slow holiday day with a quiet nudge that term is coming.", ["holiday", "gym", "gaming"],
               ["🎮", "🏋️", "☕"]),
    ],
    "priya": [
        _entry("dream", 0, time(7, 20), "Lost in a Tokyo Train Station",
               "I was in a huge Tokyo station and every platform sign showed my conference slides instead of train times.",
               "adventurous", "Every departure board in a vast Tokyo station showed your slides.", ["tokyo", "trains", "conference"],
               ["🚄", "🗼", "🗾"]),
        _entry("day", 1, time(21, 10), "Slides Nearly There",
               "Finished most of the conference slides, booked the airport train, still need to rehearse. Bed early tonight for the jet-lag plan.",
               "curious", "Slides mostly done and the journey booked, one rehearsal to go.", ["conference", "travel", "slides"],
               ["💻", "🧳", "✈️"]),
    ],
}

DUMPS = {
    "maya": ("After algorithms I need to start on networks: do a past paper, and I have to email the module leader "
             "about the formula sheet. Also need to buy snacks. I'm scared I'll blank on the proofs tomorrow.",
             [{"title": "Do a networks past paper", "minutes": 90, "focus": True, "when": "tomorrow"},
              {"title": "Email networks module leader about the formula sheet", "minutes": 10, "focus": False, "when": "tomorrow"},
              {"title": "Buy snacks for revision", "minutes": 20, "focus": False, "when": "tomorrow"}],
             [{"text": "Scared of blanking on the proofs", "note": "That fear is common before exams; it can rest until morning."}],
             "Your Networks prep is planned around tomorrow's exam, and one worry is parked for tonight."),
    "priya": ("Tomorrow I have to rehearse my talk twice and print the boarding pass, and pack the adapter. "
              "I'm nervous about presenting in front of senior people.",
              [{"title": "Rehearse the conference talk twice", "minutes": 60, "focus": True, "when": "tomorrow"},
               {"title": "Print boarding pass", "minutes": 10, "focus": False, "when": "tomorrow"},
               {"title": "Pack the plug adapter", "minutes": 10, "focus": False, "when": "tomorrow"}],
             [{"text": "Nervous about presenting to senior people", "note": "Nerves mean it matters to you; nothing to fix tonight."}],
             "Your trip prep is planned and the presentation nerves are parked."),
}


def seed(user_id: str, persona: str, data: UserData, now: datetime) -> None:
    """Replace this demo account's journal with the persona's sample entries."""
    db.delete_all_dreams(user_id)
    for entry in sorted(JOURNALS.get(persona, []), key=lambda e: (-e["days_ago"], e["at"])):
        when = datetime.combine(now.date() - timedelta(days=entry["days_ago"]), entry["at"])
        if instant(when) > instant(now):
            when -= timedelta(days=1)  # keep sample entries in the past
        db.save_dream(user_id, entry["title"], entry["content"], entry["details"], kind=entry["kind"],
                      created_at=instant(when))
    if persona in DUMPS:
        content, tasks, worries, summary = DUMPS[persona]
        plan = brain_dump.plan_tomorrow(data, now, tasks)
        day = datetime.fromisoformat(plan["day"])
        details = {"tasks": tasks, "worries": worries, "summary": summary, "plan": plan, "crisis": False,
                   "support": None, "source": "doze", "title": f"Plan for {day:%a %d %b}"}
        db.save_dream(user_id, details["title"], content, details, kind="dump", created_at=instant(now - timedelta(minutes=5)))
