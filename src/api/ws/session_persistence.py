"""Live-session persistence: snapshot the in-memory Session to the DB after
every state-changing broadcast, and rebuild it from that snapshot when a
connection arrives for a session the process doesn't have in memory - so a
backend restart (a deploy, a crash) becomes a short client reconnect instead
of losing every live game.

Kept separate from api/ws/session.py (which owns the Session dataclass and
every broadcast) so the snapshot format is one self-contained, testable unit:
`build_snapshot` reads a Session, `restore_snapshot` returns plain pieces for
session.py to assemble back into one. Everything stored is Pydantic
`model_dump(mode="json")` output or plain JSON types - the same shapes already
sent to the client on every state_update - so a snapshot round-trips through
the same validation a fresh request would.

What is deliberately NOT stored, and why:
- the rng / rate-limit bucket / connections: ephemeral by nature (a restart
  drops every socket anyway; a fresh Random is as good as the old one).
- `human_character_ids` (the token map): already durable on
  CampaignProgress.player_tokens, and the single source of truth for it.
- `pending_scene_narration`: only ever non-empty before a session's first
  connect, which is before the first snapshot is ever written.
- the narration log: it lives client-side; a reconnecting client keeps its own.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.engine.campaign import Campaign
from src.engine.state import Character, GameState
from src.engine.turn_engine import PendingBardicChoice

if TYPE_CHECKING:
    from src.api.ws.session import Session

logger = logging.getLogger(__name__)

SNAPSHOT_VERSION = 1
"""Bumped whenever the snapshot's own shape changes incompatibly. A snapshot
with any other version is treated as absent (the session rebuilds from
scratch, exactly as it did before persistence existed) rather than guessed at."""


@dataclass
class RestoredSession:
    """Everything a snapshot restores, as plain pieces - session.py assembles
    them into a Session (it owns that class, and the party-choice dataclass
    this module can't import without a cycle)."""

    game_state: GameState
    campaign: Campaign
    party: list[Character]
    current_scene_id: str | None
    campaign_complete: bool
    adaptive_generations_used: int
    pending_bardic_choice: PendingBardicChoice | None
    pending_party_choice: dict[str, Any] | None
    """{"scene_id", "situation", "responses"} - raw, see session.py."""


def build_snapshot(
    session: Session, game_state_json: dict[str, Any] | None = None
) -> dict[str, Any]:
    """`game_state_json` lets a caller that has already dumped the GameState
    for a state_update broadcast reuse that dump instead of serializing the
    (event-log-heavy) state a second time."""
    assert session.campaign is not None  # only real sessions are persisted
    pending_party = session.pending_party_choice
    return {
        "version": SNAPSHOT_VERSION,
        "game_state": game_state_json
        if game_state_json is not None
        else session.game_state.model_dump(mode="json"),
        # The whole campaign, not just a scene pointer: _resolve_party_choice
        # splices model-generated scenes onto the live Campaign's scene list,
        # and those exist nowhere else.
        "campaign": session.campaign.model_dump(mode="json"),
        "current_scene_id": session.current_scene_id,
        "campaign_complete": session.campaign_complete,
        "adaptive_generations_used": session.adaptive_generations_used,
        "pending_bardic_choice": (
            dataclasses.asdict(session.pending_bardic_choice)
            if session.pending_bardic_choice is not None
            else None
        ),
        "pending_party_choice": (
            {
                "scene_id": pending_party.scene_id,
                "situation": pending_party.situation,
                "responses": dict(pending_party.responses),
            }
            if pending_party is not None
            else None
        ),
    }


def restore_snapshot(snapshot: dict[str, Any], party_character_ids: list[str]) -> RestoredSession:
    """Raises (ValueError / pydantic.ValidationError / KeyError) on anything
    off - the caller treats any failure as "no usable snapshot" and rebuilds
    the session from scratch, so a bad snapshot can never wedge a game."""
    if snapshot.get("version") != SNAPSHOT_VERSION:
        raise ValueError(f"unsupported session snapshot version {snapshot.get('version')!r}")

    game_state = GameState.model_validate(snapshot["game_state"])
    campaign = Campaign.model_validate(snapshot["campaign"])

    # The party must be the SAME Character objects the game_state holds, not
    # copies: rests (apply_short_rest/apply_long_rest) mutate session.party in
    # place and rely on build_encounter_state having shared them with
    # game_state.characters. Rebuilt by id from the restored state, in the
    # party's own stored order.
    party: list[Character] = []
    for char_id in party_character_ids:
        character = game_state.characters.get(char_id)
        if character is None:
            raise ValueError(f"party member {char_id!r} missing from the snapshot's game state")
        party.append(character)

    bardic_raw = snapshot.get("pending_bardic_choice")
    bardic: PendingBardicChoice | None = None
    if bardic_raw is not None:
        # JSON turned the (label, value) breakdown tuples into lists.
        bardic = PendingBardicChoice(
            **{
                **bardic_raw,
                "attack_bonus_breakdown": [
                    (str(label), int(value))
                    for label, value in bardic_raw["attack_bonus_breakdown"]
                ],
            }
        )

    return RestoredSession(
        game_state=game_state,
        campaign=campaign,
        party=party,
        current_scene_id=snapshot.get("current_scene_id"),
        campaign_complete=bool(snapshot.get("campaign_complete", False)),
        adaptive_generations_used=int(snapshot.get("adaptive_generations_used", 0)),
        pending_bardic_choice=bardic,
        pending_party_choice=snapshot.get("pending_party_choice"),
    )
