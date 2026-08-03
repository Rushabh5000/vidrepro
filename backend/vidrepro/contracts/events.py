"""Typed observation-log contracts passed between pipeline stages (the
'artifact bus'). Deterministic stages emit these; synthesis consumes them."""
from typing import Literal, Optional

from pydantic import BaseModel

ActionType = Literal[
    "click", "double_click", "tap", "long_press", "type", "scroll", "swipe",
    "drag", "navigate", "back", "tab_switch", "menu_open", "dialog_open",
    "file_upload", "form_submit", "unknown",
]

AnomalyType = Literal[
    "error_text", "blank_screen", "crash_dialog", "stall", "layout_shift", "app_exit",
]


class SegmentInfo(BaseModel):
    idx: int
    start_ms: int
    end_ms: int
    transition_type: str = "cut"
    label: str = ""


class OcrBlock(BaseModel):
    frame_id: str
    t_ms: int
    text: str
    bbox: list[int]  # x, y, w, h
    confidence: float
    role: str = "body"


class DetectedEvent(BaseModel):
    id: str
    t_start_ms: int
    t_end_ms: int
    type: ActionType
    pos: Optional[list[int]] = None  # [x, y] if known
    detail: dict = {}
    signals: list[str] = []
    confidence: float = 0.5


class Anomaly(BaseModel):
    id: str
    t_ms: int
    type: AnomalyType
    detail: dict = {}
    evidence_frame_id: str = ""
    score: float = 0.5


class MotionSample(BaseModel):
    t_ms: int
    dx: float
    dy: float


class CursorSample(BaseModel):
    t_ms: int
    x: int
    y: int
    conf: float = 0.5
