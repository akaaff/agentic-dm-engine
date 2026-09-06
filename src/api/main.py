"""FastAPI app entrypoint. Routers (characters/campaigns/companions, then
the WebSocket live-play session) get included here starting Day 9."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.routes import campaigns, characters, companions, sessions
from src.api.ws import session as ws_session
from src.imagegen.service import DEFAULT_OUTPUT_DIR, MEDIA_URL_PREFIX

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

# StaticFiles requires the directory to exist at mount time - generate_scene_image
# only creates it lazily on first use (Day 16), so a fresh checkout that's
# never generated an image yet would otherwise fail app startup.
DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount(MEDIA_URL_PREFIX, StaticFiles(directory=DEFAULT_OUTPUT_DIR), name="scene-images")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
