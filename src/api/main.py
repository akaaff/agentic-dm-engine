"""FastAPI app entrypoint. Routers (characters/campaigns/companions, then
the WebSocket live-play session) get included here starting Day 9."""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="agentic-dm-engine")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
