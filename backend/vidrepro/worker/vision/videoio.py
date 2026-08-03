"""FFmpeg/ffprobe + OpenCV video access helpers."""
import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Iterator

import cv2
import numpy as np

from vidrepro import storage


class FfmpegError(RuntimeError):
    pass


def run_cmd(cmd: list[str], timeout: int = 1800) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise FfmpegError(f"{cmd[0]} failed ({proc.returncode}): {proc.stderr[-800:]}")
    return proc.stdout


def ffprobe(path: str) -> dict:
    out = run_cmd([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ], timeout=120)
    return json.loads(out)


def duration_by_packets(path: str) -> float:
    """Duration from the last video packet timestamp. WebM written by
    MediaRecorder (browser screen capture) carries NO duration metadata at
    all — the only truth is in the packet stream. Header-only scan, no
    decode, so it stays fast even on multi-hundred-MB files."""
    out = run_cmd([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,dts_time", "-of", "json", path,
    ], timeout=600)
    best = 0.0
    for pkt in json.loads(out).get("packets", []):
        for key in ("pts_time", "dts_time"):
            try:
                best = max(best, float(pkt.get(key)))
            except (TypeError, ValueError):
                continue
    return best


@dataclass
class LocalVideo:
    path: str
    tmpdir: tempfile.TemporaryDirectory

    def cleanup(self):
        self.tmpdir.cleanup()


def fetch(storage_key: str) -> LocalVideo:
    tmpdir = tempfile.TemporaryDirectory(prefix="vidrepro_")
    ext = os.path.splitext(storage_key)[1] or ".mp4"
    path = os.path.join(tmpdir.name, f"video{ext}")
    storage.download_file(storage_key, path)
    return LocalVideo(path=path, tmpdir=tmpdir)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_frames(path: str, sample_fps: float = 5.0, max_side: int = 0
                ) -> Iterator[tuple[int, np.ndarray]]:
    """Yield (t_ms, BGR frame) sampled at ~sample_fps. max_side>0 downscales."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise FfmpegError(f"OpenCV cannot open {path}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(src_fps / sample_fps))
    idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step == 0:
                t_ms = int(idx / src_fps * 1000)
                if max_side and max(frame.shape[:2]) > max_side:
                    scale = max_side / max(frame.shape[:2])
                    frame = cv2.resize(frame, None, fx=scale, fy=scale,
                                       interpolation=cv2.INTER_AREA)
                yield t_ms, frame
            idx += 1
    finally:
        cap.release()


def frame_at(path: str, t_ms: int) -> np.ndarray | None:
    cap = cv2.VideoCapture(path)
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, float(t_ms))
        ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()


def blur_score(gray: np.ndarray) -> float:
    """Variance of Laplacian; < ~50 on UI content suggests heavy blur."""
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())
