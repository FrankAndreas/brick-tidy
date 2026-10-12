"""Brick detection: split a photo into one crop per brick.

MVP: the whole photo is treated as a single brick. Roadmap step 2 replaces this
with OpenCV segmentation (and later YOLO) producing several crops per photo.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Crop:
    path: Path
    bbox: tuple[int, int, int, int] | None  # x, y, width, height in the source photo


def detect(photo_path: Path, crops_dir: Path) -> list[Crop]:
    return [Crop(path=photo_path, bbox=None)]
