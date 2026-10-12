"""Synthetic photos of bricks on a plain surface."""

from pathlib import Path

import cv2
import numpy as np


def make_photo(path: Path, bricks, size=(1200, 1600), background=(225, 228, 230), noise=6):
    """Draw bricks (x, y, w, h, BGR color) on a slightly noisy, unevenly lit background."""
    height, width = size
    rng = np.random.default_rng(0)
    image = np.empty((height, width, 3), np.float32)
    image[:] = background
    image *= np.linspace(0.92, 1.0, width, dtype=np.float32)[None, :, None]  # light falloff
    image += rng.normal(0, noise, image.shape)
    for x, y, w, h, color in bricks:
        cv2.rectangle(image, (x, y), (x + w, y + h), color, thickness=-1)
        # a darker top face edge, like a real brick
        cv2.rectangle(image, (x, y), (x + w, y + h // 6), [c * 0.8 for c in color], thickness=-1)
    cv2.imwrite(str(path), np.clip(image, 0, 255).astype(np.uint8))
    return path


RED = (9, 26, 201)
BLUE = (191, 85, 0)
YELLOW = (21, 205, 242)
DARK_GRAY = (85, 89, 99)
