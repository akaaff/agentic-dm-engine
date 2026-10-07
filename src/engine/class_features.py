"""The SRD 5.1 level-1 class features, as plain name + description + how this game
treats them (issue #103). The vendored SRD data carries no features file (a class
entry only links to its level table), so these are written here, paraphrasing the SRD,
and shown on the character creator and the sheet next to the resource counters.

`note` says honestly what the engine does with a feature: most are fully modeled
(Rage, Second Wind, Sneak Attack...), a few are partial, and a few are flavor with no
mechanical effect in this game - a player reading the sheet shouldn't have to guess
which."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClassFeature:
    name: str
    desc: str
    note: str | None = None
    """None for a fully modeled feature; otherwise what is missing or flavor-only."""


_FLAVOR = "Flavor only - no mechanical effect in this game."

LEVEL_1_FEATURES: dict[str, list[ClassFeature]] = {
    "barbarian": [
        ClassFeature(
            "Rage",
            "As a bonus action, enter a rage: bonus damage on melee attacks and resistance "
            "to bludgeoning, piercing and slashing damage. Two uses per long rest.",
            "A rage lasts until a rest here, rather than ending after a minute of no fighting.",
        ),
        ClassFeature(
            "Unarmored Defense",
            "While wearing no armor, your AC is 10 + Dexterity + Constitution. A shield is "
            "allowed.",
        ),
    ],
    "bard": [
        ClassFeature(
            "Spellcasting",
            "You cast bard spells using Charisma, knowing a fixed list of spells.",
        ),
        ClassFeature(
            "Bardic Inspiration",
            "As a bonus action, give an ally a d6 to add to a later roll. Uses equal to your "
            "Charisma modifier per long rest.",
            "The die can only be added to an attack roll, not an ability check or a save.",
        ),
    ],
    "cleric": [
        ClassFeature(
            "Spellcasting",
            "You cast cleric spells using Wisdom, preparing a list from the whole class list "
            "each day.",
        ),
        ClassFeature(
            "Divine Domain: Life",
            "Disciple of Life: your healing spells restore an extra 2 + the spell's level. "
            "Proficiency with heavy armor. Bless and Cure Wounds are always prepared.",
        ),
    ],
    "druid": [
        ClassFeature(
            "Druidic",
            "You know Druidic, the secret language of druids, and can leave hidden messages in it.",
            _FLAVOR,
        ),
        ClassFeature(
            "Spellcasting",
            "You cast druid spells using Wisdom, preparing a list from the whole class list "
            "each day.",
        ),
    ],
    "fighter": [
        ClassFeature(
            "Fighting Style",
            "You adopt a style of fighting as your specialty - Archery, Defense, Dueling, "
            "Great Weapon Fighting, Protection or Two-Weapon Fighting.",
        ),
        ClassFeature(
            "Second Wind",
            "As a bonus action, regain 1d10 + your level hit points. Once per short or long rest.",
        ),
    ],
    "monk": [
        ClassFeature(
            "Unarmored Defense",
            "While wearing no armor and no shield, your AC is 10 + Dexterity + Wisdom.",
        ),
        ClassFeature(
            "Martial Arts",
            "Unarmed strikes and monk weapons use Dexterity if it is higher, deal a martial "
            "arts die instead of the weapon's, and you can make one unarmed strike as a "
            "bonus action after attacking.",
        ),
    ],
    "paladin": [
        ClassFeature(
            "Divine Sense",
            "As an action, sense the location of any celestial, fiend or undead within 60 "
            "feet. 1 + your Charisma modifier uses per long rest.",
        ),
        ClassFeature(
            "Lay on Hands",
            "A pool of healing equal to 5 x your level. As an action, touch a creature and "
            "spend points from it to heal that many hit points.",
            "Curing disease or poison with the pool isn't modeled.",
        ),
    ],
    "ranger": [
        ClassFeature(
            "Favored Enemy",
            "Pick a type of enemy you have studied. You have advantage on Wisdom (Survival) "
            "checks to track them and on Intelligence checks to recall information about "
            "them, and you learn one of their languages.",
            _FLAVOR,
        ),
        ClassFeature(
            "Natural Explorer",
            "Pick a favored terrain. In it you are good at finding your way and food, and "
            "your party travels carefully and quickly.",
            _FLAVOR,
        ),
    ],
    "rogue": [
        ClassFeature(
            "Expertise",
            "Choose two skill proficiencies: your proficiency bonus is doubled for them.",
            "Thieves' tools, the other half of the SRD choice, aren't modeled.",
        ),
        ClassFeature(
            "Sneak Attack",
            "Once per turn, deal an extra 1d6 damage with a finesse or ranged weapon when "
            "you have advantage or an ally is next to the target.",
        ),
        ClassFeature(
            "Thieves' Cant",
            "You know thieves' cant, a secret mix of dialect, jargon and code for hiding "
            "messages in plain conversation.",
            _FLAVOR,
        ),
    ],
    "sorcerer": [
        ClassFeature(
            "Spellcasting",
            "You cast sorcerer spells using Charisma, knowing a fixed list of spells.",
        ),
        ClassFeature(
            "Sorcerous Origin: Draconic Bloodline",
            "Draconic Resilience: +1 hit point per level, and your unarmored AC is 13 + Dexterity.",
            "The Dragon Ancestor choice (a language and flavor) isn't modeled.",
        ),
    ],
    "warlock": [
        ClassFeature(
            "Otherworldly Patron: The Fiend",
            "Dark One's Blessing: when you reduce a hostile creature to 0 hit points, gain "
            "temporary hit points equal to your Charisma modifier + warlock level. Burning "
            "Hands and Command join your spell list.",
        ),
        ClassFeature(
            "Pact Magic",
            "You cast warlock spells using Charisma with a few slots that all come back on a "
            "short rest.",
        ),
    ],
    "wizard": [
        ClassFeature(
            "Spellcasting",
            "You cast wizard spells using Intelligence, preparing a list from the whole "
            "class list each day.",
            "The spellbook (copying new spells in) isn't modeled.",
        ),
        ClassFeature(
            "Arcane Recovery",
            "Once per day during a short rest, recover spell slots with a combined level up "
            "to half your wizard level (rounded up).",
        ),
    ],
}


def level_1_features(class_index: str) -> list[ClassFeature]:
    """The level-1 features of `class_index`, [] for an unknown class."""
    return list(LEVEL_1_FEATURES.get(class_index, []))
