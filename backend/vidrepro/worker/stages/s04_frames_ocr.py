"""s04: keyframe extraction per segment + OCR.

Keyframes: first stable frame after the transition settles, plus the last
frame of long segments. OCR runs on full-resolution keyframes; results are
persisted both as DB rows (for search/timeline) and in the artifact (for
synthesis stages).
"""
import cv2

from vidrepro import storage
from vidrepro.db.models import Frame, OcrSnippet, ProcessingJob, Video
from vidrepro.db.session import db_session
from vidrepro.worker import artifacts
from vidrepro.worker.vision import ocr, videoio

SETTLE_OFFSET_MS = 350   # skip transition animation
LONG_SEGMENT_MS = 4000   # long segments also get an end keyframe


def run(job_id: str) -> None:
    with db_session() as db:
        job = db.get(ProcessingJob, job_id)
        video = db.get(Video, job.video_id)
        org_id, video_id = video.org_id, video.id
    seg_art = artifacts.load(org_id, job_id, "s03_segment")
    norm_key = artifacts.load(org_id, job_id, "s02_normalize")["normalized_key"]

    local = videoio.fetch(norm_key)
    frames_out: list[dict] = []
    ocr_out: list[dict] = []
    try:
        for seg in seg_art["segments"]:
            times = [min(seg["start_ms"] + SETTLE_OFFSET_MS, seg["end_ms"] - 1)]
            if seg["end_ms"] - seg["start_ms"] > LONG_SEGMENT_MS:
                times.append(seg["end_ms"] - 500)
            for t_ms in times:
                frame = videoio.frame_at(local.path, max(0, t_ms))
                if frame is None:
                    continue
                ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if not ok:
                    continue
                with db_session() as db:
                    row = Frame(job_id=job_id, segment_idx=seg["idx"], t_ms=t_ms,
                                storage_key="", kind="keyframe")
                    db.add(row)
                    db.flush()
                    frame_id = row.id
                    key = f"{org_id}/frames/{video_id}/{frame_id}.jpg"
                    row.storage_key = key
                storage.put_bytes(key, jpg.tobytes(), "image/jpeg")

                lines = ocr.extract_lines(frame)
                with db_session() as db:
                    for line in lines:
                        db.add(OcrSnippet(
                            job_id=job_id, frame_id=frame_id, t_ms=t_ms,
                            text=line.text[:2000], bbox=list(line.bbox),
                            confidence=line.confidence, role=line.role,
                        ))
                frames_out.append({"frame_id": frame_id, "t_ms": t_ms,
                                   "segment_idx": seg["idx"], "storage_key": key})
                ocr_out.extend([
                    {"frame_id": frame_id, "t_ms": t_ms, "text": line.text,
                     "bbox": list(line.bbox), "confidence": line.confidence,
                     "role": line.role}
                    for line in lines
                ])

        artifacts.save(org_id, job_id, "s04_frames_ocr",
                       {"frames": frames_out, "ocr": ocr_out})
    finally:
        local.cleanup()
