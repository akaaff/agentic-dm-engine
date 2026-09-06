"""Session-start REST API (Day 18). Persists the player's character/party/
campaign choices as a CampaignProgress row (the table has existed since Day
8 but nothing wrote to it until now) and hands back a session_id. Turning
that session_id into an actual playable GameState over the WebSocket is Day
19's job - this endpoint only records the *choice*, matching the plan's own
phasing ("party setup + campaign select screens" today, "start a live
session... against the teacher-model pipeline" next).
"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.api.db.models import CampaignProgress, CharacterRecord
from src.api.db.session import get_db
from src.engine.campaign import load_campaign
from src.engine.companions import load_all_companion_specs

router = APIRouter(prefix="/sessions", tags=["sessions"])

DbSession = Annotated[Session, Depends(get_db)]


class StartSessionRequest(BaseModel):
    campaign_id: str
    character_id: str
    """The player's own created character (src/api/routes/characters.py)."""
    companion_ids: list[str] = []
    """0-4 pregen companion ids (src/engine/companions.py), per the plan's
    party-setup design - not enforced to <=4 server-side since nothing about
    play actually requires that cap; the frontend enforces it as a design
    choice, not a hard rule."""


class StartSessionResponse(BaseModel):
    session_id: str


@router.post("", response_model=StartSessionResponse, status_code=201)
def start_session(body: StartSessionRequest, db: DbSession) -> StartSessionResponse:
    try:
        campaign = load_campaign(body.campaign_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"Campaign {body.campaign_id} not found"
        ) from exc

    if db.get(CharacterRecord, body.character_id) is None:
        raise HTTPException(status_code=404, detail=f"Character {body.character_id} not found")

    known_companion_ids = {spec.character_id for spec in load_all_companion_specs()}
    unknown = set(body.companion_ids) - known_companion_ids
    if unknown:
        raise HTTPException(status_code=404, detail=f"Unknown companion id(s): {sorted(unknown)}")

    session_id = uuid4().hex
    db.add(
        CampaignProgress(
            id=session_id,
            campaign_id=campaign.id,
            current_scene_id=campaign.first_scene().id,
            party_character_ids=[body.character_id, *body.companion_ids],
        )
    )
    db.commit()
    return StartSessionResponse(session_id=session_id)
