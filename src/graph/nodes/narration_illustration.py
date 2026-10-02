"""Illustrates out-of-combat narration: a few lines of scene text become one
picture for the scene panel (the slot the combat grid occupies during a
fight, which sat empty between encounters).

Not a LangGraph node - nothing here touches GraphState; api/ws/session.py
calls it directly with the lines it is about to broadcast, the same way it
calls party_choice's plain LLM functions.

SD-Turbo reads about 77 tokens, so the narration itself can't be the prompt:
the teacher model first condenses the lines into one concrete visual
sentence (setting, lighting, what's in view), and Python caps the length and
adds a fixed style prefix - the model only picks what to depict, the
mechanical limits are not left to it. Any failure along the way (Ollama
unreachable, no GPU, not enough VRAM) returns None, and the scene simply has
no picture - illustration is a bonus, never a reason a story stalls.
"""

from __future__ import annotations

import logging

import httpx

from src import config
from src.imagegen.service import MEDIA_URL_PREFIX, generate_scene_image
from src.llm.providers import chat_english_only, load_prompt

logger = logging.getLogger(__name__)

STYLE_PREFIX = "Fantasy tabletop RPG illustration, digital painting, dramatic lighting, no text. "
MAX_DESCRIPTION_WORDS = 45
"""Headroom over the prompt's own "35 words" ask - a model overshoots a
word limit often enough that the cap has to be enforced here, or the
tail of the prompt is silently dropped by the 77-token CLIP window."""


def _cap_words(text: str, limit: int) -> str:
    return " ".join(text.split()[:limit])


def build_narration_image_prompt(lines: list[str]) -> str:
    """The image-generator prompt for these narration lines. Falls back to
    the narration's own opening words when the condensing call fails, so an
    Ollama hiccup degrades the picture's precision, not its existence."""
    narration = " ".join(line.strip() for line in lines if line.strip())
    try:
        description = chat_english_only(
            messages=[
                {
                    "role": "user",
                    "content": load_prompt("narration_image").format(narration=narration),
                }
            ],
            temperature=0.5,
        ).strip()
    except httpx.HTTPError:
        logger.warning("Narration image description failed - using the narration itself")
        description = ""
    return STYLE_PREFIX + _cap_words(description or narration, MAX_DESCRIPTION_WORDS)


def illustrate_narration(lines: list[str]) -> str | None:
    """A media URL for an image illustrating `lines`, or None (disabled, no
    text, or generation skipped/failed)."""
    if not config.SCENE_IMAGES_ENABLED or not any(line.strip() for line in lines):
        return None
    image_path = generate_scene_image(build_narration_image_prompt(lines))
    return f"{MEDIA_URL_PREFIX}/{image_path.name}" if image_path else None
