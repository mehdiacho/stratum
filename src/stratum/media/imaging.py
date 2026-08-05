"""Cheap per-frame image measures: perceptual hash and sharpness.

Both are deliberately crude. Tier 1 of S4 runs on every candidate and has to cost
essentially nothing, because it removes most of the work before the tier that
costs real compute runs.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import imagehash
import numpy as np
from PIL import Image


def perceptual_hash(path: Path, *, hash_size: int = 8) -> imagehash.ImageHash:
    """dHash — gradient-based, and more robust to the global brightness shifts that
    autoexposure produces mid-pan than the DCT hash is."""
    with Image.open(path) as img:
        return imagehash.dhash(img, hash_size=hash_size)


def hamming(a: imagehash.ImageHash, b: imagehash.ImageHash) -> int:
    return int(a - b)


def sharpness(path: Path) -> float:
    """Variance of the Laplacian.

    This matters more than it sounds. A keyframe grabbed mid-pan is
    motion-blurred and unreadable, and a vision model will hallucinate into the
    blur rather than say it cannot see.
    """
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return 0.0
    image = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if image is None:
        return 0.0
    return float(cv2.Laplacian(image, cv2.CV_64F).var())


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Both vectors are L2-normalised by contract, so this is a dot product.

    Normalised defensively anyway: a backend that quietly stops normalising would
    otherwise shift every threshold in S4 without any error.
    """
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-8 or nb < 1e-8:
        return 0.0
    return float(np.dot(a, b) / (na * nb))
