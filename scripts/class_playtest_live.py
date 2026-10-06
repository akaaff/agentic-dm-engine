"""Live class playtest: three classes in ONE real game, driven over the real
WebSocket by one player controlling all three seats (multi-character play).

Everything is the real stack - REST character creation, lobby, the live intent
parser, the rules engine, the narrator - against a backend you start yourself
(see the module command below) so it can use a throwaway database. Each class
works through a short script of what that class actually does in a fight; when
the script runs out (or an action is rejected twice) it falls back to a plain
attack. The transcript (what each seat typed, how it parsed, what resolved,
what was rejected, the narration) goes to docs/class-playtest/live_<group>.md.

    DATABASE_URL=sqlite:///./class_playtest.db uv run alembic upgrade head
    DATABASE_URL=sqlite:///./class_playtest.db TTS_ENABLED=0 SCENE_IMAGES_ENABLED=0 \\
      MECHANICS_LOG_ENABLED=0 uv run uvicorn src.api.main:app --port 8001
    uv run python scripts/class_playtest_live.py --group 0 --port 8001"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from class_playtest_builds import BUILDS, GROUPS  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "class-playtest"

# What each class does in a fight, in the order a player would try it. "{ally1}"
# / "{ally2}" are the other two party members' names.
SCRIPTS: dict[str, list[str]] = {
    "barbarian": [
        "I rage and charge the nearest goblin, then chop it with my greataxe",
        "I attack the nearest goblin with my greataxe",
        "I grapple the nearest goblin",
        "I throw a javelin at the nearest goblin",
    ],
    "bard": [
        "I cast vicious mockery at the nearest goblin",
        "I inspire {ally1}",
        "I cast sleep on the goblins",
        "I cast healing word on {ally2}",
        "I stab the nearest goblin with my rapier",
    ],
    "cleric": [
        "I cast bless on myself, {ally1} and {ally2}",
        "I cast guiding bolt at the nearest goblin",
        "I cast sacred flame on the nearest goblin",
        "I cast cure wounds on {ally1}",
        "I hit the nearest goblin with my mace",
    ],
    "druid": [
        "I cast entangle on the goblins",
        "I cast shillelagh on my quarterstaff",
        "I cast produce flame at the nearest goblin",
        "I cast healing word on {ally1}",
        "I hit the nearest goblin with my quarterstaff",
    ],
    "fighter": [
        "I charge the nearest goblin and attack it with my longsword",
        "I shove the nearest goblin to knock it prone",
        "I use my second wind",
        "I attack the nearest goblin with my longsword",
    ],
    "monk": [
        "I run up to the nearest goblin and punch it, then kick it as a bonus action",
        "I throw a dart at the nearest goblin",
        "I punch the nearest goblin",
    ],
    "paladin": [
        "I charge the nearest goblin and attack it with my longsword",
        "I lay hands on {ally1}",
        "I attack the nearest goblin with my longsword",
    ],
    "ranger": [
        "I shoot the nearest goblin with my longbow",
        "I hide behind cover and shoot the nearest goblin with my longbow",
        "I shoot the nearest goblin with my longbow",
    ],
    "rogue": [
        "I hide and then shoot the nearest goblin with my shortbow",
        "I sneak up on the nearest goblin and stab it with my shortsword",
        "I throw a dagger at the nearest goblin",
        "I stab the nearest goblin with my shortsword",
    ],
    "sorcerer": [
        "I cast burning hands on the goblins",
        "I cast fire bolt at the nearest goblin",
        "I cast magic missile at the nearest goblin",
        "I cast fire bolt at the nearest goblin",
    ],
    "warlock": [
        "I cast eldritch blast at the nearest goblin",
        "I cast hellish rebuke on the nearest goblin",
        "I cast eldritch blast at the nearest goblin",
    ],
    "wizard": [
        "I cast sleep on the goblins",
        "I cast magic missile at the nearest goblin",
        "I cast shield",
        "I cast fire bolt at the nearest goblin",
    ],
}


DB_PATH = Path(__file__).resolve().parent.parent / "class_playtest.db"


def _forget_character(cid: str) -> None:
    """Characters are keyed by name slug, as in the real UI - clear a previous
    run's row from the throwaway database so a re-run can reuse the name."""
    import sqlite3

    with sqlite3.connect(DB_PATH) as con:
        con.execute("DELETE FROM characters WHERE id = ?", (cid,))


def create_party(http: httpx.Client, classes: list[str]) -> dict[str, dict[str, str]]:
    """character id -> {cls, name, token}; a lobby with all three seated."""
    campaign = "goblin_ambush_oneshot"
    sid = http.post("/sessions/lobby", json={"campaign_id": campaign}).json()["session_id"]
    party: dict[str, dict[str, str]] = {}
    for cls in classes:
        spec = BUILDS[cls]
        cid = spec["name"].lower()  # the real UI uses slugify(name) as the id
        _forget_character(cid)
        body: dict[str, Any] = {
            "character_id": cid,
            "name": spec["name"],
            "race_index": spec["race_index"],
            "class_index": cls,
            "background_index": "acolyte",
            "base_ability_scores": spec["scores"],
            "chosen_skills": spec["skills"],
            "chosen_equipment": spec.get("equipment") or [],
            "gender": "female",
            "fighting_style": spec.get("fighting_style"),
            "chosen_spells": spec.get("spells"),
            "chosen_prepared_spells": spec.get("prepared"),
        }
        r = http.post("/characters", json=body)
        r.raise_for_status()
        j = http.post(f"/sessions/{sid}/join", json={"character_id": cid})
        j.raise_for_status()
        party[cid] = {"cls": cls, "name": spec["name"], "token": j.json()["token"], "session": sid}
    http.post(f"/sessions/{sid}/start", json={"companion_ids": []}).raise_for_status()
    return party


def summarize_events(events: list[dict[str, Any]]) -> list[str]:
    lines = []
    for e in events:
        p = e.get("payload", {})
        t = e["type"]
        if t == "attack_roll":
            lines.append(
                f"{e['actor']} attack {p.get('source')} vs {p.get('target')}: "
                f"{p.get('roll_total')} vs AC {p.get('target_ac')} "
                f"{'HIT' if p.get('hit') else 'miss'}"
                + (f" +sneak {p['sneak_attack_damage']}" if p.get("sneak_attack_damage") else "")
            )
        elif t == "spell_cast":
            extra = {k: v for k, v in p.items() if k not in ("attack_bonus_breakdown",)}
            lines.append(f"{e['actor']} cast {extra}")
        elif t == "damage_dealt":
            lines.append(f"  dmg {p.get('amount')} {p.get('damage_type')} -> {p.get('target')}")
        elif t == "hp_change":
            lines.append(f"  heal {p.get('amount')} ({p.get('source')}) -> {p.get('target')}")
        elif t in ("saving_throw", "skill_check"):
            lines.append(
                f"{e['actor']} {t} {p.get('spell') or p.get('skill') or p.get('kind')}: "
                f"{p.get('roll_total')} vs {p.get('dc')} -> {p.get('success')}"
            )
        elif t == "initiative_rolled":
            continue
        else:
            lines.append(f"{e['actor']} {t} {p}")
    return lines


async def drive(group: int, port: int, max_actions: int) -> None:
    classes = GROUPS[group]
    http = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60)
    party = create_party(http, classes)
    sid = next(iter(party.values()))["session"]
    names = {cid: v["name"] for cid, v in party.items()}
    by_id = {cid: v["cls"] for cid, v in party.items()}
    queue: dict[str, list[str]] = {}
    for cid, v in party.items():
        allies = [n for c, n in names.items() if c != cid]
        queue[cid] = [line.format(ally1=allies[0], ally2=allies[1]) for line in SCRIPTS[v["cls"]]]
    rejects: dict[str, int] = {cid: 0 for cid in party}

    query = "&".join(f"token={v['token']}" for v in party.values())
    url = f"ws://127.0.0.1:{port}/ws/session/{sid}?{query}"

    transcript: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    seen_events = 0
    status = "in_progress"
    sent = 0

    async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
        while sent < max_actions or current is not None:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=240)
            except TimeoutError:
                print("timeout waiting for the server")
                break
            msg = json.loads(raw)
            t = msg["type"]
            if t == "state_update":
                game = msg["game_state"]
                status = game["status"]
                new = game["events"][seen_events:]
                seen_events = len(game["events"])
                if current is not None:
                    current["events"].extend(summarize_events(new))
                else:
                    transcript.append(
                        {
                            "actor": "(auto)",
                            "text": "",
                            "events": summarize_events(new),
                            "errors": [],
                            "narration": [],
                        }
                    )
                if status != "in_progress":
                    print(f"game over: {status}")
                    break
            elif t == "narration" or t == "scene_narration":
                target = (
                    current if current is not None else (transcript[-1] if transcript else None)
                )
                if target is not None:
                    target["narration"].append(msg["text"])
            elif t == "error":
                if current is not None:
                    current["errors"].append(msg["detail"])
                    rejects[current["cid"]] += 1
            elif t == "bardic_inspiration_offer":
                await ws.send(json.dumps({"type": "bardic_inspiration_response", "use": True}))
            elif t == "party_choice_offer":
                await ws.send(json.dumps({"type": "party_choice_response", "text": "We press on."}))
            elif t == "awaiting_input":
                if current is not None:
                    transcript.append(current)
                    current = None
                if sent >= max_actions:
                    break
                cid = msg["actor"]
                cls = by_id[cid]
                if rejects[cid] >= 2 or not queue[cid]:
                    text = "I attack the nearest goblin"
                    rejects[cid] = 0
                else:
                    text = queue[cid].pop(0)
                current = {
                    "cid": cid,
                    "actor": f"{names[cid]} ({cls})",
                    "text": text,
                    "events": [],
                    "errors": [],
                    "narration": [],
                }
                sent += 1
                print(f"[{sent}] {current['actor']}: {text}")
                await ws.send(json.dumps({"type": "player_action", "text": text}))
        if current is not None:
            transcript.append(current)

    write_transcript(group, classes, transcript, status)


def write_transcript(
    group: int, classes: list[str], transcript: list[dict[str, Any]], status: str
) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Live game: {', '.join(classes)} (group {group})",
        "",
        f"Final status: **{status}**",
        "",
    ]
    for i, step in enumerate(transcript, 1):
        who = step["actor"]
        if step["text"]:
            lines.append(f'### {i}. {who}: "{step["text"]}"')
        else:
            lines.append(f"### {i}. (monsters/companions)")
        for ev in step["events"]:
            lines.append(f"- {ev}")
        for err in step["errors"]:
            lines.append(f"- **REJECTED:** {err}")
        for n in step["narration"]:
            lines.append(f"  > {n}")
        lines.append("")
    path = OUT_DIR / f"live_{group}_{'_'.join(classes)}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    (OUT_DIR / f"live_{group}.json").write_text(json.dumps(transcript, indent=1), encoding="utf-8")
    print(f"wrote {path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", type=int, default=0)
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--max-actions", type=int, default=14)
    args = ap.parse_args()
    asyncio.run(drive(args.group, args.port, args.max_actions))


if __name__ == "__main__":
    main()
