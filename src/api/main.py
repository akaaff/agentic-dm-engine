"""FastAPI app entrypoint. Routers (characters/campaigns/companions, then
the WebSocket live-play session) get included here starting Day 9."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.api.routes import campaigns, characters, companions, sessions
from src.api.ws import session as ws_session
from src.config import EXTRA_CORS_ORIGINS, SHARED_ACCESS_PASSPHRASE
from src.engine.character_creation import PORTRAIT_DIR
from src.imagegen.service import DEFAULT_OUTPUT_DIR, MEDIA_URL_PREFIX

app = FastAPI(title="agentic-dm-engine")

# Issue #42: everything except /health requires the shared passphrase (as an
# X-Access-Passphrase header) once SHARED_ACCESS_PASSPHRASE is set - a no-op
# when it's unset (local dev, the default). Registered *before* the
# CORSMiddleware call below so CORS ends up as the outermost layer (Starlette
# wraps middleware in reverse-registration order - confirmed by reading
# Starlette's own build_middleware_stack rather than assuming) and still
# attaches Access-Control-Allow-Origin to a rejected 401 response, the same
# "don't let a masked backend response read as a CORS error" lesson already
# documented elsewhere in this file for unhandled exceptions. This only
# covers HTTP requests - the WebSocket endpoint (which can't carry a custom
# header from browser JS) does its own equivalent check via a `key` query
# param, in api/ws/session.py.
_UNGATED_PATHS = {"/health"}


@app.middleware("http")
async def _require_passphrase(request: Request, call_next):  # type: ignore[no-untyped-def]
    if SHARED_ACCESS_PASSPHRASE and request.url.path not in _UNGATED_PATHS:
        if request.headers.get("X-Access-Passphrase") != SHARED_ACCESS_PASSPHRASE:
            return JSONResponse({"detail": "Missing or incorrect passphrase."}, status_code=401)
    return await call_next(request)


# The Vite dev server (Day 17+) runs on a different origin (localhost:5173)
# than this API (localhost:8000) - local-dev-only, wide open since this is a
# local single-user app with no deployed/public instance to protect.
# EXTRA_CORS_ORIGINS (issue #40) covers a genuinely split-origin hosting
# setup - the common single-tunnel case doesn't need it at all, since the
# Vite dev proxy (see vite.config.ts) makes every request same-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", *EXTRA_CORS_ORIGINS],
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

# Same reasoning as scene-images above - a fresh checkout that hasn't run
# generate_portraits.py yet still needs the mount directory to exist at
# startup. pc/ and monsters/ subdirs are created too so the frontend's
# onError-fallback-to-circle path (Phase 3+) has real 404s to fall back
# from, not a mount-level 404 for the whole prefix.
(PORTRAIT_DIR / "pc").mkdir(parents=True, exist_ok=True)
(PORTRAIT_DIR / "monsters").mkdir(parents=True, exist_ok=True)
app.mount("/media/portraits", StaticFiles(directory=PORTRAIT_DIR), name="portraits")


@app.get("/health")
def health() -> dict[str, object]:
    # passphrase_required lets the frontend's gate screen (issue #42) decide
    # whether to show itself at all, without guessing from a failed request -
    # this endpoint is deliberately exempt from the gate itself (see
    # _UNGATED_PATHS above), matching the ordinary "a health check shouldn't
    # need auth" convention.
    return {"status": "ok", "passphrase_required": SHARED_ACCESS_PASSPHRASE is not None}


@app.get("/auth/check")
def auth_check() -> dict[str, bool]:
    # Gated like everything else - reaching this handler at all already
    # proves the caller's X-Access-Passphrase header was correct (or the
    # gate is disabled), so there's nothing left to check here. The
    # frontend's gate screen calls this once to validate what the player
    # typed before storing it and proceeding, rather than only discovering
    # it was wrong on the first real API call.
    return {"ok": True}
