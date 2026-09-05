"""FastAPI app entrypoint. Routers (characters/campaigns/companions, then
the WebSocket live-play session) get included here starting Day 9."""

from __future__ import annotations

from fastapi import FastAPI

from src.api.routes import campaigns, characters, companions

app = FastAPI(title="agentic-dm-engine")
app.include_router(characters.router)
app.include_router(companions.router)
app.include_router(campaigns.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
