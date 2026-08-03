"""Pipeline definition: stage order and queue routing.

Every stage is a pure function `stage(job_id) -> None` that reads persisted
inputs (DB rows + artifacts) and persists outputs. The generic `run_stage`
task advances the chain. Stage s08 from the design is folded into s10 in
this deterministic build; the optional LLM refiner (off by default) hooks
into s10.
"""
from vidrepro.worker.celery_app import app

# (stage name, queue) in execution order
STAGES: list[tuple[str, str]] = [
    ("s01_validate", "q.ingest"),
    ("s02_normalize", "q.ingest"),
    ("s03_segment", "q.vision"),
    ("s04_frames_ocr", "q.vision"),
    ("s05_motion", "q.vision"),
    ("s06_events", "q.vision"),
    ("s07_targets", "q.vision"),
    ("s09_bug", "q.reason"),
    ("s10_synthesis", "q.reason"),
    ("s11_validate_score", "q.reason"),
    ("s12_finalize", "q.export"),
]

FIRST_STAGE = STAGES[0][0]
STAGE_QUEUE = dict(STAGES)
STAGE_ORDER = [name for name, _ in STAGES]


def next_stage(stage: str) -> str | None:
    idx = STAGE_ORDER.index(stage)
    return STAGE_ORDER[idx + 1] if idx + 1 < len(STAGE_ORDER) else None


def enqueue_stage(job_id: str, stage: str) -> None:
    app.send_task("pipeline.run_stage", args=[job_id, stage], queue=STAGE_QUEUE[stage])
