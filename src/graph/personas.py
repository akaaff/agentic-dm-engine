"""Formats a companion's persona into an LLM prompt fragment.

Character.persona is already populated at creation time (see
engine/companions.py, which threads each pregen's YAML persona blurb through
create_character()) - this module's only job is presentation for
player_agent's prompt, not loading, so it stays a single small function
rather than a data-loading layer duplicating engine/companions.py.
"""

from __future__ import annotations

from src.engine.state import Character


def persona_block(character: Character) -> str:
    persona = character.persona or "no particular personality - play it straight"
    return f"You are {character.name}, a {character.race} {character.class_}. Persona: {persona}"
