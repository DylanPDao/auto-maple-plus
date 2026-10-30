from __future__ import annotations

import numpy as np

from mlbot.tools.yt_frames import crop_frac, is_blank, is_new, thumb


def scene(seed: int) -> np.ndarray:
    # big random blocks: pixel noise would average out to flat grey in the thumbnail
    blocks = np.random.default_rng(seed).integers(0, 255, (9, 16, 3), dtype=np.uint8)
    return np.kron(blocks, np.ones((80, 80, 1), np.uint8))


def test_dedup_and_blank() -> None:
    a, b = thumb(scene(1)), thumb(scene(2))
    assert is_new(a, None, 4.0)
    assert not is_new(a, a, 4.0)
    assert is_new(b, a, 4.0)
    assert is_blank(thumb(np.zeros((720, 1280, 3), np.uint8)))
    assert not is_blank(a)


def test_crop_frac() -> None:
    image = scene(3)
    assert crop_frac(image, None) is image
    assert crop_frac(image, (0, 0, 1, 0.5)).shape == (360, 1280, 3)
    assert crop_frac(image, (0.5, 0.25, 0.25, 0.5)).shape == (360, 320, 3)
