"""s05: global motion series (scroll/swipe) + cursor/touch small-mover track."""
import cv2

from vidrepro.db.models import ProcessingJob, Video
from vidrepro.db.session import db_session
from vidrepro.worker import artifacts
from vidrepro.worker.vision import motion, videoio

SAMPLE_FPS = 8.0


def run(job_id: str) -> None:
    with db_session() as db:
        job = db.get(ProcessingJob, job_id)
        video = db.get(Video, job.video_id)
        org_id = video.org_id
        src_w = video.width or 1
    norm_key = artifacts.load(org_id, job_id, "s02_normalize")["normalized_key"]

    local = videoio.fetch(norm_key)
    motions: list[dict] = []
    cursor_track: list[dict] = []
    try:
        prev_gray = None
        analysis_w = None
        for t_ms, frame in videoio.iter_frames(local.path, SAMPLE_FPS, max_side=480):
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if analysis_w is None:
                analysis_w = gray.shape[1]
            if prev_gray is not None and prev_gray.shape == gray.shape:
                dx, dy = motion.global_shift(prev_gray, gray)
                if abs(dx) > 1.5 or abs(dy) > 1.5:
                    motions.append({"t_ms": t_ms, "dx": round(dx, 2), "dy": round(dy, 2)})
                hit = motion.small_mover(prev_gray, gray, t_ms)
                if hit:
                    cursor_track.append({"t_ms": hit.t_ms, "x": hit.x, "y": hit.y,
                                         "conf": hit.conf})
            prev_gray = gray

        # scale factor to map analysis coords back to normalized-video coords
        scale = 1.0
        if analysis_w:
            probe = videoio.ffprobe(local.path)
            vs = next(s for s in probe["streams"] if s["codec_type"] == "video")
            scale = int(vs["width"]) / analysis_w

        cursor_found = len(cursor_track) >= 3
        with db_session() as db:
            video = db.get(Video, db.get(ProcessingJob, job_id).video_id)
            if not cursor_found:
                flags = list(video.quality_flags or [])
                if "cursor_invisible" not in flags:
                    video.quality_flags = flags + ["cursor_invisible"]

        artifacts.save(org_id, job_id, "s05_motion", {
            "motions": motions,
            "cursor_track": cursor_track,
            "coord_scale": round(scale, 3),
            "cursor_found": cursor_found,
        })
    finally:
        local.cleanup()
