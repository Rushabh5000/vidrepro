"""Global motion (scroll/swipe) via phase correlation and cursor/touch
tracking via small-mover blob detection on frame differences."""
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Motion:
    t_ms: int
    dx: float
    dy: float


@dataclass
class CursorHit:
    t_ms: int
    x: int
    y: int
    area: float
    conf: float


def global_shift(prev_gray: np.ndarray, gray: np.ndarray) -> tuple[float, float]:
    """Dominant translation between frames (dx, dy) in source pixels."""
    a = np.float32(prev_gray)
    b = np.float32(gray)
    (dx, dy), _response = cv2.phaseCorrelate(a, b)
    return float(dx), float(dy)


def small_mover(prev_gray: np.ndarray, gray: np.ndarray, t_ms: int,
                max_area_frac: float = 0.004) -> CursorHit | None:
    """Find a small localized change blob — the classic signature of a cursor
    move or a touch indicator — while ignoring large content changes."""
    diff = cv2.absdiff(prev_gray, gray)
    _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
    thresh = cv2.dilate(thresh, np.ones((3, 3), np.uint8), iterations=1)
    changed_frac = float(np.count_nonzero(thresh)) / thresh.size
    if changed_frac > 0.05:  # too much changed — scrolling/transition, not a cursor
        return None
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    h, w = gray.shape[:2]
    best = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(best)
    if area <= 4 or area > max_area_frac * h * w:
        return None
    m = cv2.moments(best)
    if m["m00"] == 0:
        return None
    cx, cy = int(m["m10"] / m["m00"]), int(m["m01"] / m["m00"])
    # roundness: touch indicators / cursors are compact blobs
    peri = cv2.arcLength(best, True)
    roundness = 4 * np.pi * area / (peri * peri) if peri > 0 else 0
    conf = 0.5 + 0.4 * min(1.0, roundness)
    return CursorHit(t_ms=t_ms, x=cx, y=cy, area=float(area), conf=round(conf, 2))


def region_change(prev_gray: np.ndarray, gray: np.ndarray,
                  center: tuple[int, int], radius: int = 60) -> float:
    """Fraction of pixels changed inside a window around `center` — used to
    confirm that a dwell was followed by a localized UI reaction (click)."""
    h, w = gray.shape[:2]
    x, y = center
    x0, x1 = max(0, x - radius), min(w, x + radius)
    y0, y1 = max(0, y - radius), min(h, y + radius)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    diff = cv2.absdiff(prev_gray[y0:y1, x0:x1], gray[y0:y1, x0:x1])
    return float(np.count_nonzero(diff > 25)) / diff.size
