"""Image preprocessing for scoreboard OCR.

Pipeline: ROI crop -> CLAHE -> Gaussian blur -> sharpen -> multiple binarizations.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ROI:
    """Region of interest as fractional coordinates (0-1)."""

    name: str
    x_start: float
    y_start: float
    x_end: float
    y_end: float


# WTT scoreboard candidate regions (fractional coords)
SCOREBOARD_ROIS = [
    ROI("bottom_left", 0.0, 0.75, 0.55, 1.0),     # bottom-left scoreboard (primary)
    ROI("top_left", 0.0, 0.0, 0.45, 0.18),         # top-left fallback
]

SCALE_FACTOR = 2


def crop_roi(frame: np.ndarray, roi: ROI) -> np.ndarray:
    """Crop a region of interest from the frame."""
    h, w = frame.shape[:2]
    x1 = int(w * roi.x_start)
    y1 = int(h * roi.y_start)
    x2 = int(w * roi.x_end)
    y2 = int(h * roi.y_end)
    return frame[y1:y2, x1:x2]


def upscale(image: np.ndarray, factor: int = SCALE_FACTOR) -> np.ndarray:
    """Upscale image using cubic interpolation."""
    h, w = image.shape[:2]
    return cv2.resize(image, (w * factor, h * factor), interpolation=cv2.INTER_CUBIC)


def enhance(image: np.ndarray) -> np.ndarray:
    """Apply CLAHE + Gaussian blur + sharpening."""
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)

    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)

    lab = cv2.merge([l_channel, a_channel, b_channel])
    enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    # GaussianBlur is ~19x faster than bilateralFilter and sufficient
    # for scoreboard text denoising before binarization.
    enhanced = cv2.GaussianBlur(enhanced, (5, 5), 0)

    kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]], dtype=np.float32)
    enhanced = cv2.filter2D(enhanced, -1, kernel)

    return enhanced


def binarize_multi(image: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """Produce multiple binarization variants for OCR.

    WTT scoreboards have light text on dark background, so we need
    both normal and inverted thresholding.

    Returns list of (variant_name, binary_image) tuples.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    results = []

    # 1. Adaptive threshold (good for varied lighting)
    adaptive = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 4
    )
    results.append(("adaptive", adaptive))

    # 2. OTSU (auto-threshold, good for bimodal histograms)
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    results.append(("otsu", otsu))

    # 3. OTSU inverted (light text on dark bg -> dark text on light bg)
    _, otsu_inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    results.append(("otsu_inv", otsu_inv))

    # 4. Enhanced grayscale without binarization (let OCR handle it)
    results.append(("gray", gray))

    return results


def preprocess_roi(frame: np.ndarray, roi: ROI) -> list[tuple[str, np.ndarray]]:
    """Full preprocessing pipeline for a single ROI.

    Returns multiple binarization variants as (variant_name, image) tuples.
    """
    cropped = crop_roi(frame, roi)
    enhanced = enhance(cropped)
    return binarize_multi(enhanced)


def preprocess_frame(
    frame: np.ndarray,
) -> list[tuple[ROI, str, np.ndarray]]:
    """Preprocess all candidate ROIs from a frame.

    Returns list of (roi, variant_name, processed_image) tuples.
    """
    results = []
    for roi in SCOREBOARD_ROIS:
        variants = preprocess_roi(frame, roi)
        for variant_name, processed in variants:
            results.append((roi, variant_name, processed))
    return results


# Priority order: otsu_inv works best for WTT light-on-dark scoreboards,
# then adaptive, then otsu, then gray. Primary ROI first.
_VARIANT_PRIORITY = ["otsu_inv", "adaptive", "otsu", "gray"]


def preprocess_frame_prioritized(
    frame: np.ndarray,
) -> Generator[tuple[ROI, str, np.ndarray], None, None]:
    """Yield (roi, variant_name, image) in priority order for early-exit OCR.

    Processes primary ROI first with the most likely variant (otsu_inv),
    so the caller can stop as soon as a good result is found.
    """
    for roi in SCOREBOARD_ROIS:
        cropped = crop_roi(frame, roi)
        enhanced = enhance(cropped)
        variants = dict(binarize_multi(enhanced))
        for variant_name in _VARIANT_PRIORITY:
            if variant_name in variants:
                yield (roi, variant_name, variants[variant_name])
