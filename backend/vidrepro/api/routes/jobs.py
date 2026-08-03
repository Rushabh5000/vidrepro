import json

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from vidrepro import storage
from vidrepro.api.deps import get_db
from vidrepro.config import get_settings
from vidrepro.db.models import (
    Frame,
    InferredAction,
    ProcessingJob,
    Report,
    Segment,
    Video,
)
from vidrepro.worker.pipeline import FIRST_STAGE, enqueue_stage

router = APIRouter()


def _get_job(db: Session, job_id: str) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


class JobIn(BaseModel):
    video_id: str


@router.post("/jobs", status_code=202)
def create_job(body: JobIn, db: Session = Depends(get_db)):
    video = db.get(Video, body.video_id)
    if not video:
        raise HTTPException(404, "video not found")
    if video.status not in ("uploaded", "processed", "failed"):
        raise HTTPException(409, f"video status is {video.status}")
    job = ProcessingJob(org_id=video.org_id, video_id=video.id, status="queued")
    db.add(job)
    video.status = "processing"
    db.flush()
    db.commit()  # job must be visible to the worker before we enqueue
    enqueue_stage(job.id, FIRST_STAGE)
    return {"job_id": job.id, "status": "queued"}


@router.get("/jobs")
def list_jobs(project_id: str, db: Session = Depends(get_db)):
    videos = db.execute(select(Video).where(Video.project_id == project_id)).scalars().all()
    out = []
    for v in videos:
        jobs = db.execute(
            select(ProcessingJob).where(ProcessingJob.video_id == v.id).order_by(ProcessingJob.created_at.desc())
        ).scalars().all()
        for j in jobs:
            report = db.execute(select(Report).where(Report.job_id == j.id)).scalar_one_or_none()
            out.append({
                "job_id": j.id, "video_id": v.id, "filename": v.original_filename,
                "status": j.status, "current_stage": j.current_stage,
                "report_id": report.id if report else None, "created_at": str(j.created_at),
            })
    return sorted(out, key=lambda x: x["created_at"], reverse=True)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = _get_job(db, job_id)
    report = db.execute(select(Report).where(Report.job_id == job.id)).scalar_one_or_none()
    return {
        "job_id": job.id, "video_id": job.video_id, "status": job.status,
        "current_stage": job.current_stage, "stage_progress": job.stage_progress,
        "stages": job.stages, "error": job.error,
        "report_id": report.id if report else None,
    }


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, db: Session = Depends(get_db)):
    job = _get_job(db, job_id)

    async def gen():
        yield {"event": "snapshot", "data": json.dumps({
            "status": job.status, "current_stage": job.current_stage, "stages": job.stages,
        })}
        r = aioredis.from_url(get_settings().redis_url)
        pubsub = r.pubsub()
        await pubsub.subscribe(f"progress:{job_id}")
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if msg is None:
                    yield {"event": "ping", "data": "{}"}
                    continue
                payload = json.loads(msg["data"])
                yield {"event": "progress", "data": json.dumps(payload)}
                if payload.get("status") in ("awaiting_review", "completed", "failed"):
                    break
        finally:
            await pubsub.unsubscribe(f"progress:{job_id}")
            await r.aclose()

    return EventSourceResponse(gen())


@router.get("/jobs/{job_id}/timeline")
def timeline(job_id: str, db: Session = Depends(get_db)):
    job = _get_job(db, job_id)
    video = db.get(Video, job.video_id)
    segments = db.execute(
        select(Segment).where(Segment.job_id == job.id).order_by(Segment.idx)
    ).scalars().all()
    actions = db.execute(
        select(InferredAction).where(InferredAction.job_id == job.id).order_by(InferredAction.t_start_ms)
    ).scalars().all()
    frames = db.execute(
        select(Frame).where(Frame.job_id == job.id, Frame.kind == "keyframe").order_by(Frame.t_ms)
    ).scalars().all()
    return {
        "duration_ms": int((video.duration_s or 0) * 1000),
        "segments": [
            {"idx": s.idx, "start_ms": s.start_ms, "end_ms": s.end_ms,
             "transition_type": s.transition_type, "label": s.label} for s in segments
        ],
        "actions": [
            {"id": a.id, "t_start_ms": a.t_start_ms, "t_end_ms": a.t_end_ms,
             "action_type": a.action_type, "target_desc": a.target_desc,
             "confidence": a.confidence, "signals": a.signals} for a in actions
        ],
        "frames": [
            {"id": f.id, "t_ms": f.t_ms, "url": storage.presigned_get(f.storage_key)} for f in frames
        ],
    }


@router.get("/videos/{video_id}/media")
def media(video_id: str, kind: str = "proxy", db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(404, "not found")
    keys = {
        "original": video.storage_key,
        "normalized": f"{video.org_id}/derived/{video.id}/normalized.mp4",
        "proxy": f"{video.org_id}/derived/{video.id}/proxy_360.mp4",
    }
    if kind not in keys:
        raise HTTPException(400, "kind must be original|normalized|proxy")
    key = keys[kind]
    if not storage.object_exists(key):
        key = video.storage_key  # derivative not ready yet — fall back to original
    return {"url": storage.presigned_get(key, expires=3600)}
