"""s07: pixel-level click-target naming.

s04 only OCRs one or two keyframes per segment, so a click can land on a
control no keyframe ever captured — those clicks used to fall back to raw
coordinates. This stage goes back to the video for every click/tap that
still has no readable label: grab the frame at the click instant (before
the screen transitions), crop a window around the position, upscale, OCR
the crop, and pick the best readable line nearest the click point.

Output artifact: {"targets": {event_id: label}} — the authoritative label
map for s10. Labels resolved from s04 blocks are included so s10 reads one
source. InferredAction rows are updated in place for the timeline UI.
"""
from vidrepro.db.models import InferredAction, ProcessingJob, Video
from vidrepro.db.session import db_session
from vidrepro.worker import artifacts
from vidrepro.worker.stages.s06_events import nearest_text
from vidrepro.worker.textquality import label_quality, sanitize_label
from vidrepro.worker.vision import ocr, videoio

CROP_W, CROP_H = 460, 220
FRAME_BACKOFF_MS = 120  # read slightly before the activation instant
CLICKISH = ("click", "tap", "form_submit")


def crop_bounds(pos: list[int], frame_w: int, frame_h: int,
                crop_w: int = CROP_W, crop_h: int = CROP_H
                ) -> tuple[int, int, int, int]:
    """Crop window centred on pos, clamped to the frame. Returns x0,y0,x1,y1."""
    x0 = max(0, min(pos[0] - crop_w // 2, frame_w - crop_w))
    y0 = max(0, min(pos[1] - crop_h // 2, frame_h - crop_h))
    return x0, y0, min(x0 + crop_w, frame_w), min(y0 + crop_h, frame_h)


def pick_crop_line(lines, cx: float, cy: float) -> str:
    """Best readable OCR line in a crop, scored by distance to the click
    point and label quality. Same philosophy as nearest_text: no label
    beats a wrong label."""
    best, best_score = "", 0.0
    for ln in lines:
        if ln.confidence < 0.45:
            continue
        label = sanitize_label(ln.text)
        if not label:
            continue
        bx = ln.bbox[0] + ln.bbox[2] / 2
        by = ln.bbox[1] + ln.bbox[3] / 2
        d = ((bx - cx) ** 2 + (by - cy) ** 2) ** 0.5
        score = (1.0 - min(d, 300.0) / 301.0) \
            + 0.3 * label_quality(label) \
            - (0.1 if len(label) > 40 else 0.0)
        if score > best_score:
            best, best_score = label, score
    return best


def run(job_id: str) -> None:
    with db_session() as db:
        job = db.get(ProcessingJob, job_id)
        video = db.get(Video, job.video_id)
        org_id = video.org_id

    events = artifacts.load(org_id, job_id, "s06_events")["events"]
    ocr_art = artifacts.load(org_id, job_id, "s04_frames_ocr")
    norm_key = artifacts.load(org_id, job_id, "s02_normalize")["normalized_key"]
    blocks = ocr_art["ocr"]

    clickish = [e for e in events if e["type"] in CLICKISH and e.get("pos")]
    targets: dict[str, str] = {}
    unresolved: list[dict] = []
    for e in clickish:
        label = nearest_text(blocks, e["pos"], e["t_start_ms"]) or ""
        if label:
            targets[e["id"]] = label
        else:
            unresolved.append(e)

    if unresolved:
        local = videoio.fetch(norm_key)
        try:
            for e in unresolved:
                t = max(0, e["t_start_ms"] - FRAME_BACKOFF_MS)
                frame = videoio.frame_at(local.path, t)
                if frame is None:
                    continue
                fh, fw = frame.shape[:2]
                x0, y0, x1, y1 = crop_bounds(e["pos"], fw, fh)
                crop = frame[y0:y1, x0:x1]
                if crop.size == 0:
                    continue
                lines = ocr.extract_lines(crop)
                label = pick_crop_line(lines, e["pos"][0] - x0, e["pos"][1] - y0)
                if label:
                    targets[e["id"]] = label
        finally:
            local.cleanup()

    # timeline UI reads InferredAction rows — keep them in sync
    with db_session() as db:
        rows = db.query(InferredAction).filter_by(job_id=job_id).all()
        by_eid = {r.detail.get("event_id"): r for r in rows if r.detail}
        for eid, label in targets.items():
            row = by_eid.get(eid)
            if row is not None and label:
                row.target_desc = label[:500]

    artifacts.save(org_id, job_id, "s07_targets", {"targets": targets})
