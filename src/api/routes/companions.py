"""Pregen companion roster - each request derives fresh Character sheets
from the YAML recipes (src/engine/companions.py), same as any other
create_character() call. Not persisted: a companion is re-derived on demand,
never mutated in place (mutation happens on the in-session copy once picked
for a party, not on the roster's canonical definition)."""

from __future__ import annotations

from fastapi import APIRouter

from src.engine.companions import build_companion, load_all_companion_specs
from src.engine.state import Character

router = APIRouter(prefix="/companions", tags=["companions"])


@router.get("", response_model=list[Character])
def list_companions() -> list[Character]:
    return [build_companion(spec) for spec in load_all_companion_specs()]
