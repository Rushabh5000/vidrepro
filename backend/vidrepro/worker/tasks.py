"""Generic stage runner. Loaded only inside worker processes."""
import logging
import time
import traceback

from prometheus_client import Counter, Histogram

from vidrepro.db.models import ProcessingJob, Video
from vidrepro.db.session import db_session
from vidrepro.worker import progress
from vidrepro.worker.celery_app import app
from vidrepro.worker.pipeline import STAGE_ORDER, enqueue_stage, next_stage

log = logging.getLogger(__name__)

STAGE_DURATION = Histogram("vidrepro_stage_seconds", "Stage duration", ["stage"])
STAGE_FAILURES = Counter("vidrepro_stage_failures_total", "Stage failures", ["stage"])

MAX_ATTEMPTS = 3


def _stage_fn(stage: str):
    from vidrepro.worker.stages import (  # imported lazily: heavy deps
        s01_validate, s02_normalize, s03_segment, s04_frames_ocr,
        s05_motion, s06_events, s07_targets, s09_bug, s10_synthesis,
        s11_validate_score, s12_finalize,
    )
    return {
        "s01_validate": s01_validate.run,
        "s02_normalize": s02_normalize.run,
        "s03_segment": s03_segment.run,
        "s04_frames_ocr": s04_frames_ocr.run,
        "s05_motion": s05_motion.run,
        "s06_events": s06_events.run,
        "s07_targets": s07_targets.run,
        "s09_bug": s09_bug.run,
        "s10_synthesis": s10_synthesis.run,
        "s11_validate_score": s11_validate_score.run,
        "s12_finalize": s12_finalize.run,
    }[stage]


class StageFailed(Exception):
    """Terminal, user-facing failure — do not retry."""


@app.task(name="pipeline.run_stage", bind=True, max_retries=MAX_ATTEMPTS - 1,
          retry_backoff=True, retry_backoff_max=300)
def run_stage(self, job_id: str, stage: str):
    stage_pct = (STAGE_ORDER.index(stage)) / len(STAGE_ORDER)
    with db_session() as db:
        job = db.get(ProcessingJob, job_id)
        if job is None or job.status in ("failed", "canceled"):
            return
        job.status = "running"
        job.current_stage = stage
        job.stage_progress = stage_pct
    progress.publish(job_id, status="running", stage=stage, pct=round(stage_pct, 3),
                     message=f"running {stage}")

    start = time.monotonic()
    try:
        _stage_fn(stage)(job_id)
        elapsed = time.monotonic() - start
        STAGE_DURATION.labels(stage).observe(elapsed)
        with db_session() as db:
            job = db.get(ProcessingJob, job_id)
            job.stages = (job.stages or []) + [
                {"stage": stage, "status": "done", "ms": int(elapsed * 1000)}
            ]
        log.info("stage done", extra={"job_id": job_id, "stage": stage})
        nxt = next_stage(stage)
        if nxt:
            enqueue_stage(job_id, nxt)
    except StageFailed as e:
        _fail(job_id, stage, str(e))
    except Exception as e:
        STAGE_FAILURES.labels(stage).inc()
        log.error("stage error: %s", e, extra={"job_id": job_id, "stage": stage})
        if self.request.retries + 1 >= MAX_ATTEMPTS:
            _fail(job_id, stage, f"{stage} failed after {MAX_ATTEMPTS} attempts: {e}\n"
                                 f"{traceback.format_exc()[-1500:]}")
        else:
            raise self.retry(exc=e)


def _fail(job_id: str, stage: str, error: str) -> None:
    with db_session() as db:
        job = db.get(ProcessingJob, job_id)
        job.status = "failed"
        job.error = error[:4000]
        job.stages = (job.stages or []) + [{"stage": stage, "status": "failed", "error": error[:500]}]
        video = db.get(Video, job.video_id)
        if video:
            video.status = "failed"
    progress.publish(job_id, status="failed", stage=stage, message=error[:300])
