"""Local text-to-speech for narration audio.

See DECISIONS.md #9 for why Kokoro-82M specifically (Apache-2.0, 54 built-in
voices, ~2GB VRAM measured live - a much lighter footprint than the
originally-considered XTTS-v2, and no reference-audio sourcing needed).

Built swappable on purpose, the same shape config.INTENT_PARSER_BACKEND
already uses to swap intent_parser_node's underlying model: the ONE public
function every caller uses is generate_narration_audio(text, voice) below,
which dispatches on config.TTS_BACKEND to a private per-backend function.
Nothing outside this module imports a TTS backend's own package directly -
adding a second engine later means writing one new _synthesize_<name>
function and one new dispatch branch, not touching any call site.
"""

from __future__ import annotations

import logging
import threading
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from src import config

if TYPE_CHECKING:
    from kokoro import KPipeline

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "generated_audio"

MEDIA_URL_PREFIX = "/media/narration-audio"
"""Where DEFAULT_OUTPUT_DIR is mounted as a FastAPI StaticFiles route (see
src/api/main.py) - co-located with DEFAULT_OUTPUT_DIR for the same reason
imagegen.service.MEDIA_URL_PREFIX is: session.py needs both to turn a saved
file into a URL the browser can actually load."""

KOKORO_MIN_FREE_VRAM_BYTES = 2 * 1024**3
"""Kokoro's own measured footprint is ~2GB (live spike, see DECISIONS.md #9)
- well under SD-Turbo's own 2-3GB threshold, but kept as its own constant
(not reused from imagegen.service) since a future TTS backend may have a
very different real number. Below this much free VRAM, skip generation
rather than risk a mid-session CUDA OOM - same graceful-fallback stance as
imagegen.service._has_enough_vram."""


def _has_enough_vram(min_free_bytes: int) -> bool:
    import torch

    if not torch.cuda.is_available():
        return False
    free_bytes, _total_bytes = torch.cuda.mem_get_info()
    return free_bytes >= min_free_bytes


_LOAD_LOCK = threading.Lock()
"""Held around the cached loader: `@cache` alone lets two threads that both miss run the
body twice (found live: two concurrent loads on the first narration lines)."""


@cache
def _load_kokoro_pipeline() -> KPipeline | None:
    """@cache'd so this only ever runs once per process (Kokoro's own load
    took ~14s in the live spike) - same one-time-cold-load reasoning as
    imagegen.service._load_pipeline, including why the VRAM guard belongs
    here and not in the per-call function (checking free VRAM before every
    call would wrongly skip every generation after the first, since the
    caching allocator holds onto what it already grabbed - see that
    function's own docstring for the full Day-16 story this mirrors).
    Falls back to the CPU when the GPU is missing or short on free memory,
    instead of switching narration off: Kokoro-82M is small enough that the CPU
    makes audio about 2.5x faster than real time (measured: 2.9 s for a 7.5 s line,
    after a ~13 s one-time load). This was found live - the scene-image model
    loads first, takes most of a 10 GB card, and the old behaviour then disabled
    voiceover for the rest of the process, with only a log warning to say why.
    Returns None (never raises) if the `kokoro` package or its `espeak-ng`
    phonemizer dependency isn't available - that degrades to "skip this line's
    audio", not a crash."""
    device = "cuda" if _has_enough_vram(KOKORO_MIN_FREE_VRAM_BYTES) else "cpu"
    if device == "cpu":
        logger.warning(
            "Not enough free VRAM for the Kokoro pipeline - narration will run on the CPU"
        )
    try:
        from kokoro import KPipeline
    except Exception:
        logger.exception("Failed to import kokoro - narration audio disabled")
        return None
    try:
        pipeline = KPipeline(lang_code="a", device=device, repo_id="hexgrad/Kokoro-82M")
        # Loading another diffusers/transformers model flips torch's *global* default dtype
        # to half precision for the duration of its load. Two threads were found doing
        # exactly that at once (the scene-image model and this one, on the first turn),
        # which built Kokoro's weights in half precision and made every later call fail
        # with "mat1 and mat2 must have the same dtype, but got Float and Half". Kokoro
        # is float32, so put it back whatever the default was at construction.
        pipeline.model.float()
        return pipeline
    except Exception:
        logger.exception("Failed to load the Kokoro pipeline - narration audio disabled")
        return None


def _synthesize_kokoro(text: str, voice: str, output_path: Path) -> Path | None:
    with _LOAD_LOCK:
        pipeline = _load_kokoro_pipeline()
    if pipeline is None:
        return None
    try:
        import numpy as np
        import soundfile as sf

        chunks = [audio for _graphemes, _phonemes, audio in pipeline(text, voice=voice)]
        if not chunks:
            return None
        sf.write(output_path, np.concatenate(chunks), 24000)
    except Exception:
        logger.exception("Kokoro narration audio generation failed - skipping")
        return None
    return output_path


def generate_narration_audio(
    text: str, voice: str, output_dir: Path = DEFAULT_OUTPUT_DIR
) -> Path | None:
    """Returns the path to a generated WAV, or None if generation was
    skipped (backend unavailable, insufficient VRAM) or failed. Callers
    (session.py's _broadcast_narration) treat None as "no audio for this
    line" and continue play rather than block or crash - same reasoning as
    imagegen.service.generate_scene_image's own docstring: a local ML
    pipeline call is a real system boundary, not a place for a blanket-
    error-handling anti-pattern."""
    if not text.strip():
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{uuid4().hex}.wav"

    if config.TTS_BACKEND == "kokoro":
        return _synthesize_kokoro(text, voice, output_path)

    logger.warning("Unknown TTS_BACKEND %r - narration audio disabled", config.TTS_BACKEND)
    return None
