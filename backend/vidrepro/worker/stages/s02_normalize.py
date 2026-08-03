"""s02: FFmpeg derivatives — normalized ≤1080p CFR MP4, 360p review proxy,
audio WAV if present — plus blur/quality scoring."""
import os

import cv2

from vidrepro import storage
from vidrepro.db.models import ProcessingJob, Video
from vidrepro.db.session import db_session
from vidrepro.worker import artifacts
from vidrepro.worker.vision import videoio


def run(job_id: str) -> None:
    with db_session() as db:
        job = db.get(ProcessingJob, job_id)
        video = db.get(Video, job.video_id)
        org_id, video_id = video.org_id, video.id
        storage_key, has_audio = video.storage_key, video.has_audio

    local = videoio.fetch(storage_key)
    tmp = local.tmpdir.name
    try:
        norm = os.path.join(tmp, "normalized.mp4")
        proxy = os.path.join(tmp, "proxy_360.mp4")
        # keep aspect, cap the longest side at 1080, force even dims for h264
        scale = ("scale=w='if(gte(iw,ih),min(1080,iw),-2)':"
                 "h='if(lt(iw,ih),min(1080,ih),-2)':force_divisible_by=2")
        videoio.run_cmd([
            "ffmpeg", "-y", "-i", local.path,
            "-vf", scale,
            "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-an", norm,
        ])
        videoio.run_cmd([
            "ffmpeg", "-y", "-i", norm,
            "-vf", "scale=-2:360", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "28", "-movflags", "+faststart", "-an", proxy,
        ])
        norm_key = f"{org_id}/derived/{video_id}/normalized.mp4"
        proxy_key = f"{org_id}/derived/{video_id}/proxy_360.mp4"
        storage.put_file(norm_key, norm, "video/mp4")
        storage.put_file(proxy_key, proxy, "video/mp4")

        audio_key = ""
        if has_audio:
            wav = os.path.join(tmp, "audio.wav")
            try:
                videoio.run_cmd(["ffmpeg", "-y", "-i", local.path, "-vn",
                                 "-ar", "16000", "-ac", "1", wav])
                audio_key = f"{org_id}/derived/{video_id}/audio.wav"
                storage.put_file(audio_key, wav, "audio/wav")
            except videoio.FfmpegError:
                pass  # audio extraction is best-effort

        # blur/quality scoring on 10 sampled frames of the normalized video
        blur_scores = []
        for i, (t_ms, frame) in enumerate(videoio.iter_frames(norm, sample_fps=0.5, max_side=720)):
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            blur_scores.append(videoio.blur_score(gray))
            if i >= 9:
                break
        median_blur = sorted(blur_scores)[len(blur_scores) // 2] if blur_scores else 0.0

        with db_session() as db:
            video = db.get(Video, video_id)
            flags = list(video.quality_flags or [])
            if median_blur and median_blur < 40:
                flags.append("blurry")
            video.quality_flags = sorted(set(flags))

        artifacts.save(org_id, job_id, "s02_normalize", {
            "normalized_key": norm_key, "proxy_key": proxy_key, "audio_key": audio_key,
            "median_blur": median_blur,
        })
    finally:
        local.cleanup()
