"""s12: persist the draft report revision and hand off to human review."""
from vidrepro.db.models import ProcessingJob, Report, ReportRevision, Video
from vidrepro.db.session import db_session
from vidrepro.worker import artifacts, progress


def run(job_id: str) -> None:
    with db_session() as db:
        job = db.get(ProcessingJob, job_id)
        video = db.get(Video, job.video_id)
        org_id, project_id = video.org_id, video.project_id

    body = artifacts.load(org_id, job_id, "s11_validate_score")

    with db_session() as db:
        report = Report(org_id=org_id, project_id=project_id, job_id=job_id, status="draft")
        db.add(report)
        db.flush()
        revision = ReportRevision(
            report_id=report.id, author_type="ai",
            body=body, change_note="initial automated draft",
        )
        db.add(revision)
        db.flush()
        report.head_revision_id = revision.id
        report_id = report.id

        job = db.get(ProcessingJob, job_id)
        job.status = "awaiting_review"
        job.current_stage = "done"
        job.stage_progress = 1.0
        video = db.get(Video, job.video_id)
        video.status = "processed"

    progress.publish(job_id, status="awaiting_review", stage="done", pct=1.0,
                     report_id=report_id, message="draft report ready for review")
