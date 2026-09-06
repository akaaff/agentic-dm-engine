"""FastAPI app entrypoint. Routers (characters/campaigns/companions, then
the WebSocket live-play session) get included here starting Day 9."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import campaigns, characters, companions, sessions
from src.api.ws import session as ws_session

app = FastAPI(title="agentic-dm-engine")

# The Vite dev server (Day 17+) runs on a different origin (localhost:5173)
# than this API (localhost:8000) - local-dev-only, wide open since this is a
# local single-user app with no deployed/public instance to protect.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(characters.router)
app.include_router(companions.router)
app.include_router(campaigns.router)
app.include_router(sessions.router)
app.include_router(ws_session.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
