"""OCR engines. Tesseract is the default (bundled in the worker image);
PaddleOCR is optional for higher accuracy. Both return line-level blocks with
bbox + confidence, merged and role-classified."""
import logging
from dataclasses import dataclass

import numpy as np

from vidrepro.config import get_settings

log = logging.getLogger(__name__)


@dataclass
class OcrLine:
    text: str
    bbox: tuple[int, int, int, int]  # x, y, w, h
    confidence: float  # 0..1
    role: str = "body"


def classify_role(bbox: tuple[int, int, int, int], frame_h: int, frame_w: int, text: str) -> str:
    from vidrepro.worker.textquality import is_readable, looks_like_clock

    from vidrepro.worker.textquality import DOMAIN_TOKEN

    x, y, w, h = bbox
    if y < frame_h * 0.06:
        # mobile status bar lives in the same strip as browser chrome: clock,
        # battery, carrier junk must never become a title/URL
        if looks_like_clock(text) or not is_readable(text):
            return "status_bar"
        if "/" in text or text.startswith(("http", "www.")) or "." in text.split(" ")[0]:
            return "url_bar"
        return "title_bar"
    if y < frame_h * 0.16:
        # full-phone recordings put the browser URL bar BELOW the status bar;
        # URL-shaped text in this band is chrome, not page content
        for tok in text.split():
            if DOMAIN_TOKEN.match(tok.strip(".,;:!?()\"'")):
                return "url_bar"
    if y + h > frame_h * 0.95:
        return "status_bar"
    if len(text) <= 24 and w < frame_w * 0.25 and h < frame_h * 0.06:
        return "button_like"
    return "body"


def _tesseract(image_bgr: np.ndarray) -> list[OcrLine]:
    import cv2
    import pytesseract

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    if max(gray.shape) < 1200:  # upscale small sources for better recognition
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_LANCZOS4)
        scale = 2.0
    else:
        scale = 1.0
    data = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT)

    # merge word boxes into lines keyed by (block, par, line)
    lines: dict[tuple, dict] = {}
    for i in range(len(data["text"])):
        word = (data["text"][i] or "").strip()
        conf = float(data["conf"][i])
        if not word or conf < 30:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        x, y, w, h = (int(data[k][i] / scale) for k in ("left", "top", "width", "height"))
        entry = lines.setdefault(key, {"words": [], "confs": [], "x0": x, "y0": y, "x1": x + w, "y1": y + h})
        entry["words"].append(word)
        entry["confs"].append(conf)
        entry["x0"] = min(entry["x0"], x)
        entry["y0"] = min(entry["y0"], y)
        entry["x1"] = max(entry["x1"], x + w)
        entry["y1"] = max(entry["y1"], y + h)

    out = []
    for entry in lines.values():
        bbox = (entry["x0"], entry["y0"], entry["x1"] - entry["x0"], entry["y1"] - entry["y0"])
        out.append(OcrLine(
            text=" ".join(entry["words"]),
            bbox=bbox,
            confidence=round(sum(entry["confs"]) / len(entry["confs"]) / 100.0, 3),
        ))
    return out


def _paddle(image_bgr: np.ndarray) -> list[OcrLine]:
    from paddleocr import PaddleOCR  # optional dependency

    global _paddle_engine
    if "_paddle_engine" not in globals():
        _paddle_engine = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)
    result = _paddle_engine.ocr(image_bgr, cls=False)
    out = []
    for line in (result[0] or []):
        pts, (text, conf) = line
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        out.append(OcrLine(text=text, confidence=float(conf),
                           bbox=(int(min(xs)), int(min(ys)),
                                 int(max(xs) - min(xs)), int(max(ys) - min(ys)))))
    return out


def extract_lines(image_bgr: np.ndarray) -> list[OcrLine]:
    engine = get_settings().ocr_engine
    if engine == "none":
        return []
    try:
        lines = _paddle(image_bgr) if engine == "paddle" else _tesseract(image_bgr)
    except Exception as e:  # OCR failure degrades the job, never kills it
        log.warning("OCR failed on frame: %s", e)
        return []
    h, w = image_bgr.shape[:2]
    for line in lines:
        line.role = classify_role(line.bbox, h, w, line.text)
    return lines
