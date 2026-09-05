"""Campaign library REST API - read-only, no persistence involved (that's
campaign_progress's job, once a session actually starts one - see
api/db/models.py and Day 11+)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.engine.campaign import Campaign, CampaignSize, load_all_campaigns, load_campaign

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


class CampaignSummary(BaseModel):
    id: str
    title: str
    size: CampaignSize
    description: str


@router.get("", response_model=list[CampaignSummary])
def list_campaigns() -> list[CampaignSummary]:
    return [
        CampaignSummary(id=c.id, title=c.title, size=c.size, description=c.description)
        for c in load_all_campaigns()
    ]


@router.get("/{campaign_id}", response_model=Campaign)
def get_campaign(campaign_id: str) -> Campaign:
    try:
        return load_campaign(campaign_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found") from exc
