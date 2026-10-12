from pathlib import Path

import cv2
import pytest

from app.services.detector import detect, merge_overlapping
from tests.images import BLUE, DARK_GRAY, RED, YELLOW, make_photo


def overlaps(bbox, rect, tolerance=40):
    x, y, w, h = bbox
    rx, ry, rw, rh, _ = rect
    return (
        abs(x - rx) <= tolerance
        and abs(y - ry) <= tolerance
        and abs((x + w) - (rx + rw)) <= tolerance
        and abs((y + h) - (ry + rh)) <= tolerance
    )


def test_detects_each_brick_and_writes_crops(tmp_path):
    bricks = [
        (100, 150, 240, 120, RED),
        (700, 200, 120, 120, BLUE),
        (300, 700, 360, 120, YELLOW),
        (1200, 800, 120, 240, DARK_GRAY),
    ]
    photo = make_photo(tmp_path / "photo.jpg", bricks)

    crops = detect(photo, tmp_path / "crops")

    assert len(crops) == 4
    for brick in bricks:
        assert any(overlaps(c.bbox, brick) for c in crops), brick
    for crop in crops:
        assert crop.path.parent == tmp_path / "crops"
        x, y, w, h = crop.bbox
        image = cv2.imread(str(crop.path))
        # crops are padded around the brick, cut from the full-resolution photo
        assert image.shape[0] >= h and image.shape[1] >= w


def test_crops_are_ordered_top_to_bottom_left_to_right(tmp_path):
    bricks = [(900, 900, 150, 150, RED), (100, 100, 150, 150, BLUE), (900, 100, 150, 150, YELLOW)]
    crops = detect(make_photo(tmp_path / "p.jpg", bricks), tmp_path / "crops")
    assert [c.bbox[:2] for c in crops] == sorted(
        (c.bbox[:2] for c in crops), key=lambda xy: (xy[1] // 200, xy[0])
    )


def test_ignores_specks(tmp_path):
    bricks = [(400, 400, 300, 150, RED), (100, 100, 6, 6, BLUE)]
    crops = detect(make_photo(tmp_path / "p.jpg", bricks), tmp_path / "crops")
    assert len(crops) == 1


def test_single_close_up_brick_falls_back_to_whole_photo(tmp_path):
    # A brick filling most of the frame (the MVP "one brick per photo" case).
    photo = make_photo(tmp_path / "p.jpg", [(20, 20, 1560, 1160, RED)])
    [crop] = detect(photo, tmp_path / "crops")
    assert crop.path == photo
    assert crop.bbox is None


def test_empty_background_falls_back_to_whole_photo(tmp_path):
    photo = make_photo(tmp_path / "p.jpg", [])
    [crop] = detect(photo, tmp_path / "crops")
    assert crop.path == photo


def test_unreadable_image_raises(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    with pytest.raises(ValueError):
        detect(bad, tmp_path / "crops")


def test_merge_overlapping_boxes():
    split_brick = [(100, 100, 100, 50), (120, 120, 100, 50)]
    neighbours = [(400, 100, 100, 100), (495, 100, 100, 100)]  # barely touching
    assert sorted(merge_overlapping(split_brick + neighbours)) == [
        (100, 100, 120, 70),
        (400, 100, 100, 100),
        (495, 100, 100, 100),
    ]
