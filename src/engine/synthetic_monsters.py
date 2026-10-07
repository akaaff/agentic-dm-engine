"""Stat blocks the SRD's spells describe but its monster list doesn't contain (issue #56).

Animate Objects turns ordinary objects into creatures whose statistics come from a table in
the spell text, one row per size - there is no monster entry for them. Rather than teach the
turn engine a second way to attack, these are built in the exact shape of a vendored SRD
monster (`actions`, `armor_class`, `hit_points`, ability scores...) and merged into
`SrdIndex.monsters` by `srd_loader.load_srd`, so `monster_to_character`, monster_ai, attack
resolution, resistances and condition immunities all work on them unchanged.

Every entry carries `"synthetic": True` so anything that enumerates the monster list (portrait
generation) can tell it isn't real SRD content. Campaign generation only ever picks from its own
allowlist, so these can't turn up as random enemies."""

from __future__ import annotations

from typing import Any

# size -> (hit points, AC, attack bonus, damage dice, damage average, STR, DEX), from the table
# in the Animate Objects spell text.
ANIMATED_OBJECT_TABLE: dict[str, tuple[int, int, int, str, int, int, int]] = {
    "tiny": (20, 18, 8, "1d4+4", 6, 4, 18),
    "small": (25, 16, 6, "1d8+2", 6, 6, 14),
    "medium": (40, 13, 5, "2d6+1", 8, 10, 12),
    "large": (50, 10, 6, "2d10+2", 13, 14, 10),
    "huge": (80, 10, 8, "2d12+4", 17, 18, 6),
}

ANIMATED_OBJECT_COST = {"tiny": 1, "small": 1, "medium": 2, "large": 4, "huge": 8}
"""How many of the spell's ten object slots one object of each size uses."""

_CONDITION_IMMUNITIES = (
    "blinded",
    "charmed",
    "deafened",
    "frightened",
    "paralyzed",
    "petrified",
    "poisoned",
)


def animated_object_index(size: str) -> str:
    return f"animated-object-{size}"


def _animated_object(size: str) -> dict[str, Any]:
    hp, ac, bonus, dice, average, strength, dexterity = ANIMATED_OBJECT_TABLE[size]
    return {
        "index": animated_object_index(size),
        "name": "Animated Object",
        "synthetic": True,
        "size": size.capitalize(),
        "type": "construct",
        "alignment": "unaligned",
        "armor_class": [{"type": "natural", "value": ac}],
        "hit_points": hp,
        "speed": {"walk": "30 ft."},
        "strength": strength,
        "dexterity": dexterity,
        "constitution": 10,
        "intelligence": 3,
        "wisdom": 3,
        "charisma": 1,
        "proficiencies": [],
        "damage_vulnerabilities": [],
        "damage_resistances": [],
        "damage_immunities": ["poison", "psychic"],
        "condition_immunities": [
            {"index": c, "name": c.capitalize()} for c in _CONDITION_IMMUNITIES
        ],
        "senses": {"truesight": "30 ft. (blind beyond this radius)", "passive_perception": 6},
        "languages": "",
        "challenge_rating": 0.25,
        "proficiency_bonus": 2,
        "special_abilities": [],
        "actions": [
            {
                "name": "Slam",
                "desc": (
                    f"Melee Weapon Attack: +{bonus} to hit, reach 5 ft., one target. "
                    f"Hit: {average} ({dice.replace('+', ' + ')}) bludgeoning damage."
                ),
                "attack_bonus": bonus,
                "damage": [
                    {
                        "damage_type": {"index": "bludgeoning", "name": "Bludgeoning"},
                        "damage_dice": dice,
                    }
                ],
            }
        ],
    }


SYNTHETIC_MONSTERS: dict[str, dict[str, Any]] = {
    animated_object_index(size): _animated_object(size) for size in ANIMATED_OBJECT_TABLE
}
