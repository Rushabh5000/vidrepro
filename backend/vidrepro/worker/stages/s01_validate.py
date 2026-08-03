"""s01: integrity + policy validation via ffprobe; sha256 for dedup."""
from vidrepro.config import get_settings
from vidrepro.db.models import ProcessingJob, Video
from vidrepro.db.session import db_session
from vidrepro.worker import artifacts
from vidrepro.worker.vision import videoio


def run(job_id: str) -> None:
    from vidrepro.worker.tasks import StageFailed

    with db_session() as db:
        job = db.get(ProcessingJob, job_id)
        video = db.get(Video, job.video_id)
        org_id, storage_key = video.org_id, video.storage_key

    local = videoio.fetch(storage_key)
    try:
        probe = videoio.ffprobe(local.path)
        fmt = probe.get("format", {})
        vstreams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
        astreams = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
        if not vstreams:
            raise StageFailed("file contains no video stream")
        vs = vstreams[0]
        duration = float(fmt.get("duration") or vs.get("duration") or 0)
        if duration <= 0.5:
            # MediaRecorder WebM has no duration metadata — read the packets
            duration = videoio.duration_by_packets(local.path)
        settings = get_settings()
        if duration <= 0.5:
            raise StageFailed("video is empty or unreadably short")
        if duration > settings.max_duration_s:
            raise StageFailed(f"video is {duration:.0f}s; max is {settings.max_duration_s}s")

        num, _, den = (vs.get("avg_frame_rate") or "0/1").partition("/")
        fps = (float(num) / float(den)) if float(den or 1) else 0.0
        width, height = int(vs.get("width", 0)), int(vs.get("height", 0))
        # portrait aspect strongly suggests a mobile screen recording
        device_class = "mobile" if height > width * 1.2 else "desktop"
        sha = videoio.sha256_file(local.path)

        with db_session() as db:
            video = db.get(Video, db.get(ProcessingJob, job_id).video_id)
            video.duration_s = duration
            video.container = fmt.get("format_name", "")[:40]
            video.codec = vs.get("codec_name", "")[:40]
            video.width, video.height, video.fps = width, height, round(fps, 2)
            video.has_audio = bool(astreams)
            video.device_class = device_class
            video.sha256 = sha
            flags = list(video.quality_flags or [])
            if fps and fps < 12:
                flags.append("low_fps")
            video.quality_flags = flags

        artifacts.save(org_id, job_id, "s01_validate", {
            "duration_s": duration, "fps": fps, "width": width, "height": height,
            "device_class": device_class, "has_audio": bool(astreams), "sha256": sha,
        })
    finally:
        local.cleanup()
