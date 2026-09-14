"""CLI: pre-generate the portrait library for the character-sheet + portrait
feature (see CLAUDE.md), using a local FLUX.1-schnell pipeline rather than a
paid API - the original plan called for Google's Gemini/Imagen API, but the
free tier of that API has zero image-generation quota (confirmed live, not
assumed), and this project already has an established "run a local diffusion
model" precedent (src/imagegen/service.py's SD-Turbo pipeline).

Two portrait sets, each resumable by file existence (a rerun just fills in
gaps - no separate manifest needed):

  PC/companion set: every (race, class, gender) combination - race x class x
  gender only, no hair color (dropped from an earlier draft to cut this set
  3x, since a local single-GPU batch job's wall-clock time matters more than
  the extra cosmetic detail). Saved to data/portraits/pc/{race}_{class}_
  {gender}.png.

  Monster set: every vendored SRD monster. Saved to
  data/portraits/monsters/{monster_index}.png.

This machine's tight system RAM (see CLAUDE.md's live-verification notes)
means the standard bf16 T5 text encoder (~8.9GB) doesn't reliably fit
alongside everything else running - this script loads a pre-quantized fp8
T5 instead (comfyanonymous/flux_text_encoders, ~4.9GB) and moves the
GGUF-quantized transformer to the GPU *before* loading T5, so the two
loads' peak system-RAM usage doesn't overlap.

Usage:
  uv run python -m src.cli.generate_portraits --limit 10   # smoke-test a
                                                             # small batch
  uv run python -m src.cli.generate_portraits               # the full run
  uv run python -m src.cli.generate_portraits --pc-only
  uv run python -m src.cli.generate_portraits --monsters-only
"""

from __future__ import annotations

import argparse
import itertools
import sys
import time
from typing import Any

import torch
from accelerate import init_empty_weights
from diffusers import FluxPipeline, FluxTransformer2DModel, GGUFQuantizationConfig
from PIL import Image
from safetensors.torch import load_file
from transformers import T5Config, T5EncoderModel

from src.engine.character_creation import PORTRAIT_DIR, VALID_GENDERS
from src.engine.rules import class_equipment_options
from src.engine.srd_loader import SrdIndex, load_srd

PC_DIR = PORTRAIT_DIR / "pc"
MONSTER_DIR = PORTRAIT_DIR / "monsters"

GGUF_REPO = "city96/FLUX.1-schnell-gguf"
GGUF_FILENAME = "flux1-schnell-Q4_K_S.gguf"
"""Q4_K_S (not a higher-fidelity quant) - confirmed live to fit this
machine's RTX 3080 (10GB VRAM) with enable_model_cpu_offload(). See
CLAUDE.md's live-verification notes for the exact numbers."""

T5_FP8_REPO = "comfyanonymous/flux_text_encoders"
T5_FP8_FILENAME = "t5xxl_fp8_e4m3fn.safetensors"
"""Standard bf16 T5 (~8.9GB) doesn't reliably fit in this machine's ~16GB
system RAM alongside everything else already running - confirmed live via
a repeatable segfault during from_pretrained's weight-loading step. This
fp8 checkpoint (~4.9GB, same tensor keys as transformers' T5EncoderModel,
confirmed directly rather than assumed) is the standard low-resource
alternative the ComfyUI/ComfyUI-adjacent community uses for FLUX."""

BASE_REPO = "black-forest-labs/FLUX.1-schnell"

IMAGE_SIZE = 1024
NUM_INFERENCE_STEPS = 4
"""FLUX.1-schnell is distilled for very-few-step inference (unlike FLUX.1-dev)
- more steps doesn't reliably improve quality and costs real wall-clock time
across an ~550-image batch."""


def _load_pipeline() -> FluxPipeline:
    from huggingface_hub import hf_hub_download

    print("Downloading/locating GGUF-quantized transformer...", flush=True)
    gguf_path = hf_hub_download(repo_id=GGUF_REPO, filename=GGUF_FILENAME)

    print("Loading transformer, moving to GPU before loading T5...", flush=True)
    t0 = time.time()
    transformer = FluxTransformer2DModel.from_single_file(
        gguf_path,
        quantization_config=GGUFQuantizationConfig(  # type: ignore[no-untyped-call]
            compute_dtype=torch.bfloat16
        ),
        config=BASE_REPO,
        subfolder="transformer",
        torch_dtype=torch.bfloat16,
    )
    transformer = transformer.to("cuda")
    print(f"  transformer ready in {time.time() - t0:.1f}s", flush=True)

    print("Downloading/locating fp8 T5 text encoder...", flush=True)
    t5_path = hf_hub_download(
        repo_id=T5_FP8_REPO, filename=T5_FP8_FILENAME, local_dir="models/flux_text_encoders"
    )

    print("Loading fp8 T5 text encoder...", flush=True)
    t0 = time.time()
    t5_config = T5Config.from_pretrained(BASE_REPO, subfolder="text_encoder_2")
    with init_empty_weights():
        text_encoder_2 = T5EncoderModel(t5_config)
    state_dict = load_file(t5_path)
    text_encoder_2.load_state_dict(state_dict, assign=True, strict=True)
    text_encoder_2 = text_encoder_2.to(torch.bfloat16)  # type: ignore[arg-type]
    print(f"  T5 ready in {time.time() - t0:.1f}s", flush=True)

    print("Assembling pipeline...", flush=True)
    t0 = time.time()
    pipe = FluxPipeline.from_pretrained(  # type: ignore[no-untyped-call]
        BASE_REPO,
        transformer=transformer,
        text_encoder_2=text_encoder_2,
        torch_dtype=torch.bfloat16,
    )
    pipe.enable_model_cpu_offload()
    print(f"  pipeline ready in {time.time() - t0:.1f}s", flush=True)
    return pipe  # type: ignore[no-any-return]


def _generate(pipe: FluxPipeline, prompt: str) -> Image.Image:
    result = pipe(  # type: ignore[operator]
        prompt=prompt,
        height=IMAGE_SIZE,
        width=IMAGE_SIZE,
        num_inference_steps=NUM_INFERENCE_STEPS,
        guidance_scale=0.0,
    )
    image: Image.Image = result.images[0]
    return image


def _default_gear_phrase(class_index: str, srd: SrdIndex) -> str:
    """A short "wielding X and wearing Y" phrase derived from the class's
    real SRD weapon/armor proficiencies (rules.class_equipment_options),
    not a hand-authored per-class table - picks the alphabetically-first
    proficient weapon/armor as a deterministic, good-enough default (this
    is decorative prompt flavor, not mechanically meaningful)."""
    cls = srd.classes[class_index]
    options = class_equipment_options(cls, srd)
    weapon_name: str | None = None
    armor_name: str | None = None
    for index in options:
        item = srd.equipment.get(index)
        if item is None:
            continue
        if weapon_name is None and item.get("weapon_category"):
            weapon_name = item["name"]
        elif (
            armor_name is None and item.get("armor_category") and item["armor_category"] != "Shield"
        ):
            armor_name = item["name"]
        if weapon_name and armor_name:
            break
    parts = []
    if weapon_name:
        parts.append(f"wielding a {weapon_name.lower()}")
    if armor_name:
        parts.append(f"wearing {armor_name.lower()}")
    return " and ".join(parts)


RACE_DESCRIPTORS: dict[str, str] = {
    # A bare race name alone under-specifies the model's output for races
    # whose look diverges most from a plain human (confirmed live: an
    # undescribed "dragonborn" rendered as a bearded human/dwarf, or
    # sprouted incorrect tiefling-like horns and bat wings - dragonborn
    # have neither in the SRD). Only races that need real correction are
    # listed; a race not here (human/half-elf/half-orc/dwarf/elf/gnome/
    # halfling) renders correctly from its plain name.
    "dragonborn": "a dragonborn (draconic humanoid with scaled skin, a "
    "reptilian dragon-like head and snout, no hair)",
    "tiefling": "a tiefling (humanoid with small horns and a thin tail, otherwise human-like)",
}


def _pc_prompt(
    race_index: str, race_name: str, class_name: str, gender: str, gear_phrase: str
) -> str:
    subject = RACE_DESCRIPTORS.get(race_index, f"a {race_name.lower()}")
    gear = f", {gear_phrase}" if gear_phrase else ""
    return (
        f"{subject} {class_name.lower()}, {gender}{gear}, "
        "fantasy character portrait, plain background, square image, high detail"
    )


def _monster_prompt(monster: dict[str, Any]) -> str:
    size = monster.get("size", "")
    type_ = monster.get("type", "creature")
    name = monster["name"]
    desc = monster.get("desc")
    detail = f" {desc[0]}" if isinstance(desc, list) and desc else (f" {desc}" if desc else "")
    return (
        f"a {size.lower()} {type_}, {name}, fantasy creature illustration, "
        f"plain background, square image, high detail.{detail}"
    )


def _generate_pc_portraits(pipe: FluxPipeline, srd: SrdIndex, limit: int | None) -> int:
    PC_DIR.mkdir(parents=True, exist_ok=True)
    races = sorted(srd.races.values(), key=lambda r: r["index"])
    classes = sorted(srd.classes.values(), key=lambda c: c["index"])
    generated = 0
    for race, cls, gender in itertools.product(races, classes, sorted(VALID_GENDERS)):
        if limit is not None and generated >= limit:
            break
        out_path = PC_DIR / f"{race['index']}_{cls['index']}_{gender}.png"
        if out_path.exists():
            continue
        gear_phrase = _default_gear_phrase(cls["index"], srd)
        prompt = _pc_prompt(race["index"], race["name"], cls["name"], gender, gear_phrase)
        try:
            t0 = time.time()
            image = _generate(pipe, prompt)
            image.save(out_path)
            generated += 1
            print(f"[pc] {out_path.name} ({time.time() - t0:.1f}s)", flush=True)
        except Exception as exc:  # noqa: BLE001 - log and keep going, same
            # graceful-fallback discipline as imagegen/service.py's scene
            # images: one bad prompt/OOM shouldn't abort a ~550-image batch.
            print(f"[pc] FAILED {out_path.name}: {exc}", file=sys.stderr, flush=True)
    return generated


def _generate_monster_portraits(pipe: FluxPipeline, srd: SrdIndex, limit: int | None) -> int:
    MONSTER_DIR.mkdir(parents=True, exist_ok=True)
    generated = 0
    for monster in sorted(srd.monsters.values(), key=lambda m: m["index"]):
        if limit is not None and generated >= limit:
            break
        out_path = MONSTER_DIR / f"{monster['index']}.png"
        if out_path.exists():
            continue
        prompt = _monster_prompt(monster)
        try:
            t0 = time.time()
            image = _generate(pipe, prompt)
            image.save(out_path)
            generated += 1
            print(f"[monster] {out_path.name} ({time.time() - t0:.1f}s)", flush=True)
        except Exception as exc:  # noqa: BLE001 - see _generate_pc_portraits
            print(f"[monster] FAILED {out_path.name}: {exc}", file=sys.stderr, flush=True)
    return generated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of NEW images generated in this run (across both "
        "sets, PC portraits first) - for a smoke test before committing to "
        "the full batch.",
    )
    parser.add_argument("--pc-only", action="store_true")
    parser.add_argument("--monsters-only", action="store_true")
    args = parser.parse_args()

    srd = load_srd()
    pipe = _load_pipeline()

    total = 0
    remaining = args.limit
    if not args.monsters_only:
        made = _generate_pc_portraits(pipe, srd, remaining)
        total += made
        if remaining is not None:
            remaining -= made
    if not args.pc_only and (remaining is None or remaining > 0):
        total += _generate_monster_portraits(pipe, srd, remaining)

    print(f"Done - generated {total} new image(s).")


if __name__ == "__main__":
    main()
