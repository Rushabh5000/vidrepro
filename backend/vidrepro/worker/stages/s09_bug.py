"""s09: bug manifestation localization — deterministic ranking of anomaly
candidates from s06. Never fabricates a bug: with no candidates the job
completes with an explicit no_bug_found outcome for the reviewer."""
from vidrepro.db.models import ProcessingJob, Video
from vidrepro.db.session import db_session
from vidrepro.worker import artifacts

BUG_TYPE_BY_ANOMALY = {
    "error_text": "functional",
    "blank_screen": "crash",
    "crash_dialog": "crash",
    "stall": "performance",
    "app_exit": "crash",
    "layout_shift": "rendering",
}


def observed_text(anomaly: dict) -> str:
    if anomaly["type"] == "error_text":
        return f"The UI displays the error text: \"{anomaly['detail'].get('text', '')}\""
    if anomaly["type"] == "blank_screen":
        return "The screen goes blank (near-black frame), suggesting a crash or render failure."
    if anomaly["type"] == "stall":
        ms = anomaly["detail"].get("static_ms", 0)
        return (f"The screen remains completely unchanged for {ms / 1000:.1f}s after the "
                f"last user action, suggesting a freeze or unresponsive state.")
    return "An unexpected UI state is visible."


def run(job_id: str) -> None:
    with db_session() as db:
        job = db.get(ProcessingJob, job_id)
        video = db.get(Video, job.video_id)
        org_id = video.org_id

    anomalies = artifacts.load(org_id, job_id, "s06_events")["anomalies"]

    if not anomalies:
        artifacts.save(org_id, job_id, "s09_bug", {
            "found": False,
            "bug_type": "unknown",
            "t_ms": None,
            "observed": ("No clear bug manifestation was detected automatically. "
                         "Review the timeline and mark the failure point manually."),
            "confidence": 0.0,
            "candidates": [],
        })
        return

    ranked = sorted(anomalies, key=lambda a: (a["score"], a["t_ms"]), reverse=True)
    primary = ranked[0]
    artifacts.save(org_id, job_id, "s09_bug", {
        "found": True,
        "bug_type": BUG_TYPE_BY_ANOMALY.get(primary["type"], "unknown"),
        "t_ms": primary["t_ms"],
        "anomaly": primary,
        "observed": observed_text(primary),
        "confidence": round(primary["score"], 2),
        "candidates": ranked[1:6],  # surfaced as alternate interpretations
    })
