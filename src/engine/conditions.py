"""Apply/tick/remove conditions on a Character. Conditions don't stack in
5e - reapplying one refreshes it rather than adding a duplicate entry."""

from __future__ import annotations

from src.engine.state import Character, Condition, ConditionName


def apply_condition(character: Character, condition: Condition) -> None:
    character.conditions = [c for c in character.conditions if c.name != condition.name] + [
        condition
    ]


def remove_condition(character: Character, name: ConditionName) -> bool:
    """Returns True if a condition was actually removed."""
    before = len(character.conditions)
    character.conditions = [c for c in character.conditions if c.name != name]
    return len(character.conditions) < before


def has_condition(character: Character, name: ConditionName) -> bool:
    return any(c.name == name for c in character.conditions)


def tick_conditions(character: Character) -> list[Condition]:
    """Decrements every timed condition's duration by one round; conditions
    reaching 0 are removed. Indefinite conditions (duration_rounds=None) are
    untouched. Returns the conditions that expired this tick."""
    remaining: list[Condition] = []
    expired: list[Condition] = []
    for c in character.conditions:
        if c.duration_rounds is None:
            remaining.append(c)
        elif c.duration_rounds <= 1:
            expired.append(c)
        else:
            remaining.append(
                Condition(name=c.name, duration_rounds=c.duration_rounds - 1, source=c.source)
            )
    character.conditions = remaining
    return expired
