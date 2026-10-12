"""Brick color: measure it in a crop and rank catalog colors by similarity.

Photos are taken under unknown light, so the measured color is only used to
rank a short list (the colors a part has in the set), never to decide alone.
"""

import cv2
import numpy as np

from app.services.detector import foreground_mask

MEASURE_SIZE = 600  # longest side used for measuring, in pixels
MIN_PIXELS = 50  # fewer brick pixels than this: no measurement
NEUTRAL_SURFACE_CHROMA = 20.0  # surfaces this gray are used to correct white balance
LIGHTNESS_WEIGHT = 2.0  # CIE94 k_L: light and shade change lightness more than hue
# A catalog color this close to the measured one counts as a possible match. Tested on
# catalog renders under varied light: 98% of true colors are within it.
MATCH_DISTANCE = 20.0


def measure_color(image_path) -> str | None:
    """The brick's typical color as "RRGGBB", or None if no brick is found.

    The brick is segmented like in detection; highlights and deep shadows are
    ignored. If the surrounding surface is close to gray, it is used to correct
    the white balance.
    """
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        return None
    scale = min(1.0, MEASURE_SIZE / max(image.shape[:2]))
    if scale < 1.0:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    mask = largest_blob(foreground_mask(image))
    brick = cv2.erode(mask, np.ones((5, 5), np.uint8), iterations=2) > 0
    surface = cv2.dilate(mask, np.ones((15, 15), np.uint8)) == 0
    if brick.sum() < MIN_PIXELS:
        return None

    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255
    pixels = rgb[brick]
    if surface.sum() >= MIN_PIXELS:
        surface_rgb = np.median(rgb[surface], axis=0)
        if chroma(to_lab(surface_rgb)) < NEUTRAL_SURFACE_CHROMA:
            pixels = np.clip(pixels * (surface_rgb.mean() / np.maximum(surface_rgb, 1e-3)), 0, 1)

    lab = cv2.cvtColor(pixels[None, :, :], cv2.COLOR_RGB2LAB)[0]
    low, high = np.percentile(lab[:, 0], [10, 90])
    typical = np.median(lab[(lab[:, 0] >= low) & (lab[:, 0] <= high)], axis=0)
    out = cv2.cvtColor(typical[None, None, :].astype(np.float32), cv2.COLOR_LAB2RGB)[0, 0]
    return "".join(f"{round(v * 255):02X}" for v in np.clip(out, 0, 1))


def largest_blob(mask: np.ndarray) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count <= 1:
        return np.zeros_like(mask)
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return (labels == biggest).astype(np.uint8) * 255


def to_lab(rgb) -> np.ndarray:
    """RGB in 0..1 (or "RRGGBB") to CIE Lab with L in 0..100."""
    if isinstance(rgb, str):
        rgb = [int(rgb[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    return cv2.cvtColor(np.asarray(rgb, np.float32).reshape(1, 1, 3), cv2.COLOR_RGB2LAB)[0, 0]


def chroma(lab: np.ndarray) -> float:
    return float(np.hypot(lab[1], lab[2]))


def color_distance(measured: str, catalog: str) -> float:
    """CIE94 color difference with the catalog color as reference.

    Photos wash out saturated colors, and CIE94 tolerates chroma differences
    more as chroma grows; lightness is down-weighted (textile weighting).
    """
    ref, sample = to_lab(catalog), to_lab(measured)
    c_ref, c_sample = chroma(ref), chroma(sample)
    d_l = ref[0] - sample[0]
    d_c = c_ref - c_sample
    d_h_sq = max((ref[1] - sample[1]) ** 2 + (ref[2] - sample[2]) ** 2 - d_c**2, 0.0)
    return float(
        np.sqrt(
            (d_l / LIGHTNESS_WEIGHT) ** 2
            + (d_c / (1 + 0.045 * c_ref)) ** 2
            + d_h_sq / (1 + 0.015 * c_ref) ** 2
        )
    )
