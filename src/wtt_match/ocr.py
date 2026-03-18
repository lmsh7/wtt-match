"""RapidOCR wrapper for scoreboard text extraction."""

from __future__ import annotations

import logging

import numpy as np
from rapidocr_onnxruntime import RapidOCR

logger = logging.getLogger(__name__)

_engine: RapidOCR | None = None


def get_engine() -> RapidOCR:
    """Lazily initialize and return the RapidOCR engine."""
    global _engine
    if _engine is None:
        logger.info("Initializing RapidOCR engine...")
        # det_limit_side_len=480: scoreboard ROIs are small, no need for
        # full-resolution detection. use_cls=False: scoreboard text is
        # always horizontal, skip orientation classification.
        # Together these give ~12x speedup over defaults.
        _engine = RapidOCR(det_limit_side_len=320)
    return _engine


def run_ocr(image: np.ndarray) -> list[tuple[str, float]]:
    """Run OCR on a preprocessed image.

    Returns a list of (text, confidence) tuples.
    """
    engine = get_engine()
    result, _ = engine(image, use_cls=False)
    if result is None:
        return []

    texts = []
    for item in result:
        # RapidOCR returns: [box_coords, text, confidence]
        text = item[1]
        conf = float(item[2])
        texts.append((text, conf))

    return texts
