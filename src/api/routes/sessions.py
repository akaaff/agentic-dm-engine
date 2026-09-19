"""Session-start REST API (Day 18). Persists the player's character/party/
campaign choices as a CampaignProgress row (the table has existed since Day
8 but nothing wrote to it until now) and hands back a session_id. Turning
that session_id into an actual playable GameState over the WebSocket is Day
19's job - this endpoint only records the *choice*, matching the plan's own
phasing ("party setup + campaign select screens" today, "start a live
session... against the teacher-model pipeline" next).

Issue #44 adds a second, parallel way to reach the same CampaignProgress
row: a lobby (POST /sessions/lobby -> join -> start) instead of this file's
original single-shot POST /sessions (campaign + character + companions all
chosen upfront, by one player, before the row even exists). The original
endpoint is untouched - #45's frontend rewiring, not this issue, decides
whether/when the single-player UI moves onto the lobby path; both can create
a perfectly normal CampaignProgress row today, distinguished only by
`status` and whether `player_tokens` ends up populated."""

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


def _get_progress_or_404(db: DbSession, session_id: str) -> CampaignProgress:
    progress = db.get(CampaignProgress, session_id)
    if progress is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return progress


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


class CreateLobbyRequest(BaseModel):
    campaign_id: str


class CreateLobbyResponse(BaseModel):
    session_id: str
    """Also the shareable lobby code - see CampaignProgress.id's own
    docstring for why no separate short code exists."""


@router.post("/lobby", response_model=CreateLobbyResponse, status_code=201)
def create_lobby(body: CreateLobbyRequest, db: DbSession) -> CreateLobbyResponse:
    try:
        campaign = load_campaign(body.campaign_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"Campaign {body.campaign_id} not found"
        ) from exc

    session_id = uuid4().hex
    db.add(
        CampaignProgress(
            id=session_id,
            campaign_id=campaign.id,
            current_scene_id=campaign.first_scene().id,
            party_character_ids=[],
            status="open",
        )
    )
    db.commit()
    return CreateLobbyResponse(session_id=session_id)


class LobbyStatusResponse(BaseModel):
    session_id: str
    campaign_id: str
    status: str
    party_character_ids: list[str]


@router.get("/{session_id}", response_model=LobbyStatusResponse, status_code=200)
def get_lobby_status(session_id: str, db: DbSession) -> LobbyStatusResponse:
    # Issue #45: read-only, no side effects - lets a joined player's client
    # poll for "has the leader started yet" (status flipping "open" ->
    # "in_progress") without needing to open the live-play WebSocket early,
    # which would build the actual encounter from whatever's currently in
    # party_character_ids - before the leader's chosen companions fill the
    # remaining seats, if that poll happened to race ahead of POST
    # /sessions/{id}/start.
    progress = _get_progress_or_404(db, session_id)
    return LobbyStatusResponse(
        session_id=progress.id,
        campaign_id=progress.campaign_id,
        status=progress.status,
        party_character_ids=progress.party_character_ids,
    )


class JoinLobbyRequest(BaseModel):
    character_id: str | None = None
    """Required to claim a brand-new seat - the player's own already-created
    character (POST /characters happens first, same as the single-shot
    flow; this endpoint only claims a lobby seat for it, it doesn't create
    the character itself)."""
    token: str | None = None
    """A previously-issued personal token, to resume as the character it
    already maps to - a returning player, or a client retrying a join call
    that actually succeeded server-side. Exactly one of character_id/token
    must be given."""


class JoinLobbyResponse(BaseModel):
    token: str
    character_id: str


@router.post("/{session_id}/join", response_model=JoinLobbyResponse, status_code=200)
def join_lobby(session_id: str, body: JoinLobbyRequest, db: DbSession) -> JoinLobbyResponse:
    progress = _get_progress_or_404(db, session_id)

    if body.token is not None:
        character_id = progress.player_tokens.get(body.token)
        if character_id is None:
            raise HTTPException(status_code=404, detail="Unknown token for this session")
        return JoinLobbyResponse(token=body.token, character_id=character_id)

    if body.character_id is None:
        raise HTTPException(status_code=400, detail="character_id or token is required")

    # Idempotent retry: a client that already has a token for this exact
    # character (e.g. a join call that succeeded but whose response was
    # lost) gets the same token back rather than a second, orphaned one.
    existing_token = next(
        (t for t, cid in progress.player_tokens.items() if cid == body.character_id), None
    )
    if existing_token is not None:
        return JoinLobbyResponse(token=existing_token, character_id=body.character_id)

    if progress.status != "open":
        raise HTTPException(status_code=409, detail="This lobby has already started")

    if db.get(CharacterRecord, body.character_id) is None:
        raise HTTPException(status_code=404, detail=f"Character {body.character_id} not found")

    token = uuid4().hex
    progress.party_character_ids = [*progress.party_character_ids, body.character_id]
    progress.player_tokens = {**progress.player_tokens, token: body.character_id}
    db.commit()
    return JoinLobbyResponse(token=token, character_id=body.character_id)


class StartLobbyRequest(BaseModel):
    companion_ids: list[str] = []
    """Fills whatever seats the joined humans didn't claim - the same
    explicit, player-chosen-not-auto-picked shape the single-shot flow's own
    companion_ids already has, rather than inventing an auto-fill
    heuristic."""


class StartLobbyResponse(BaseModel):
    session_id: str
    party_character_ids: list[str]


@router.post("/{session_id}/start", response_model=StartLobbyResponse, status_code=200)
def start_lobby(session_id: str, body: StartLobbyRequest, db: DbSession) -> StartLobbyResponse:
    progress = _get_progress_or_404(db, session_id)

    if progress.status != "open":
        raise HTTPException(status_code=409, detail="This lobby has already started")
    if not progress.party_character_ids:
        raise HTTPException(status_code=400, detail="At least one player must join before starting")

    known_companion_ids = {spec.character_id for spec in load_all_companion_specs()}
    unknown = set(body.companion_ids) - known_companion_ids
    if unknown:
        raise HTTPException(status_code=404, detail=f"Unknown companion id(s): {sorted(unknown)}")

    progress.party_character_ids = [*progress.party_character_ids, *body.companion_ids]
    progress.status = "in_progress"
    db.commit()
    return StartLobbyResponse(
        session_id=session_id, party_character_ids=progress.party_character_ids
    )
