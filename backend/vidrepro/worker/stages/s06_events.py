"""s06: deterministic event detection over the observation log.

Consumes s03 (segments + change samples), s04 (OCR), s05 (motion + cursor).
Emits typed events (click/tap/scroll/type/navigate/dialog_open/form_submit)
and anomaly candidates (error_text/blank_screen/stall) with signal lists and
confidence priors. No pixels are re-read here — this stage is a pure function
over persisted artifacts, which makes it unit-testable with golden files.

Precision beats recall everywhere in this file: an invented tap or a garbage
target name costs the reader more than a missed low-confidence event.
"""
import re
import uuid

from vidrepro.db.models import InferredAction, ProcessingJob, Video
from vidrepro.db.session import db_session
from vidrepro.worker import artifacts
from vidrepro.worker.textquality import (
    clean,
    is_readable,
    label_quality,
    sanitize_label,
    typed_text_ok,
)

ERROR_PATTERNS = re.compile(
    r"\b(error|failed|failure|exception|crash|undefined|null|NaN|oops|"
    r"something went wrong|not found|cannot|unable to|invalid)\b", re.IGNORECASE)
SUBMIT_WORDS = re.compile(
    r"^(submit|save|apply|continue|confirm|ok|sign in|log in|login|register|"
    r"send|next|checkout|pay|add|create|update|delete)$", re.IGNORECASE)

SCROLL_MIN_PX = 3.0
SCROLL_GAP_MS = 600
CLICK_LOOKBACK_MS = 1200
CLICK_DEDUPE_MS = 800
SCROLL_EXPLAINS_MS = 500  # a transition this close to scrolling is content moving


def _eid() -> str:
    return uuid.uuid4().hex[:10]


def detect_scrolls(motions: list[dict]) -> list[dict]:
    events, group = [], []
    for m in motions:
        if abs(m["dy"]) < SCROLL_MIN_PX:
            continue
        if group and (m["t_ms"] - group[-1]["t_ms"] > SCROLL_GAP_MS
                      or (m["dy"] > 0) != (group[-1]["dy"] > 0)):
            events.append(_scroll_event(group))
            group = []
        group.append(m)
    if group:
        events.append(_scroll_event(group))
    return events


def _scroll_event(group: list[dict]) -> dict:
    total = sum(m["dy"] for m in group)
    return {
        "id": _eid(), "type": "scroll",
        "t_start_ms": group[0]["t_ms"], "t_end_ms": group[-1]["t_ms"],
        "detail": {"direction": "down" if total < 0 else "up",
                   "magnitude_px": round(abs(total), 1)},
        "signals": ["global_motion"], "confidence": 0.92,
    }


def _scroll_explains(motions: list[dict], t_ms: int) -> bool:
    """True when sustained scrolling overlaps this transition — the frame
    change is content moving under the finger, not a new screen."""
    return any(abs(m["dy"]) >= SCROLL_MIN_PX
               and t_ms - SCROLL_EXPLAINS_MS <= m["t_ms"] <= t_ms + 250
               for m in motions)


def detect_clicks(segments: list[dict], cursor_track: list[dict],
                  coord_scale: float, device_class: str,
                  motions: list[dict] | None = None) -> list[dict]:
    """Anchor click/tap inference to state transitions: the last stationary
    cursor/touch position shortly before a transition is the likely activation.

    Rules that keep this honest:
    - transitions overlapping a scroll are skipped (scrolling explains them);
    - a dialog transition without a nearby cursor is NOT a click — dialogs
      open on their own all the time (toasts, timeouts, push prompts);
    - two inferred clicks within CLICK_DEDUPE_MS collapse into the better one.
    """
    motions = motions or []
    events = []
    for seg in segments:
        if seg["transition_type"] not in ("nav", "dialog"):
            continue
        t = seg["start_ms"]
        if _scroll_explains(motions, t):
            continue
        near = [c for c in cursor_track if t - CLICK_LOOKBACK_MS <= c["t_ms"] <= t + 150]
        etype = "tap" if device_class == "mobile" else "click"
        if near:
            hit = near[-1]
            events.append({
                "id": _eid(), "type": etype,
                "t_start_ms": hit["t_ms"], "t_end_ms": t,
                "pos": [int(hit["x"] * coord_scale), int(hit["y"] * coord_scale)],
                "detail": {"led_to_segment": seg["idx"]},
                "signals": ["cursor_dwell", "transition"],
                "confidence": 0.75,
            })
        elif seg["transition_type"] == "nav":
            # effect-only inference: something activated this full-screen change
            events.append({
                "id": _eid(), "type": etype,
                "t_start_ms": max(0, t - 400), "t_end_ms": t,
                "pos": None,
                "detail": {"led_to_segment": seg["idx"]},
                "signals": ["transition_only"],
                "confidence": 0.55,
            })
    return dedupe_clicks(events)


def dedupe_clicks(events: list[dict]) -> list[dict]:
    """Collapse bursts of click inferences: within CLICK_DEDUPE_MS keep the
    highest-confidence one (cursor-anchored beats transition-only)."""
    out: list[dict] = []
    for e in sorted(events, key=lambda x: x["t_start_ms"]):
        if out and e["t_start_ms"] - out[-1]["t_start_ms"] < CLICK_DEDUPE_MS:
            if e["confidence"] > out[-1]["confidence"]:
                out[-1] = e
            continue
        out.append(e)
    return out


def _iou(a: list[int], b: list[int]) -> float:
    ax0, ay0, ax1, ay1 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    ix = max(0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union else 0.0


def detect_typing(ocr_blocks: list[dict], frames: list[dict]) -> list[dict]:
    """Field text growing between consecutive keyframes of the same segment.

    OCR jitter on dynamic content (prices, clocks, feeds) also "grows", so the
    grown text must read as something a human could actually have typed."""
    events = []
    by_frame: dict[str, list[dict]] = {}
    for block in ocr_blocks:
        by_frame.setdefault(block["frame_id"], []).append(block)
    ordered = sorted(frames, key=lambda f: f["t_ms"])
    for prev, cur in zip(ordered, ordered[1:]):
        if prev["segment_idx"] != cur["segment_idx"]:
            continue
        for pb in by_frame.get(prev["frame_id"], []):
            for cb in by_frame.get(cur["frame_id"], []):
                if _iou(pb["bbox"], cb["bbox"]) < 0.3:
                    continue
                if cb["confidence"] < 0.5 or cb["role"] == "status_bar":
                    continue
                p_text, c_text = clean(pb["text"]), clean(cb["text"])
                # prior text must be a real prefix (empty field or >= 2 chars);
                # single-glyph "prefixes" are icon OCR, not field content
                if (c_text != p_text and len(c_text) > len(p_text)
                        and (p_text == "" or len(p_text) >= 2)
                        and c_text.lower().startswith(p_text.lower()[:3])
                        and len(c_text) - len(p_text) >= 3
                        and typed_text_ok(c_text)):
                    events.append({
                        "id": _eid(), "type": "type",
                        "t_start_ms": prev["t_ms"], "t_end_ms": cur["t_ms"],
                        "pos": [cb["bbox"][0] + cb["bbox"][2] // 2,
                                cb["bbox"][1] + cb["bbox"][3] // 2],
                        "detail": {"final_text": c_text[:200]},
                        "signals": ["ocr_text_growth"], "confidence": 0.85,
                    })
    return events


def detect_navigation(segments: list[dict], ocr_blocks: list[dict],
                      frames: list[dict]) -> list[dict]:
    events = []
    frame_by_seg: dict[int, list[dict]] = {}
    for f in frames:
        frame_by_seg.setdefault(f["segment_idx"], []).append(f)
    blocks_by_frame: dict[str, list[dict]] = {}
    for b in ocr_blocks:
        blocks_by_frame.setdefault(b["frame_id"], []).append(b)

    for seg in segments:
        if seg["idx"] == 0 or seg["transition_type"] not in ("nav", "dialog"):
            continue
        if seg["transition_type"] == "dialog":
            events.append({
                "id": _eid(), "type": "dialog_open",
                "t_start_ms": seg["start_ms"], "t_end_ms": seg["start_ms"] + 300,
                "pos": None, "detail": {"segment": seg["idx"]},
                "signals": ["localized_overlay"], "confidence": 0.7,
            })
            continue
        target = ""
        for f in frame_by_seg.get(seg["idx"], [])[:1]:
            for b in blocks_by_frame.get(f["frame_id"], []):
                if b["role"] in ("url_bar", "title_bar") and b["confidence"] > 0.45:
                    target = sanitize_label(b["text"], max_len=80)
                    if target:
                        break
        events.append({
            "id": _eid(), "type": "navigate",
            "t_start_ms": seg["start_ms"], "t_end_ms": seg["start_ms"] + 300,
            "pos": None, "detail": {"target": target, "segment": seg["idx"]},
            "signals": ["segment_transition"], "confidence": 0.8,
        })
    return events


def detect_anomalies(ocr_blocks: list[dict], samples: list[dict],
                     events: list[dict]) -> list[dict]:
    anomalies = []
    for b in ocr_blocks:
        text = clean(b["text"])
        if b["confidence"] >= 0.45 and is_readable(text) \
                and ERROR_PATTERNS.search(text):
            anomalies.append({
                "id": _eid(), "type": "error_text", "t_ms": b["t_ms"],
                "detail": {"text": text[:300], "frame_id": b["frame_id"]},
                "score": min(0.85, 0.5 + b["confidence"] * 0.4),
            })
    # blank screen: near-black or near-white sampled frames
    for s in samples:
        if s.get("lum", 100) < 8:
            anomalies.append({"id": _eid(), "type": "blank_screen", "t_ms": s["t_ms"],
                              "detail": {"lum": s["lum"]}, "score": 0.7})
    # stall: >8s with essentially no change following the last detected action
    if events and samples:
        last_action_t = max(e["t_end_ms"] for e in events)
        tail = [s for s in samples if s["t_ms"] > last_action_t]
        if tail and all(s["score"] < 0.01 for s in tail):
            span = tail[-1]["t_ms"] - tail[0]["t_ms"]
            if span > 8000:
                anomalies.append({
                    "id": _eid(), "type": "stall", "t_ms": tail[0]["t_ms"],
                    "detail": {"static_ms": span}, "score": 0.5,
                })
    # dedupe blank_screen runs (keep first of consecutive)
    deduped, last_blank = [], -10_000
    for a in sorted(anomalies, key=lambda x: x["t_ms"]):
        if a["type"] == "blank_screen":
            if a["t_ms"] - last_blank < 2000:
                continue
            last_blank = a["t_ms"]
        deduped.append(a)
    return deduped


def run(job_id: str) -> None:
    with db_session() as db:
        job = db.get(ProcessingJob, job_id)
        video = db.get(Video, job.video_id)
        org_id, device_class = video.org_id, video.device_class

    seg_art = artifacts.load(org_id, job_id, "s03_segment")
    ocr_art = artifacts.load(org_id, job_id, "s04_frames_ocr")
    mot_art = artifacts.load(org_id, job_id, "s05_motion")

    segments = seg_art["segments"]
    events: list[dict] = []
    events += detect_scrolls(mot_art["motions"])
    events += detect_clicks(segments, mot_art["cursor_track"],
                            mot_art.get("coord_scale", 1.0), device_class,
                            motions=mot_art["motions"])
    events += detect_typing(ocr_art["ocr"], ocr_art["frames"])
    events += detect_navigation(segments, ocr_art["ocr"], ocr_art["frames"])
    events.sort(key=lambda e: e["t_start_ms"])

    # promote clicks on submit-labeled targets to form_submit
    blocks = ocr_art["ocr"]
    for e in events:
        if e["type"] in ("click", "tap") and e.get("pos"):
            label = nearest_text(blocks, e["pos"], e["t_start_ms"], roles=("button_like",))
            if label and SUBMIT_WORDS.match(label):
                e["type"] = "form_submit"
                e["detail"]["button"] = label

    anomalies = detect_anomalies(blocks, seg_art["samples"], events)

    with db_session() as db:
        for e in events:
            target = ""
            if e.get("pos"):
                target = nearest_text(blocks, e["pos"], e["t_start_ms"]) or ""
            db.add(InferredAction(
                job_id=job_id, t_start_ms=e["t_start_ms"], t_end_ms=e["t_end_ms"],
                action_type=e["type"], target_desc=target[:500],
                signals=e["signals"], detail={**e["detail"], "event_id": e["id"]},
                confidence=e["confidence"], source="deterministic",
            ))

    artifacts.save(org_id, job_id, "s06_events",
                   {"events": events, "anomalies": anomalies})


def nearest_text(blocks: list[dict], pos: list[int], t_ms: int,
                 roles: tuple = ("button_like", "body"), max_dist: float = 150.0,
                 time_window_ms: int = 3000) -> str | None:
    """Best readable OCR label near a position on a temporally-near keyframe —
    used to name click targets ('the Apply button') instead of raw coordinates.

    Candidates are scored, not just distance-ranked: a crisp button label a
    little further away beats OCR mush right under the cursor. Unreadable
    text is never returned — a wrong name is worse than no name."""
    best, best_score = None, 0.0
    for b in blocks:
        if abs(b["t_ms"] - t_ms) > time_window_ms or b["role"] not in roles:
            continue
        if b["confidence"] < 0.45:
            continue
        label = sanitize_label(b["text"])
        if not label:
            continue
        bx = b["bbox"][0] + b["bbox"][2] / 2
        by = b["bbox"][1] + b["bbox"][3] / 2
        d = ((bx - pos[0]) ** 2 + (by - pos[1]) ** 2) ** 0.5
        if d > max_dist:
            continue
        score = (1.0 - d / (max_dist + 1)) \
            + (0.3 if b["role"] == "button_like" else 0.0) \
            + 0.15 * label_quality(label) \
            - (0.1 if len(label) > 40 else 0.0)
        if score > best_score:
            best, best_score = label, score
    return best
