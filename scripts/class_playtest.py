"""Class playability QA harness.

For every SRD class, runs the things that class actually does in a real game -
typed the way a player would type them - through the REAL pipeline: the live
intent parser (Ollama teacher model) -> `parse_intent_sequence` ->
`turn_engine.resolve_action`. Each "play" is one utterance against a fresh,
small hand-built encounter (the class under test + a fighter buddy vs the
enemies the play names), with rigged dice so the outcome is deterministic and a
check can assert on mechanics (a failed play is a parser problem, an engine
rejection, or a mechanic that silently does nothing - each reported
differently). `--trials N` repeats the parse (the only non-deterministic part).

Also: `--sweep` casts every level-0/1 spell on each class's list through the
real `cast_spell` path (what is castable at all), and `--static` runs
creation-time checks (features a class is supposed to start with).

    uv run python scripts/class_playtest.py --classes fighter,rogue --trials 2
    uv run python scripts/class_playtest.py --sweep
    uv run python scripts/class_playtest.py --static

Results go to docs/class-playtest/ (json + a readable report)."""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from class_playtest_builds import BUILDS, build  # noqa: E402

from src.engine import conditions as conditions_mod  # noqa: E402
from src.engine.actions import ParsedAction  # noqa: E402
from src.engine.encounter import monster_to_character  # noqa: E402
from src.engine.events import Event  # noqa: E402
from src.engine.position import BattleMap, Position  # noqa: E402
from src.engine.srd_loader import SrdIndex, load_srd  # noqa: E402
from src.engine.state import Character, Condition, GameState  # noqa: E402
from src.engine.turn_engine import (  # noqa: E402
    BardicChoicePending,
    TurnEngineError,
    resolve_action,
    resolve_pending_bardic_choice,
)
from src.graph.nodes.intent_parser import parse_intent_sequence  # noqa: E402

OUT_DIR = REPO / "docs" / "class-playtest"

# ----------------------------------------------------------------- dice rig


class RiggedRng(random.Random):
    """d20s come from a fixed value, every other die rolls its (rounded-up)
    average: d4=3, d6=4, d8=5, d10=6, d12=7. So an attack at d20=14 hits any AC
    a level-1 character plausibly faces, a save at d20=2 fails, and damage is
    exactly computable by hand."""

    def __init__(self, d20: int | list[int] = 14) -> None:
        super().__init__(0)
        self._d20 = list(d20) if isinstance(d20, list) else [d20]
        self._i = 0

    def randint(self, a: int, b: int) -> int:
        if a == 1 and b == 20:
            value = self._d20[min(self._i, len(self._d20) - 1)]
            self._i += 1
            return value
        return (a + b + 1) // 2

    def shuffle(self, x: Any, random: Any = None) -> None:  # initiative order is set by hand
        return None


# ------------------------------------------------------------ state builder

MAP_W, MAP_H = 14, 7
ACTOR_POS = (2, 3)
BUDDY_POS = (2, 4)

Enemy = tuple[str, str, int, int]  # (monster_index, character_id, x, y)

ADJ: Enemy = ("goblin", "goblin_1", 3, 3)  # 5ft from the actor
MID: Enemy = ("goblin", "goblin_1", 8, 3)  # 30ft
OGRE_ADJ: Enemy = ("ogre", "ogre_1", 3, 3)  # 59 HP, AC 11: a damage sponge, adjacent
OGRE_MID: Enemy = ("ogre", "ogre_1", 8, 3)
PACK_MID: list[Enemy] = [
    ("goblin", "goblin_1", 7, 2),
    ("goblin", "goblin_2", 7, 3),
    ("goblin", "goblin_3", 7, 4),
]
PACK_ADJ: list[Enemy] = [
    ("goblin", "goblin_1", 3, 2),
    ("goblin", "goblin_2", 3, 3),
    ("goblin", "goblin_3", 3, 4),
]


def open_map() -> BattleMap:
    return BattleMap(
        width=MAP_W,
        height=MAP_H,
        terrain=[["floor"] * MAP_W for _ in range(MAP_H)],
        spawn_points={},
    )


def make_state(
    actor_class: str,
    srd: SrdIndex,
    enemies: list[Enemy],
    *,
    build_overrides: dict[str, Any] | None = None,
    buddy: bool = True,
    buddy_pos: tuple[int, int] = BUDDY_POS,
) -> tuple[GameState, Character]:
    actor = build(actor_class, srd, **(build_overrides or {}))
    actor.position = Position(x=ACTOR_POS[0], y=ACTOR_POS[1])
    characters: dict[str, Character] = {actor.id: actor}
    if buddy:
        pal = build("fighter", srd, character_id="buddy", name="Buddy")
        pal.position = Position(x=buddy_pos[0], y=buddy_pos[1])
        characters[pal.id] = pal
    for monster_index, cid, x, y in enemies:
        characters[cid] = monster_to_character(srd.monsters[monster_index], cid, Position(x=x, y=y))
    order = [actor.id, *[c for c in characters if c != actor.id]]
    state = GameState(
        encounter_id="class_playtest",
        characters=characters,
        turn_order=order,
        current_turn=0,
        round=1,
        events=[],
        status="in_progress",
        battle_map=open_map(),
    )
    return state, actor


# --------------------------------------------------------------- play model


@dataclass
class Ctx:
    state: GameState
    actor: Character
    srd: SrdIndex
    actions: list[ParsedAction]
    errors: list[str]
    events: list[Event]
    parse_error: str | None = None
    pending_bardic: bool = False

    def ev(self, type_: str, **match: Any) -> list[Event]:
        return [
            e
            for e in self.events
            if e.type == type_ and all(e.payload.get(k) == v for k, v in match.items())
        ]

    def char(self, cid: str) -> Character:
        return self.state.characters[cid]

    def has_cond(self, cid: str, name: str) -> bool:
        return any(c.name == name for c in self.state.characters[cid].conditions)

    @property
    def verbs(self) -> list[str]:
        return [a.verb for a in self.actions]

    @property
    def turn_still_mine(self) -> bool:
        return self.state.turn_order[self.state.current_turn] == self.actor.id


Check = Callable[[Ctx], list[str]]


def no_errors(ctx: Ctx) -> list[str]:
    problems = []
    if ctx.parse_error:
        problems.append(f"parser crashed: {ctx.parse_error}")
    if not ctx.actions:
        problems.append("parser returned no actions")
    if any(v == "invalid" for v in ctx.verbs):
        problems.append(f"parsed as invalid: {[a.raw_text for a in ctx.actions]}")
    problems.extend(f"engine rejected: {e}" for e in ctx.errors)
    return problems


def need(label: str, predicate: Callable[[Ctx], bool]) -> Check:
    def check(ctx: Ctx) -> list[str]:
        return [] if predicate(ctx) else [label]

    return check


def note(label: str, predicate: Callable[[Ctx], bool]) -> Check:
    """An observation, not a failure: reported but does not fail the play."""

    def check(ctx: Ctx) -> list[str]:
        return [] if predicate(ctx) else [f"NOTE: {label}"]

    return check


def has_event(type_: str, **match: Any) -> Check:
    return need(f"no {type_} event {match or ''}", lambda c: bool(c.ev(type_, **match)))


@dataclass
class Play:
    cls: str
    name: str
    utterance: str
    enemies: list[Enemy]
    checks: list[Check] = field(default_factory=list)
    d20: int | list[int] = 14
    setup: Callable[[GameState, Character], None] | None = None
    build_overrides: dict[str, Any] | None = None
    buddy_pos: tuple[int, int] = BUDDY_POS
    why: str = ""
    """Why a real player of this class does this - kept with the play so the
    report reads as a play-style walkthrough, not a bag of utterances."""
    expect_error: bool = False
    """The engine is supposed to REJECT this (an illegal play): passes only if
    the action was refused."""


def run_play(play: Play, srd: SrdIndex) -> dict[str, Any]:
    state, actor = make_state(
        play.cls,
        srd,
        play.enemies,
        build_overrides=play.build_overrides,
        buddy_pos=play.buddy_pos,
    )
    if play.setup:
        play.setup(state, actor)
    rng = RiggedRng(play.d20)
    ctx = Ctx(state=state, actor=actor, srd=srd, actions=[], errors=[], events=[])
    t0 = time.time()
    try:
        ctx.actions = parse_intent_sequence(
            {
                "game_state": state,
                "raw_text": play.utterance,
                "parsed_action": None,
                "events_before": len(state.events),
                "round_before": state.round,
                "narration": None,
                "scene_image_url": None,
            }
        )
    except Exception as exc:  # the parser is the thing under test
        ctx.parse_error = f"{type(exc).__name__}: {exc}"
    parse_s = time.time() - t0

    events_before = len(state.events)
    for action in ctx.actions:
        if action.verb == "invalid":
            break
        who = state.turn_order[state.current_turn]
        try:
            state = resolve_action(state, action, rng, srd)
        except BardicChoicePending as pending:
            ctx.pending_bardic = True
            resolve_pending_bardic_choice(state, pending.choice, False, rng, srd)
        except (TurnEngineError, NotImplementedError) as exc:
            ctx.errors.append(f"{type(exc).__name__}: {exc}")
            break
        ctx.state = state
        if state.turn_order[state.current_turn] != who or state.status != "in_progress":
            break
    ctx.events = state.events[events_before:]

    problems: list[str] = []
    base_checks: list[Check] = (
        [need("illegal play was accepted", lambda c: bool(c.errors))]
        if play.expect_error
        else [no_errors]
    )
    for check in [*base_checks, *play.checks]:
        problems.extend(check(ctx))
    hard = [p for p in problems if not p.startswith("NOTE:")]
    return {
        "class": play.cls,
        "play": play.name,
        "utterance": play.utterance,
        "why": play.why,
        "parsed": [
            {
                "verb": a.verb,
                "target": a.target,
                "targets": a.targets,
                "item_or_spell": a.item_or_spell,
                "params": a.params,
            }
            for a in ctx.actions
        ],
        "errors": ctx.errors,
        "events": [
            {"type": e.type, "actor": e.actor, "payload": _slim(e.payload)} for e in ctx.events
        ],
        "problems": problems,
        "passed": not hard,
        "parse_seconds": round(parse_s, 2),
    }


def _slim(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in {"modifier_breakdown"}}


# ------------------------------------------------------------- plays catalog


def _hp(cid: str, hp: int) -> Callable[[GameState, Character], None]:
    def setup(state: GameState, actor: Character) -> None:
        state.characters[cid].hp = hp

    return setup


def _chain(*fns: Callable[[GameState, Character], None]) -> Callable[[GameState, Character], None]:
    def setup(state: GameState, actor: Character) -> None:
        for fn in fns:
            fn(state, actor)

    return setup


def _prepare(*spells: str) -> Callable[[GameState, Character], None]:
    def setup(state: GameState, actor: Character) -> None:
        for spell in spells:
            if spell not in actor.prepared_spells:
                actor.prepared_spells.append(spell)

    return setup


def _know(*spells: str) -> Callable[[GameState, Character], None]:
    def setup(state: GameState, actor: Character) -> None:
        for spell in spells:
            if spell not in actor.known_spells:
                actor.known_spells.append(spell)

    return setup


def _dying(cid: str) -> Callable[[GameState, Character], None]:
    def setup(state: GameState, actor: Character) -> None:
        pal = state.characters[cid]
        pal.hp = 0
        conditions_mod.apply_condition(pal, Condition(name="unconscious", source="0 HP"))

    return setup


def _stealth_has_expertise(ctx: Ctx) -> bool:
    for e in ctx.ev("skill_check"):
        if any("xpertise" in str(b) for b in e.payload.get("modifier_breakdown", [])):
            return True
    stealth = ctx.ev("skill_check", skill="stealth")
    return bool(stealth) and stealth[0].payload["roll_total"] - 14 == 7


def amount(ctx: Ctx, target: str) -> int:
    return sum(e.payload.get("amount", 0) for e in ctx.ev("damage_dealt", target=target))


def build_plays() -> list[Play]:
    P = Play
    plays: list[Play] = []

    # ---------------------------------------------------------------- barbarian
    plays += [
        P(
            "barbarian",
            "rage",
            "I rage!",
            [ADJ],
            [
                need("not raging", lambda c: c.actor.is_raging),
                need("rage use not spent", lambda c: c.actor.class_resources.get("rage") == 1),
                need(
                    "rage should be a bonus action (turn still mine)", lambda c: c.turn_still_mine
                ),
            ],
            why="Rage is the barbarian's whole identity; first thing they do in every fight.",
        ),
        P(
            "barbarian",
            "rage + chop (one sentence)",
            "I fly into a rage and chop the ogre with my greataxe",
            [OGRE_ADJ],
            [
                need("not raging", lambda c: c.actor.is_raging),
                has_event("attack_roll", target="ogre_1"),
                need(
                    "damage != greataxe 1d12(7) + STR 2 + rage 2 = 11",
                    lambda c: amount(c, "ogre_1") == 11,
                ),
            ],
            why="Said in one breath; rage is a bonus action, the swing is the action.",
        ),
        P(
            "barbarian",
            "throw a javelin (not wielded)",
            "I throw a javelin at the goblin",
            [MID],
            [has_event("attack_roll", target="goblin_1")],
            why="Every barbarian carries 4 javelins: their ranged option.",
        ),
        P(
            "barbarian",
            "draw and throw a javelin",
            "I draw a javelin and hurl it at the goblin",
            [MID],
            [has_event("attack_roll", target="goblin_1")],
            why="Same, phrased as draw-then-throw (a free object interaction in 5e).",
        ),
        P(
            "barbarian",
            "grapple",
            "I grab the goblin and try to grapple it",
            [ADJ],
            [need("goblin not grappled", lambda c: c.has_cond("goblin_1", "grappled"))],
            why="Athletics is the barbarian's skill; grappling a caster/archer is a staple.",
        ),
        P(
            "barbarian",
            "shove prone",
            "I shove the goblin to knock it prone",
            [ADJ],
            [need("goblin not prone", lambda c: c.has_cond("goblin_1", "prone"))],
            why="Shove prone then everyone gets advantage in melee.",
        ),
        P(
            "barbarian",
            "intimidate",
            "I roar at the goblin to intimidate it",
            [ADJ],
            [has_event("skill_check")],
            why="Intimidation (a class skill) is the barbarian's social tool.",
        ),
        P(
            "barbarian",
            "plain greataxe swing, no rage",
            "I attack the goblin with my greataxe",
            [OGRE_ADJ],
            [need("damage != 7+2 = 9", lambda c: amount(c, "ogre_1") == 9)],
        ),
    ]

    # --------------------------------------------------------------------- bard
    plays += [
        P(
            "bard",
            "vicious mockery",
            "I cast vicious mockery at the goblin",
            [MID],
            [
                has_event("saving_throw", kind="spell_save"),
                need(
                    "no psychic damage (save forced to fail)", lambda c: amount(c, "goblin_1") > 0
                ),
                note(
                    "no disadvantage rider on the mocked goblin's next attack",
                    lambda c: bool(c.char("goblin_1").conditions) or c.char("goblin_1").is_dodging,
                ),
            ],
            d20=2,
            why="The bard's signature cantrip: damage + disadvantage on the target's next attack.",
        ),
        P(
            "bard",
            "inspire an ally",
            "I inspire Buddy with a rousing speech",
            [ADJ],
            [
                need(
                    "ally has no inspiration die",
                    lambda c: bool(c.char("buddy").bardic_inspiration_die),
                ),
                need("bonus action should not end the turn", lambda c: c.turn_still_mine),
            ],
            why="Bardic Inspiration - the bard's main contribution every combat.",
        ),
        P(
            "bard",
            "healing word on a downed ally",
            "I cast healing word on Buddy",
            [ADJ],
            [
                need("ally not healed", lambda c: bool(c.ev("hp_change", target="buddy"))),
                need("healing word should be a bonus action", lambda c: c.turn_still_mine),
            ],
            setup=_hp("buddy", 2),
            why="Pick-up healing from 60ft away as a bonus action, then keep fighting.",
        ),
        P(
            "bard",
            "sleep on a pack",
            "I cast sleep on the goblins",
            PACK_MID,
            [
                need(
                    "not all three goblins asleep",
                    lambda c: all(c.has_cond(f"goblin_{i}", "unconscious") for i in (1, 2, 3)),
                ),
            ],
            why="Sleep is the best level-1 spell against low-HP packs.",
        ),
        P(
            "bard",
            "thunderwave",
            "I cast thunderwave on the goblin",
            [ADJ],
            [
                need("no thunder damage", lambda c: amount(c, "goblin_1") > 0),
                note(
                    "goblin was not pushed 10ft away",
                    lambda c: c.char("goblin_1").position != Position(x=3, y=3),
                ),
            ],
            d20=2,
            why="Close-range blast + shove; the bard's panic button.",
        ),
        P(
            "bard",
            "charm person",
            "I cast charm person on the goblin",
            [MID],
            [
                need("goblin not charmed", lambda c: c.has_cond("goblin_1", "charmed")),
            ],
            d20=2,
            why="Social bards charm a leader to skip a fight.",
        ),
        P(
            "bard",
            "rapier attack",
            "I stab the goblin with my rapier",
            [OGRE_ADJ],
            [need("damage != rapier 1d8(5) + DEX 2 = 7", lambda c: amount(c, "ogre_1") == 5 + 2)],
        ),
        P(
            "bard",
            "talk the goblin down",
            "I try to persuade the goblin to surrender",
            [ADJ],
            [
                has_event("skill_check"),
                note(
                    "persuasion roll has no effect on the goblin (flat-DC check)",
                    lambda c: bool(c.char("goblin_1").conditions),
                ),
            ],
            why="Face of the party: talk before fighting.",
        ),
        P(
            "bard",
            "cure wounds",
            "I cast cure wounds on Buddy",
            [ADJ],
            [need("not healed", lambda c: bool(c.ev("hp_change", target="buddy")))],
            setup=_chain(_hp("buddy", 2), _know("cure-wounds")),
        ),
        P(
            "bard",
            "minor illusion (flavor cantrip)",
            "I cast minor illusion to create a decoy crate",
            [MID],
            [need("cast was rejected", lambda c: not c.errors)],
            why="Bards use utility cantrips constantly; at minimum this should not be an error.",
        ),
    ]

    # ------------------------------------------------------------------- cleric
    plays += [
        P(
            "cleric",
            "sacred flame",
            "I cast sacred flame on the goblin",
            [MID],
            [need("no radiant damage", lambda c: amount(c, "goblin_1") > 0)],
            d20=2,
            why="The cleric's go-to cantrip (ignores AC).",
        ),
        P(
            "cleric",
            "guiding bolt",
            "I cast guiding bolt at the ogre",
            [OGRE_MID],
            [
                has_event("spell_cast", target="ogre_1"),
                need("not 4d6 (16) radiant", lambda c: amount(c, "ogre_1") == 16),
                note(
                    "next attack against the ogre does not get advantage (no rider applied)",
                    lambda c: bool(c.char("ogre_1").conditions),
                ),
            ],
            why="Guiding Bolt: big damage + advantage for the next ally who hits it.",
        ),
        P(
            "cleric",
            "cure wounds (Life domain)",
            "I cast cure wounds on Buddy",
            [ADJ],
            [
                need("not healed", lambda c: bool(c.ev("hp_change", target="buddy"))),
                need(
                    "heal != 1d8(5) + WIS 3 + Disciple of Life (2+1) = 11",
                    lambda c: (
                        sum(e.payload["amount"] for e in c.ev("hp_change", target="buddy")) == 11
                    ),
                ),
            ],
            setup=_hp("buddy", 1),
            why="SRD clerics are Life domain: every healing spell heals 2 + spell level extra.",
        ),
        P(
            "cleric",
            "bless the party",
            "I cast bless on myself and Buddy",
            [ADJ],
            [
                need(
                    "not both blessed",
                    lambda c: c.has_cond("buddy", "blessed") and c.has_cond("cleric", "blessed"),
                ),
            ],
            why="Bless is the cleric's best opening round.",
        ),
        P(
            "cleric",
            "heal then fight",
            "I cast healing word on Buddy and then hit the goblin with my mace",
            [ADJ],
            [
                need("ally not healed", lambda c: bool(c.ev("hp_change", target="buddy"))),
                has_event("attack_roll", target="goblin_1"),
            ],
            setup=_hp("buddy", 2),
            why="Bonus-action heal + main-action attack in one turn.",
        ),
        P(
            "cleric",
            "spare the dying",
            "I cast spare the dying on Buddy",
            [ADJ],
            [need("buddy not stabilized", lambda c: c.char("buddy").is_stable)],
            setup=_dying("buddy"),
            why="Stop a dying ally's death saves with a cantrip.",
        ),
        P(
            "cleric",
            "command",
            "I cast command on the goblin and tell it to drop its weapon",
            [MID],
            [
                need(
                    "command had no effect on the goblin",
                    lambda c: bool(c.char("goblin_1").conditions),
                )
            ],
            setup=_prepare("command"),
            d20=2,
            why="Command is a staple 1st-level control spell.",
        ),
        P(
            "cleric",
            "heal self",
            "I cast cure wounds on myself",
            [ADJ],
            [need("not healed", lambda c: bool(c.ev("hp_change", target="cleric")))],
            setup=_hp("cleric", 2),
        ),
        P(
            "cleric",
            "mace swing",
            "I smash the goblin with my mace",
            [OGRE_ADJ],
            [need("damage != mace 1d6(4) + STR 2 = 6", lambda c: amount(c, "ogre_1") == 6)],
        ),
        P(
            "cleric",
            "guidance",
            "I cast guidance on Buddy before he tries to climb the wall",
            [MID],
            [need("rejected", lambda c: not c.errors)],
            why="Guidance (+1d4 to an ability check) is cast constantly out of combat.",
        ),
    ]

    # ------------------------------------------------------------------- druid
    plays += [
        P(
            "druid",
            "produce flame",
            "I conjure a flame in my hand and hurl it at the goblin",
            [MID],
            [has_event("spell_cast", spell="Produce Flame")],
            why="Produce Flame: light source + ranged fire cantrip.",
        ),
        P(
            "druid",
            "shillelagh",
            "I cast shillelagh on my quarterstaff and bash the goblin",
            [ADJ],
            [has_event("attack_roll", target="goblin_1")],
            why="Shillelagh makes the staff a WIS-based d8 weapon - the druid's melee build.",
        ),
        P(
            "druid",
            "entangle",
            "I cast entangle on the goblins",
            PACK_MID,
            [
                need(
                    "goblins not restrained",
                    lambda c: all(c.has_cond(f"goblin_{i}", "restrained") for i in (1, 2, 3)),
                ),
            ],
            d20=2,
            why="Entangle is the druid's battlefield control opener.",
        ),
        P(
            "druid",
            "goodberry",
            "I cast goodberry",
            [MID],
            [need("rejected", lambda c: not c.errors)],
            why="Goodberry is the druid's out-of-combat healing/food spell.",
        ),
        P(
            "druid",
            "faerie fire",
            "I cast faerie fire on the goblins",
            PACK_MID,
            [
                need(
                    "no effect on any goblin",
                    lambda c: any(c.char(f"goblin_{i}").conditions for i in (1, 2, 3)),
                )
            ],
            d20=2,
            why="Faerie Fire: advantage for the whole party against outlined targets.",
        ),
        P(
            "druid",
            "healing word",
            "I cast healing word on Buddy",
            [ADJ],
            [need("not healed", lambda c: bool(c.ev("hp_change", target="buddy")))],
            setup=_hp("buddy", 2),
        ),
        P(
            "druid",
            "poison spray",
            "I cast poison spray on the goblin",
            [ADJ],
            [need("no damage", lambda c: amount(c, "goblin_1") > 0)],
            d20=2,
        ),
        P(
            "druid",
            "quarterstaff",
            "I whack the goblin with my quarterstaff",
            [OGRE_ADJ],
            [need("damage != 1d6(4) + STR -1 = 3", lambda c: amount(c, "ogre_1") == 3)],
        ),
    ]

    # ------------------------------------------------------------------ fighter
    plays += [
        P(
            "fighter",
            "longsword",
            "I attack the ogre with my longsword",
            [OGRE_ADJ],
            [need("damage != 1d8(5)+3 = 8", lambda c: amount(c, "ogre_1") == 8)],
        ),
        P(
            "fighter",
            "second wind",
            "I use my second wind",
            [ADJ],
            [
                need("not healed", lambda c: bool(c.ev("hp_change", target="fighter"))),
                need("bonus action should not end the turn", lambda c: c.turn_still_mine),
            ],
            setup=_hp("fighter", 4),
            why="Second Wind: bonus-action heal, then still attack.",
        ),
        P(
            "fighter",
            "second wind then attack",
            "I use second wind and then attack the goblin",
            [ADJ],
            [
                need("not healed", lambda c: bool(c.ev("hp_change", target="fighter"))),
                has_event("attack_roll", target="goblin_1"),
            ],
            setup=_hp("fighter", 4),
        ),
        P(
            "fighter",
            "shove prone",
            "I shove the goblin to the ground",
            [ADJ],
            [need("not prone", lambda c: c.has_cond("goblin_1", "prone"))],
        ),
        P(
            "fighter",
            "help an ally",
            "I help Buddy hit the goblin",
            [ADJ],
            [has_event("help")],
        ),
        P(
            "fighter",
            "dodge",
            "I take the dodge action",
            [ADJ],
            [need("not dodging", lambda c: c.actor.is_dodging)],
        ),
        P(
            "fighter",
            "archer fighter",
            "I shoot the goblin with my longbow",
            [MID],
            [
                has_event("attack_roll", target="goblin_1"),
                need(
                    "Archery +2 not in the attack bonus",
                    lambda c: any(
                        "rchery" in str(b)
                        for e in c.ev("attack_roll")
                        for b in e.payload.get("attack_bonus_breakdown", [])
                    ),
                ),
            ],
            build_overrides={
                "equipment": ["longbow", "leather-armor"],
                "fighting_style": "archery",
            },
            why="Archery fighter is a very common level-1 build.",
        ),
    ]

    # --------------------------------------------------------------------- monk
    plays += [
        P(
            "monk",
            "punch",
            "I punch the ogre",
            [OGRE_ADJ],
            [
                need(
                    "a punch resolved as a weapon attack, not an unarmed strike",
                    lambda c: any(
                        e.payload.get("source") == "unarmed strike" for e in c.ev("attack_roll")
                    ),
                ),
            ],
            why="Martial arts: unarmed strikes with DEX and a d4.",
        ),
        P(
            "monk",
            "punch + bonus kick",
            "I punch the goblin and then kick it as a bonus action",
            [OGRE_ADJ],
            [
                need("fewer than 2 attack rolls", lambda c: len(c.ev("attack_roll")) == 2),
                need(
                    "the strikes were not unarmed",
                    lambda c: all(
                        e.payload.get("source") == "unarmed strike" for e in c.ev("attack_roll")
                    ),
                ),
            ],
            why="The L1 monk's whole turn: attack + martial-arts bonus strike.",
        ),
        P(
            "monk",
            "throw a dart",
            "I throw a dart at the goblin",
            [("goblin", "goblin_1", 6, 3)],
            [has_event("attack_roll", target="goblin_1")],
            why="Darts are a monk weapon for ranged pressure.",
        ),
        P(
            "monk",
            "tumble",
            "I tumble past the goblin",
            [ADJ],
            [has_event("skill_check")],
            why="Acrobatics.",
        ),
        P(
            "monk",
            "close the distance and strike",
            "I run up to the goblin and punch it",
            [("goblin", "goblin_1", 6, 3)],
            [has_event("move"), has_event("attack_roll", target="goblin_1")],
        ),
    ]

    # ------------------------------------------------------------------ paladin
    plays += [
        P(
            "paladin",
            "longsword",
            "I attack the ogre with my longsword",
            [OGRE_ADJ],
            [need("damage != longsword 1d8(5) + STR 3 = 8", lambda c: amount(c, "ogre_1") == 8)],
        ),
        P(
            "paladin",
            "lay on hands",
            "I lay hands on Buddy to heal him",
            [ADJ],
            [need("ally not healed", lambda c: bool(c.ev("hp_change", target="buddy")))],
            setup=_hp("buddy", 2),
            why="Lay on Hands is the L1 paladin's emergency heal (pool of 5 HP).",
        ),
        P(
            "paladin",
            "divine sense",
            "I use divine sense to detect any undead or fiends nearby",
            [MID],
            [need("rejected", lambda c: not c.errors)],
            why="Divine Sense: the L1 paladin's other class feature.",
        ),
        P(
            "paladin",
            "help",
            "I help Buddy attack the goblin",
            [ADJ],
            [has_event("help")],
        ),
    ]

    # ------------------------------------------------------------------- ranger
    plays += [
        P(
            "ranger",
            "longbow at range",
            "I shoot the goblin with my longbow",
            [MID],
            [has_event("attack_roll", target="goblin_1")],
            why="The ranger's bread and butter.",
        ),
        P(
            "ranger",
            "longbow at point blank",
            "I shoot the goblin with my longbow",
            [ADJ],
            [
                has_event("attack_roll", target="goblin_1"),
                need(
                    "no disadvantage on a ranged attack with a hostile adjacent (d20 18/3)",
                    lambda c: (
                        bool(c.ev("attack_roll")) and c.ev("attack_roll")[0].payload["natural"] == 3
                    ),
                ),
            ],
            d20=[18, 3],
            why="Archers get closed on; PHB p195: ranged attack at 5ft has disadvantage.",
        ),
        P(
            "ranger",
            "switch to shortsword",
            "I draw my shortsword and attack the goblin",
            [ADJ],
            [has_event("attack_roll", target="goblin_1")],
            build_overrides={"equipment": ["longbow", "shortsword"]},
            why="Archers swap to melee when something closes in.",
        ),
        P(
            "ranger",
            "track",
            "I study the ground to track where the goblins went",
            [MID],
            [has_event("skill_check", skill="survival")],
            why="Survival: a ranger's reason to exist out of combat.",
        ),
        P(
            "ranger",
            "hide",
            "I duck behind the crate and hide",
            [MID],
            [
                need(
                    "no hidden state applied",
                    lambda c: (
                        c.has_cond("ranger", "invisible")
                        or bool(c.ev("skill_check", skill="stealth"))
                    ),
                )
            ],
            why="Hide, then shoot from hiding: the canonical ranger/rogue opener.",
        ),
        P(
            "ranger",
            "spot enemies",
            "I scan the treeline for hidden enemies",
            [MID],
            [has_event("skill_check", skill="perception")],
        ),
    ]

    # -------------------------------------------------------------------- rogue
    plays += [
        P(
            "rogue",
            "sneak attack (ally adjacent)",
            "I stab the ogre with my shortsword",
            [OGRE_ADJ],
            [
                need(
                    "damage != 1d6(4)+DEX 3 + sneak 1d6(4) = 11",
                    lambda c: amount(c, "ogre_1") == 11,
                )
            ],
            why="Sneak attack with an ally flanking is the rogue's damage engine.",
        ),
        P(
            "rogue",
            "no sneak attack alone",
            "I stab the ogre with my shortsword",
            [OGRE_ADJ],
            [
                need(
                    "sneak attack applied with no ally adjacent and no advantage",
                    lambda c: amount(c, "ogre_1") == 7,
                )
            ],
            buddy_pos=(0, 0),
        ),
        P(
            "rogue",
            "shortbow",
            "I shoot the ogre with my shortbow",
            [OGRE_MID],
            [has_event("attack_roll", target="ogre_1")],
        ),
        P(
            "rogue",
            "hide",
            "I slip into the shadows and hide",
            [MID],
            [need("no hidden state applied", lambda c: c.has_cond("rogue", "invisible"))],
            why="Hide for advantage (and thus guaranteed sneak attack) - core rogue play.",
        ),
        P(
            "rogue",
            "expertise stealth",
            "I make a stealth check to sneak past the goblin",
            [ADJ],
            [
                has_event("skill_check", skill="stealth"),
                need(
                    "no expertise in the stealth bonus (DEX 3 + prof 2 + expertise 2)",
                    _stealth_has_expertise,
                ),
            ],
            why="Expertise (double proficiency in two skills) is a level-1 rogue feature.",
        ),
        P(
            "rogue",
            "throw a dagger",
            "I throw a dagger at the goblin",
            [("goblin", "goblin_1", 6, 3)],
            [has_event("attack_roll", target="goblin_1")],
        ),
        P(
            "rogue",
            "pickpocket",
            "I try to pick the goblin's pocket",
            [ADJ],
            [has_event("skill_check")],
        ),
        P(
            "rogue",
            "dual-wield",
            "I attack the ogre with my shortsword and my dagger",
            [OGRE_ADJ],
            [need("fewer than 2 attack rolls", lambda c: len(c.ev("attack_roll")) == 2)],
        ),
    ]

    # ----------------------------------------------------------------- sorcerer
    plays += [
        P(
            "sorcerer",
            "fire bolt",
            "I cast fire bolt at the ogre",
            [OGRE_MID],
            [need("damage != 1d10(6)", lambda c: amount(c, "ogre_1") == 6)],
            why="Fire Bolt is the default sorcerer/wizard attack.",
        ),
        P(
            "sorcerer",
            "magic missile",
            "I cast magic missile at the ogre",
            [OGRE_MID],
            [need("damage != 3 x (1d4+1 = 4) = 12", lambda c: amount(c, "ogre_1") == 12)],
        ),
        P(
            "sorcerer",
            "burning hands on a pack",
            "I cast burning hands on the goblins",
            PACK_ADJ,
            [
                need(
                    "not all three damaged",
                    lambda c: all(amount(c, f"goblin_{i}") > 0 for i in (1, 2, 3)),
                )
            ],
            d20=2,
            why="Burning Hands into a cluster of enemies is a classic opener.",
        ),
        P(
            "sorcerer",
            "ray of frost",
            "I cast ray of frost at the goblin",
            [MID],
            [
                has_event("spell_cast", target="goblin_1"),
                note("speed reduction not applied", lambda c: c.char("goblin_1").speed < 30),
            ],
        ),
        P(
            "sorcerer",
            "shocking grasp",
            "I cast shocking grasp on the goblin",
            [ADJ],
            [has_event("spell_cast", target="goblin_1")],
        ),
        P(
            "sorcerer",
            "fire bolt at point blank",
            "I cast fire bolt at the goblin",
            [ADJ],
            [
                has_event("spell_cast", target="goblin_1"),
                need(
                    "ranged spell attack with a hostile adjacent had no disadvantage "
                    "(d20 18/3, +5: expected roll_total 8)",
                    lambda c: (
                        bool(c.ev("spell_cast"))
                        and c.ev("spell_cast")[0].payload.get("roll_total") == 8
                    ),
                ),
            ],
            d20=[18, 3],
            why="Casters get cornered; PHB p195: point-blank ranged spells have disadvantage.",
        ),
    ]

    # ------------------------------------------------------------------ warlock
    plays += [
        P(
            "warlock",
            "eldritch blast",
            "I cast eldritch blast at the ogre",
            [OGRE_MID],
            [need("damage != 1d10(6)", lambda c: amount(c, "ogre_1") == 6)],
            why="Eldritch Blast: the warlock's every-turn attack.",
        ),
        P(
            "warlock",
            "hellish rebuke",
            "I cast hellish rebuke on the goblin",
            [ADJ],
            [need("no fire damage", lambda c: amount(c, "goblin_1") > 0)],
            d20=2,
            why="The Fiend warlock's reaction spell (2d10 fire).",
        ),
        P(
            "warlock",
            "charm person",
            "I cast charm person on the goblin",
            [MID],
            [need("goblin not charmed", lambda c: c.has_cond("goblin_1", "charmed"))],
            d20=2,
        ),
        P(
            "warlock",
            "off-list spell is refused",
            "I cast cure wounds on Buddy",
            [ADJ],
            [],
            setup=_hp("buddy", 2),
            expect_error=True,
            why="Cure Wounds is not a warlock spell; a warlock shouldn't be able to cast it.",
        ),
        P(
            "warlock",
            "dagger",
            "I stab the goblin with my dagger",
            [OGRE_ADJ],
            [need("damage != 1d4(3)+DEX 2 = 5", lambda c: amount(c, "ogre_1") == 5)],
        ),
    ]

    # ------------------------------------------------------------------- wizard
    plays += [
        P(
            "wizard",
            "fire bolt",
            "I cast fire bolt at the ogre",
            [OGRE_MID],
            [need("damage != 1d10(6)", lambda c: amount(c, "ogre_1") == 6)],
        ),
        P(
            "wizard",
            "magic missile",
            "I cast magic missile at the ogre",
            [OGRE_MID],
            [need("damage != 12", lambda c: amount(c, "ogre_1") == 12)],
        ),
        P(
            "wizard",
            "sleep a pack",
            "I cast sleep on the goblins",
            PACK_MID,
            [
                need(
                    "not all asleep",
                    lambda c: all(c.has_cond(f"goblin_{i}", "unconscious") for i in (1, 2, 3)),
                ),
            ],
        ),
        P(
            "wizard",
            "shield",
            "I cast shield",
            [ADJ],
            [
                need(
                    "no AC bonus applied",
                    lambda c: c.actor.ac > 13 or c.has_cond("wizard", "warded"),
                )
            ],
            why="Shield (+5 AC as a reaction) is the wizard's signature defensive spell.",
        ),
        P(
            "wizard",
            "mage armor",
            "I cast mage armor on myself",
            [MID],
            [need("AC != 13 + DEX 3 = 16", lambda c: c.actor.ac == 16)],
            setup=_prepare("mage-armor"),
        ),
        P(
            "wizard",
            "find familiar",
            "I cast find familiar",
            [MID],
            [need("rejected", lambda c: not c.errors)],
            setup=_prepare("find-familiar"),
            why="Find Familiar is a staple (scout + Help action).",
        ),
        P(
            "wizard",
            "dagger",
            "I stab the goblin with my dagger",
            [OGRE_ADJ],
            [need("damage != 1d4(3)+DEX 3 = 6", lambda c: amount(c, "ogre_1") == 6)],
        ),
    ]

    # ------------------------------- bonus action AFTER the main action (any class)
    plays += [
        P(
            "fighter",
            "attack, then second wind",
            "I attack the goblin and then use second wind",
            [ADJ],
            [
                has_event("attack_roll", target="goblin_1"),
                need(
                    "second wind never happened",
                    lambda c: bool(c.ev("hp_change", target="fighter")),
                ),
            ],
            setup=_hp("fighter", 4),
            why="Players name the main action first and the bonus action second.",
        ),
        P(
            "cleric",
            "attack, then healing word",
            "I hit the goblin with my mace and then cast healing word on Buddy",
            [ADJ],
            [
                has_event("attack_roll", target="goblin_1"),
                need(
                    "healing word never happened", lambda c: bool(c.ev("hp_change", target="buddy"))
                ),
            ],
            setup=_hp("buddy", 2),
        ),
        P(
            "druid",
            "attack, then healing word",
            "I whack the goblin with my staff and then cast healing word on Buddy",
            [ADJ],
            [
                has_event("attack_roll", target="goblin_1"),
                need(
                    "healing word never happened", lambda c: bool(c.ev("hp_change", target="buddy"))
                ),
            ],
            setup=_hp("buddy", 2),
        ),
        P(
            "barbarian",
            "attack, then rage",
            "I swing my greataxe at the goblin and rage",
            [ADJ],
            [
                has_event("attack_roll", target="goblin_1"),
                need("not raging", lambda c: c.actor.is_raging),
            ],
        ),
    ]

    # ------------------------------------------------ rules that should hold for anyone
    plays += [
        P(
            "rogue",
            "sneak attack once per turn when dual-wielding",
            "I attack the ogre with my shortsword and my dagger",
            [OGRE_ADJ],
            [
                need(
                    "sneak attack applied more than once in one turn",
                    lambda c: (
                        sum(1 for e in c.ev("attack_roll") if e.payload.get("sneak_attack_damage"))
                        <= 1
                    ),
                ),
            ],
            why="PHB: Sneak Attack is once per turn.",
        ),
        P(
            "fighter",
            "shoved target gets back up",
            "I shove the goblin to the ground",
            [ADJ],
            [
                need("goblin is prone", lambda c: c.has_cond("goblin_1", "prone")),
                need(
                    "prone has no expiry/stand-up path (condition has no duration)",
                    lambda c: any(
                        x.name == "prone" and x.duration_rounds is not None
                        for x in c.char("goblin_1").conditions
                    ),
                ),
            ],
            why="Prone should end when the creature stands (half its movement).",
        ),
    ]

    # --------------------------------------------------- common actions (fighter)
    plays += [
        P("fighter", "common: dash", "I dash toward the goblin", [MID], [has_event("move")]),
        P(
            "fighter",
            "common: disengage",
            "I disengage and back away from the goblin",
            [ADJ],
            [has_event("disengage")],
        ),
        P(
            "fighter",
            "common: ready an action",
            "I ready my sword to strike the goblin the moment it comes into reach",
            [MID],
            [need("rejected", lambda c: not c.errors)],
            why="Ready is a core 5e action.",
        ),
        P(
            "fighter",
            "common: drink a potion",
            "I drink my healing potion",
            [ADJ],
            [need("not healed", lambda c: bool(c.ev("hp_change", source="potion-of-healing")))],
            setup=_hp("fighter", 4),
        ),
        P(
            "fighter",
            "common: break a grapple",
            "I try to break free from the goblin's grip",
            [ADJ],
            [need("not freed", lambda c: not c.has_cond("fighter", "grappled"))],
            setup=lambda s, a: conditions_mod.apply_condition(
                a, Condition(name="grappled", source="goblin_1")
            ),
            why="Escaping a grapple is an action in 5e.",
        ),
        P(
            "fighter",
            "common: stand up from prone",
            "I get back up",
            [ADJ],
            [need("still prone", lambda c: not c.has_cond("fighter", "prone"))],
            setup=lambda s, a: conditions_mod.apply_condition(
                a, Condition(name="prone", source="goblin_1")
            ),
        ),
        P(
            "fighter",
            "common: search",
            "I search the room for anything hidden",
            [MID],
            [has_event("skill_check")],
        ),
    ]
    return plays


# --------------------------------------------------------------- static checks


def static_checks(srd: SrdIndex) -> list[dict[str, Any]]:
    """Creation-time facts - what a class is supposed to start with."""
    from src.engine.character_creation import CharacterCreationError
    from src.engine.resting import apply_short_rest

    out: list[dict[str, Any]] = []

    def rec(cls: str, name: str, ok: bool, detail: str) -> None:
        out.append({"class": cls, "check": name, "ok": ok, "detail": detail})

    def try_create(cls: str, **kw: Any) -> tuple[Character | None, str]:
        try:
            return build(cls, srd, **kw), ""
        except CharacterCreationError as exc:
            return None, str(exc)

    # Fighting styles: every SRD style (PHB p72) is a legitimate L1 choice.
    for style in (
        "archery",
        "defense",
        "dueling",
        "great-weapon-fighting",
        "protection",
        "two-weapon-fighting",
    ):
        eq = {"great-weapon-fighting": ["greatsword"], "archery": ["longbow"]}.get(
            style, ["longsword"]
        )
        c, err = try_create("fighter", fighting_style=style, equipment=eq)
        rec("fighter", f"fighting style: {style}", c is not None, err or "ok")

    # Cleric: Life domain (the only SRD domain) grants heavy armor.
    c, err = try_create("cleric", equipment=["mace", "chain-mail"])
    rec("cleric", "Life domain heavy armor (chain mail)", c is not None, err or "ok")

    # Sorcerer: Draconic Resilience (the only SRD origin): +1 HP/level, AC 13 + DEX unarmored.
    c, _ = try_create("sorcerer")
    if c:
        rec("sorcerer", "Draconic Resilience AC (13 + DEX 2 = 15)", c.ac == 15, f"AC {c.ac}")
        rec(
            "sorcerer",
            "Draconic Resilience HP (+1)",
            c.max_hp == 9,
            f"max_hp {c.max_hp} (6 + CON 2 + 1 = 9)",
        )

    # Warlock: pact slots refresh on a SHORT rest.
    c, _ = try_create("warlock")
    if c:
        c.spell_slots[1] = 0
        c.hp = 3
        apply_short_rest([c], RiggedRng())
        rec(
            "warlock",
            "Pact Magic slots refresh on short rest",
            c.spell_slots.get(1, 0) == 1,
            f"slots {c.spell_slots}",
        )
        rec(
            "warlock",
            "has any spell list / known spells",
            bool(c.known_spells),
            f"known {c.known_spells}",
        )

    # Wizard: Arcane Recovery on short rest.
    c, _ = try_create("wizard")
    if c:
        c.spell_slots[1] = 0
        apply_short_rest([c], RiggedRng())
        rec(
            "wizard",
            "Arcane Recovery on short rest",
            c.spell_slots.get(1, 0) >= 1,
            f"slots {c.spell_slots}",
        )

    # Class resources every L1 class feature should provide.
    expectations = {
        "paladin": ("Lay on Hands pool", "lay_on_hands"),
        "barbarian": ("Rage uses", "rage"),
        "fighter": ("Second Wind", "second_wind"),
        "bard": ("Bardic Inspiration", "bardic_inspiration"),
        "wizard": ("Arcane Recovery", "arcane_recovery"),
    }
    for cls, (label, key) in expectations.items():
        c, _ = try_create(cls)
        if c:
            rec(cls, f"class resource: {label}", key in c.class_resources, f"{c.class_resources}")

    # Rogue Expertise / skill expertise field.
    c, _ = try_create("rogue")
    if c:
        has = any("xpert" in f for f in c.model_fields) or hasattr(c, "expertise")
        rec(
            "rogue",
            "Expertise is modeled on the character",
            bool(has),
            "no expertise field" if not has else "ok",
        )

    # Class features surfaced anywhere for the player?
    for cls in BUILDS:
        c, _ = try_create(cls)
        if c:
            names = [f for f in c.model_fields if "feature" in f]
            rec(cls, "class features list on the character", bool(names), f"fields: {names}")

    # Cantrips / spell lists available per class at level 0 and 1.
    from src.engine.rules import class_spell_indices

    for cls in BUILDS:
        rec(
            cls,
            "spell list (L0 / L1 count)",
            True,
            f"{len(class_spell_indices(cls, srd, 0))} cantrips / "
            f"{len(class_spell_indices(cls, srd, 1))} L1 spells",
        )
    return out


# ---------------------------------------------------------------- spell sweep


def spell_sweep(srd: SrdIndex) -> list[dict[str, Any]]:
    """Every level-0/1 spell on each class's list, cast for real."""
    from src.engine.rules import class_spell_indices, spell_mechanic

    results: list[dict[str, Any]] = []
    for cls in BUILDS:
        if cls in {"barbarian", "fighter", "monk", "rogue", "paladin", "ranger"}:
            continue
        indices = sorted(class_spell_indices(cls, srd, 0) | class_spell_indices(cls, srd, 1))
        for index in indices:
            spell = srd.spells[index]
            status = "?"
            detail = ""
            for target_id in ("goblin_1", "buddy", cls):
                state, actor = make_state(cls, srd, [ADJ])
                actor.prepared_spells.append(index)
                actor.known_spells.append(index)
                actor.spell_slots[1] = max(actor.spell_slots.get(1, 0), 2)
                state.characters["buddy"].hp = 3
                try:
                    resolve_action(
                        state,
                        ParsedAction(
                            actor=actor.id,
                            verb="cast_spell",
                            target=target_id,
                            targets=[target_id],
                            item_or_spell=index,
                            raw_text=f"cast {index}",
                        ),
                        RiggedRng(2),
                        srd,
                    )
                    status = "castable"
                    detail = f"on {target_id}: " + ",".join(sorted({e.type for e in state.events}))
                    break
                except (TurnEngineError, NotImplementedError) as exc:
                    detail = str(exc)
                    if "not supported" in detail or "isn't supported" in detail:
                        status = "UNSUPPORTED"
                        break
                    status = "error"
            results.append(
                {
                    "class": cls,
                    "spell": index,
                    "level": spell.get("level"),
                    "mechanic": spell_mechanic(spell),
                    "status": status,
                    "detail": detail[:160],
                }
            )
    return results


# ------------------------------------------------------------------- driver


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--classes", default="", help="comma list (default all)")
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--play", default="", help="only plays whose name contains this")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--static", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    srd = load_srd()

    if args.static:
        rows = static_checks(srd)
        (OUT_DIR / "static.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
        for r in rows:
            print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['class']:10} {r['check']}: {r['detail']}")
        return

    if args.sweep:
        rows = spell_sweep(srd)
        (OUT_DIR / "spell_sweep.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
        for r in rows:
            print(
                f"{r['status']:12} {r['class']:9} L{r['level']} {r['spell']:28} "
                f"{r['mechanic']}  {r['detail'][:70]}"
            )
        return

    wanted = {c for c in args.classes.split(",") if c} or None
    plays = [p for p in build_plays() if wanted is None or p.cls in wanted]
    plays = [p for p in plays if args.play in p.name]
    results: list[dict[str, Any]] = []
    for play in plays:
        trials = [run_play(play, srd) for _ in range(args.trials)]
        passed = sum(t["passed"] for t in trials)
        first_fail = next((t for t in trials if not t["passed"]), trials[0])
        record = {
            **first_fail,
            "trials": args.trials,
            "passed_trials": passed,
            "all_trials": trials,
        }
        results.append(record)
        mark = "PASS" if passed == args.trials else ("FLAKY" if passed else "FAIL")
        print(f"[{mark} {passed}/{args.trials}] {play.cls}: {play.name} :: {play.utterance!r}")
        if passed != args.trials:
            for problem in first_fail["problems"]:
                print(f"      - {problem}")
            parsed = [(a["verb"], a["target"], a["item_or_spell"]) for a in first_fail["parsed"]]
            print(f"      parsed: {parsed}")
        sys.stdout.flush()
    out = (
        Path(args.out)
        if args.out
        else OUT_DIR / f"plays_{'_'.join(sorted(wanted)) if wanted else 'all'}.json"
    )
    out.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
