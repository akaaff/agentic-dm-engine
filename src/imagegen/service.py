"""Local Stable Diffusion (SD-Turbo) pipeline wrapper for scene images.

See DECISIONS.md #5 for why SD-Turbo specifically (small enough VRAM
footprint to coexist with the Ollama teacher model on one RTX 3080, verified
live on Day 16 - see CLAUDE.md). The pipeline is loaded once and cached at
module scope: it's too expensive (multi-second load, ~2-3GB VRAM) to reload
per graph invocation, and scene_image_node is a plain function called many
times over a session's life.
"""

from __future__ import annotations

import logging
from functools import cache
from pathlib import Path
from uuid import uuid4

import torch
from diffusers import AutoPipelineForText2Image

logger = logging.getLogger(__name__)

SD_TURBO_MODEL = "stabilityai/sd-turbo"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "generated_images"

MEDIA_URL_PREFIX = "/media/scene-images"
"""Where DEFAULT_OUTPUT_DIR is mounted as a FastAPI StaticFiles route (Day
21, src/api/main.py) - co-located with DEFAULT_OUTPUT_DIR since they're two
views of the same directory and scene_image_node needs both to turn a saved
file into a URL the browser can actually load."""

MIN_FREE_VRAM_BYTES = 2 * 1024**3
"""SD-Turbo's own documented footprint is ~2-3GB. Below this much free VRAM,
skip generation rather than risk a mid-session CUDA OOM crash - the
graceful-fallback design called for in the plan/DECISIONS.md #5."""


def _has_enough_vram() -> bool:
    if not torch.cuda.is_available():
        return False
    free_bytes, _total_bytes = torch.cuda.mem_get_info()
    return free_bytes >= MIN_FREE_VRAM_BYTES


@cache
def _load_pipeline() -> AutoPipelineForText2Image | None:
    """The VRAM guard belongs here, not in generate_scene_image, and only
    matters for this one-time cold load (this function is @cache'd, so it
    runs exactly once per process). Found live on Day 16: checking free VRAM
    before *every* generate call was wrong - once the pipeline is loaded,
    PyTorch's caching allocator holds onto the VRAM it already grabbed for
    reuse by this same process, so torch.cuda.mem_get_info()'s "free" number
    stays low from then on even though the already-resident pipeline needs
    no *new* memory to run another single-step inference. That made every
    generation after the very first one get skipped for the rest of a
    session. A genuine later OOM (a real resource failure, not a heuristic
    guess) is still caught by generate_scene_image's broad except below."""
    if not torch.cuda.is_available():
        logger.warning("No CUDA device available - scene image generation disabled")
        return None
    if not _has_enough_vram():
        logger.warning("Not enough free VRAM to load the SD-Turbo pipeline - skipping images")
        return None
    # diffusers ships no type stubs - from_pretrained/pipeline.__call__ are
    # untyped from mypy's point of view under strict mode.
    pipe = AutoPipelineForText2Image.from_pretrained(  # type: ignore[no-untyped-call]
        SD_TURBO_MODEL, torch_dtype=torch.float16, variant="fp16"
    )
    return pipe.to("cuda")  # type: ignore[no-any-return]


def generate_scene_image(prompt: str, output_dir: Path = DEFAULT_OUTPUT_DIR) -> Path | None:
    """Returns the path to a generated PNG, or None if generation was
    skipped (no CUDA, insufficient free VRAM to ever load the pipeline) or
    failed. Callers (scene_image_node) treat None as "no image this turn"
    and continue play rather than block or crash - the broad except below
    is deliberate for that reason, not a blanket-error-handling
    anti-pattern: a local ML pipeline call is a real system boundary (OOM, a
    corrupted download, a driver hiccup are all real failure modes here),
    and the plan explicitly calls for this exact graceful-fallback
    behavior."""
    pipe = _load_pipeline()
    if pipe is None:
        return None

    try:
        # SD-Turbo is distilled for 1-4 step inference with no classifier-free
        # guidance (guidance_scale=0.0) - using the higher step counts or
        # default guidance meant for SD1.5 defeats the point of the "turbo"
        # distillation and produces worse images, per the model card.
        image = pipe(  # type: ignore[operator]
            prompt=prompt, num_inference_steps=1, guidance_scale=0.0
        ).images[0]
    except Exception:
        logger.exception("Scene image generation failed - skipping")
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{uuid4().hex}.png"
    image.save(output_path)
    return output_path
