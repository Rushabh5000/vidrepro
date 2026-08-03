from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from vidrepro.api.deps import get_db
from vidrepro.contracts.report import ReportBody
from vidrepro.db.models import Comment, ProcessingJob, Report, ReportRevision

router = APIRouter()


def _get_report(db: Session, report_id: str) -> Report:
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(404, "report not found")
    return report


@router.get("/reports/{report_id}")
def get_report(report_id: str, db: Session = Depends(get_db)):
    report = _get_report(db, report_id)
    revision = db.get(ReportRevision, report.head_revision_id)
    job = db.get(ProcessingJob, report.job_id)
    return {
        "report_id": report.id,
        "job_id": report.job_id,
        "video_id": job.video_id if job else None,
        "status": report.status,
        "revision_id": revision.id if revision else None,
        "author_type": revision.author_type if revision else None,
        "body": revision.body if revision else None,
    }


@router.get("/reports/{report_id}/revisions")
def list_revisions(report_id: str, db: Session = Depends(get_db)):
    report = _get_report(db, report_id)
    revisions = db.execute(
        select(ReportRevision)
        .where(ReportRevision.report_id == report.id)
        .order_by(ReportRevision.created_at.desc())
    ).scalars().all()
    return [
        {"id": r.id, "author_type": r.author_type, "change_note": r.change_note,
         "created_at": str(r.created_at)} for r in revisions
    ]


class ReportUpdateIn(BaseModel):
    body: dict
    change_note: str = ""
    parent_revision_id: str


@router.put("/reports/{report_id}")
def update_report(report_id: str, payload: ReportUpdateIn, db: Session = Depends(get_db)):
    report = _get_report(db, report_id)
    if payload.parent_revision_id != report.head_revision_id:
        raise HTTPException(409, "report was updated by someone else — reload and re-apply edits")
    try:
        body = ReportBody.model_validate(payload.body)
    except Exception as e:
        raise HTTPException(422, f"invalid report body: {e}")
    revision = ReportRevision(
        report_id=report.id, parent_revision_id=report.head_revision_id,
        author_type="human", author_id="system",
        body=body.model_dump(), change_note=payload.change_note,
    )
    db.add(revision)
    db.flush()
    report.head_revision_id = revision.id
    report.status = "in_review"
    return {"revision_id": revision.id}


@router.post("/reports/{report_id}/approve")
def approve(report_id: str, db: Session = Depends(get_db)):
    report = _get_report(db, report_id)
    report.status = "approved"
    return {"ok": True}


class CommentIn(BaseModel):
    body: str
    step_key: str = ""
    t_ms: int = 0


@router.get("/reports/{report_id}/comments")
def list_comments(report_id: str, db: Session = Depends(get_db)):
    report = _get_report(db, report_id)
    comments = db.execute(
        select(Comment).where(Comment.report_id == report.id).order_by(Comment.created_at)
    ).scalars().all()
    return [
        {"id": c.id, "author_id": c.author_id, "step_key": c.step_key, "t_ms": c.t_ms,
         "body": c.body, "resolved": c.resolved, "created_at": str(c.created_at)} for c in comments
    ]


@router.post("/reports/{report_id}/comments", status_code=201)
def add_comment(report_id: str, body: CommentIn, db: Session = Depends(get_db)):
    report = _get_report(db, report_id)
    comment = Comment(report_id=report.id, author_id="system", step_key=body.step_key,
                      t_ms=body.t_ms, body=body.body)
    db.add(comment)
    db.flush()
    return {"id": comment.id}
