"""Brick detection: split a photo into one crop per brick.

Classic OpenCV segmentation for bricks spread out on a fairly plain surface:
model the surface color (allowing a lighting gradient), mark pixels that differ
from it or lie on an edge stronger than the surface texture, clean the mask up
and take one bounding box per blob. Bricks that touch end up in one crop; a YOLO
model can replace this later for cluttered piles.
"""

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

WORK_SIZE = 1000  # longest side, in pixels, used for segmentation
MIN_AREA = 0.0008  # of the image area; smaller blobs are specks or noise
CLOSE_UP_AREA = 0.25  # a single blob this large means one brick fills the photo
COLOR_THRESHOLD = 18.0  # Lab distance from the background model
LIGHTNESS_WEIGHT = 0.5  # shadows and lighting change lightness more than color
EDGE_LOW, EDGE_HIGH = 20, 50  # Canny thresholds on the blurred gray image
MERGE_OVERLAP = 0.3  # boxes overlapping this much of the smaller one are merged
PADDING = 0.15  # crop margin, as a fraction of the brick's longer side

Box = tuple[int, int, int, int]  # x, y, width, height


@dataclass(frozen=True)
class Crop:
    path: Path
    bbox: Box | None  # in source photo pixels; None when the crop is the whole photo


def detect(photo_path: Path, crops_dir: Path) -> list[Crop]:
    """One crop per brick, ordered in reading order (rows, then left to right).

    Falls back to the whole photo when nothing, or one brick filling the frame,
    is found, so close-ups of a single brick work as before.
    """
    image = cv2.imread(str(photo_path), cv2.IMREAD_COLOR)  # applies EXIF orientation
    if image is None:
        raise ValueError(f"Cannot read image {photo_path.name}")

    height, width = image.shape[:2]
    scale = min(1.0, WORK_SIZE / max(height, width))
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    boxes = find_bricks(small)

    small_area = small.shape[0] * small.shape[1]
    close_up = len(boxes) == 1 and boxes[0][2] * boxes[0][3] > CLOSE_UP_AREA * small_area
    if not boxes or close_up:
        return [Crop(path=photo_path, bbox=None)]

    band = max(small.shape[0] // 8, 1)
    boxes.sort(key=lambda b: ((b[1] + b[3] // 2) // band, b[0]))

    crops_dir.mkdir(parents=True, exist_ok=True)
    crops = []
    for index, (x, y, w, h) in enumerate(boxes):
        bbox = (round(x / scale), round(y / scale), round(w / scale), round(h / scale))
        crop_path = crops_dir / f"{photo_path.stem}_{index:02d}.jpg"
        cv2.imwrite(str(crop_path), cut_padded(image, bbox), [cv2.IMWRITE_JPEG_QUALITY, 92])
        crops.append(Crop(path=crop_path, bbox=bbox))
    return crops


def find_bricks(image: np.ndarray) -> list[Box]:
    mask = foreground_mask(image)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = MIN_AREA * image.shape[0] * image.shape[1]
    boxes = [cv2.boundingRect(c) for c in contours if cv2.contourArea(c) >= min_area]
    return merge_overlapping(boxes)


def merge_overlapping(boxes: list[Box], min_overlap: float = MERGE_OVERLAP) -> list[Box]:
    """Merge boxes covering much of each other: one brick split into two blobs."""
    boxes = list(boxes)
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                (ax, ay, aw, ah), (bx, by, bw, bh) = boxes[i], boxes[j]
                ix = min(ax + aw, bx + bw) - max(ax, bx)
                iy = min(ay + ah, by + bh) - max(ay, by)
                if ix > 0 and iy > 0 and ix * iy >= min_overlap * min(aw * ah, bw * bh):
                    x, y = min(ax, bx), min(ay, by)
                    boxes[i] = (x, y, max(ax + aw, bx + bw) - x, max(ay + ah, by + bh) - y)
                    del boxes[j]
                    merged = True
                    break
            if merged:
                break
    return boxes


def foreground_mask(image: np.ndarray) -> np.ndarray:
    """Pixels that differ in color from the surface, or lie on a brick's outline.

    Color alone misses bricks with the surface's color (light gray on gray, white
    on white); their outlines and studs still produce edges.
    """
    blurred = cv2.GaussianBlur(image, (5, 5), 0)
    lab = cv2.cvtColor(blurred, cv2.COLOR_BGR2LAB).astype(np.float32)
    distance_sq = np.zeros(lab.shape[:2], np.float32)
    for channel, weight in zip(range(3), (LIGHTNESS_WEIGHT, 1.0, 1.0)):
        diff = lab[:, :, channel] - background_plane(lab[:, :, channel])
        distance_sq += (weight * diff) ** 2
    color_mask = (np.sqrt(distance_sq) > COLOR_THRESHOLD).astype(np.uint8) * 255
    color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    gray = cv2.cvtColor(blurred, cv2.COLOR_BGR2GRAY)
    low, high = edge_thresholds(gray)
    edges = cv2.Canny(gray, low, high, L2gradient=True)

    # Bridge the faces, studs and outline pieces of one brick, then fill holes.
    mask = cv2.morphologyEx(color_mask | edges, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, contours, -1, 255, thickness=cv2.FILLED)
    # Drop thin edge fragments (surface texture, scratches) that did not close up.
    return cv2.morphologyEx(filled, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))


def edge_thresholds(gray: np.ndarray) -> tuple[float, float]:
    """Canny thresholds above the surface's own texture (wood grain, fabric).

    The texture is measured at the image border, where there are usually no bricks.
    """
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    texture = np.percentile(cv2.magnitude(gx, gy)[border_mask(gray.shape)], 98)
    low = max(EDGE_LOW, 1.5 * texture)
    return low, max(EDGE_HIGH, 2.5 * low)


def border_mask(shape: tuple[int, ...], border: float = 0.04) -> np.ndarray:
    height, width = shape[:2]
    margin = max(int(min(height, width) * border), 2)
    edge = np.zeros((height, width), bool)
    edge[:margin, :] = edge[-margin:, :] = edge[:, :margin] = edge[:, -margin:] = True
    return edge


def background_plane(channel: np.ndarray) -> np.ndarray:
    """Fit value = a*x + b*y + c to the image border, ignoring outliers (bricks)."""
    height, width = channel.shape
    ys, xs = np.mgrid[0:height, 0:width]
    edge = border_mask(channel.shape)
    x, y, v = xs[edge].astype(np.float32), ys[edge].astype(np.float32), channel[edge]
    keep = np.ones(v.shape, bool)
    for _ in range(2):
        design = np.column_stack([x[keep], y[keep], np.ones(keep.sum(), np.float32)])
        coeffs, *_ = np.linalg.lstsq(design, v[keep], rcond=None)
        residual = np.abs(v - (coeffs[0] * x + coeffs[1] * y + coeffs[2]))
        mad = np.median(residual[keep]) + 1e-3
        keep = residual < 3 * 1.4826 * mad + 2
    return (coeffs[0] * xs + coeffs[1] * ys + coeffs[2]).astype(np.float32)


def cut_padded(image: np.ndarray, bbox: Box) -> np.ndarray:
    x, y, w, h = bbox
    pad = max(int(PADDING * max(w, h)), 12)
    height, width = image.shape[:2]
    top, bottom = max(y - pad, 0), min(y + h + pad, height)
    left, right = max(x - pad, 0), min(x + w + pad, width)
    return image[top:bottom, left:right]
