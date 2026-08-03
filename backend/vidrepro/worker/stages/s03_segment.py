"""s03: scene/state segmentation on the normalized video.

Change score per sampled frame = 0.6·histogram-delta + 0.4·pixel-change-frac.
Hysteresis thresholding splits segments; transition type is classified from
how much of the frame changed AND how often changes of that magnitude occur
across the whole video. The second signal is what keeps live tickers, price
feeds, spinners, and playing videos from being read as user navigation:
ambient churn repeats all recording long, a real navigation is rare.
"""
import cv2
import numpy as np

from vidrepro.db.models import ProcessingJob, Segment, Video
from vidrepro.db.session import db_session
from vidrepro.worker import artifacts
from vidrepro.worker.vision import videoio

SAMPLE_FPS = 5.0
MIN_SEGMENT_MS = 700

NAV_CHANGE_FRAC = 0.55      # full-screen replacement
DIALOG_CHANGE_FRAC = 0.12   # localized overlay
NAV_AMBIENT_PER_MIN = 24.0  # full-screen churn above this = video playback, not nav
DIALOG_AMBIENT_PER_MIN = 8.0  # partial churn above this = ticker/feed, not dialog
MIN_AMBIENT_SAMPLES = 30    # churn needs volume: a burst of taps is not a ticker
AMBIENT_SPREAD_FRAC = 0.7   # ...and must recur across the video, not cluster


def _hist(gray: np.ndarray) -> np.ndarray:
    h = cv2.calcHist([gray], [0], None, [64], [0, 256])
    return cv2.normalize(h, h).flatten()


def compute_threshold(scores: np.ndarray) -> float:
    return max(0.08, float(scores.mean() + 2 * scores.std()))


def find_boundaries(samples: list[dict], threshold: float,
                    min_segment_ms: int = MIN_SEGMENT_MS) -> list[int]:
    boundaries = [0]
    for i, s in enumerate(samples):
        if s["score"] >= threshold \
                and s["t_ms"] - samples[boundaries[-1]]["t_ms"] >= min_segment_ms:
            boundaries.append(i)
    return boundaries


def ambient_rate_per_min(samples: list[dict], score: float, duration_ms: int) -> float:
    """How often changes of similar magnitude happen anywhere in the video."""
    if score <= 0 or duration_ms <= 0:
        return 0.0
    similar = sum(1 for s in samples if 0.6 * score <= s["score"] <= 1.6 * score)
    return similar / max(duration_ms / 60000.0, 1 / 60)


def _is_ambient(samples: list[dict], score: float, duration_ms: int,
                rate_cap: float) -> bool:
    """Ambient churn = changes of this magnitude recur often (rate), in
    volume (count), and across the whole timeline (spread). All three guards
    exist because each alone misfires on real recordings: rate explodes on
    short clips (4 taps / 0.27min = "15/min churn"), count is inflated when
    a burst of taps sees its own siblings in the band, and both together
    still can't tell a 10s tap flurry from a ticker — but a ticker runs the
    length of the video and a flurry doesn't."""
    if score <= 0 or duration_ms <= 0:
        return False
    times = [s["t_ms"] for s in samples
             if 0.6 * score <= s["score"] <= 1.6 * score]
    if len(times) < MIN_AMBIENT_SAMPLES:
        return False
    rate = len(times) / max(duration_ms / 60000.0, 1 / 60)
    spread = (max(times) - min(times)) / duration_ms
    return rate > rate_cap and spread >= AMBIENT_SPREAD_FRAC


def classify_transition(idx: int, change_frac: float, score: float,
                        samples: list[dict], duration_ms: int) -> str:
    if idx == 0:
        return "start"
    if change_frac >= NAV_CHANGE_FRAC:
        if _is_ambient(samples, score, duration_ms, NAV_AMBIENT_PER_MIN):
            return "minor"  # constant full-frame churn = embedded video/animation
        return "nav"
    if change_frac >= DIALOG_CHANGE_FRAC:
        if _is_ambient(samples, score, duration_ms, DIALOG_AMBIENT_PER_MIN):
            return "minor"  # repeating partial churn = ticker/feed refresh
        return "dialog"
    return "minor"


def build_segments(samples: list[dict], threshold: float,
                   sample_fps: float = SAMPLE_FPS) -> list[dict]:
    boundaries = find_boundaries(samples, threshold)
    end_ms = samples[-1]["t_ms"] + int(1000 / sample_fps)
    duration_ms = end_ms - samples[0]["t_ms"]

    segments = []
    for n, bi in enumerate(boundaries):
        start = samples[bi]["t_ms"]
        end = samples[boundaries[n + 1]]["t_ms"] if n + 1 < len(boundaries) else end_ms
        transition = classify_transition(
            n, samples[bi]["change_frac"], samples[bi]["score"], samples, duration_ms)
        segments.append({"idx": n, "start_ms": start, "end_ms": end,
                         "transition_type": transition})
    return segments


def run(job_id: str) -> None:
    with db_session() as db:
        job = db.get(ProcessingJob, job_id)
        video = db.get(Video, job.video_id)
        org_id = video.org_id
    art = artifacts.load(org_id, job_id, "s02_normalize")
    local = videoio.fetch(art["normalized_key"])
    try:
        samples: list[dict] = []
        prev_gray, prev_hist = None, None
        for t_ms, frame in videoio.iter_frames(local.path, SAMPLE_FPS, max_side=480):
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            hist = _hist(gray)
            if prev_gray is not None:
                hist_delta = 1.0 - float(cv2.compareHist(
                    prev_hist, hist, cv2.HISTCMP_CORREL))
                diff = cv2.absdiff(prev_gray, gray)
                change_frac = float(np.count_nonzero(diff > 25)) / diff.size
                score = 0.6 * max(0.0, hist_delta) + 0.4 * change_frac
            else:
                score, change_frac = 0.0, 0.0
            samples.append({"t_ms": t_ms, "score": round(score, 4),
                            "change_frac": round(change_frac, 4),
                            "lum": round(float(gray.mean()), 1)})
            prev_gray, prev_hist = gray, hist

        if not samples:
            raise RuntimeError("no frames decoded from normalized video")

        threshold = compute_threshold(np.array([s["score"] for s in samples]))
        segments = build_segments(samples, threshold)

        with db_session() as db:
            for seg in segments:
                db.add(Segment(job_id=job_id, idx=seg["idx"], start_ms=seg["start_ms"],
                               end_ms=seg["end_ms"], transition_type=seg["transition_type"]))

        artifacts.save(org_id, job_id, "s03_segment", {
            "segments": segments, "threshold": threshold,
            "samples": samples,  # kept for s05/s06 reuse and debugging
        })
    finally:
        local.cleanup()
