"""Live check against the real local SD-Turbo pipeline (downloads weights on
first run, ~2-3GB). Not for the default offline suite - see conftest.py."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.imagegen.service import generate_scene_image

pytestmark = pytest.mark.imagegen


def test_generates_a_real_png() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        output_path = generate_scene_image(
            "Fantasy tabletop RPG battle scene, a dwarf fighter facing a goblin in a mountain pass",
            output_dir=Path(tmp),
        )

        assert output_path is not None
        assert output_path.exists()
        assert output_path.stat().st_size > 0
        assert output_path.suffix == ".png"
