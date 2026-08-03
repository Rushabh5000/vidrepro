from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vidrepro import storage
from vidrepro.api.deps import get_db
from vidrepro.contracts.report import ReportBody
from vidrepro.db.models import Export, Organization, Report, ReportRevision
from vidrepro.exports.ado import create_ado_bug
from vidrepro.exports.github import github_issue_payload
from vidrepro.exports.markdown import render_markdown
from vidrepro.exports.text import render_text

router = APIRouter()


class ExportIn(BaseModel):
    format: str  # json | markdown | text | github | ado
    options: dict = {}


def _load(db: Session, report_id: str) -> tuple[Report, ReportRevision, ReportBody]:
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(404, "report not found")
    revision = db.get(ReportRevision, report.head_revision_id)
    if not revision:
        raise HTTPException(409, "report has no revision yet")
    return report, revision, ReportBody.model_validate(revision.body)


@router.post("/reports/{report_id}/exports", status_code=201)
def create_export(report_id: str, payload: ExportIn, db: Session = Depends(get_db)):
    report, revision, body = _load(db, report_id)
    export = Export(report_id=report.id, revision_id=revision.id,
                    format=payload.format, created_by="system")
    db.add(export)
    db.flush()  # assigns export.id (column default fires on INSERT, not construction)

    if payload.format == "json":
        key = f"{report.org_id}/exports/{report.id}/{export.id}.json"
        storage.put_json(key, body.model_dump())
        export.storage_key = key
        result = {"download_url": storage.presigned_get(key)}
    elif payload.format == "markdown":
        md = render_markdown(body)
        key = f"{report.org_id}/exports/{report.id}/{export.id}.md"
        storage.put_bytes(key, md.encode(), "text/markdown")
        export.storage_key = key
        result = {"download_url": storage.presigned_get(key), "content": md}
    elif payload.format == "text":
        txt = render_text(body)
        key = f"{report.org_id}/exports/{report.id}/{export.id}.txt"
        storage.put_bytes(key, txt.encode(), "text/plain")
        export.storage_key = key
        result = {"download_url": storage.presigned_get(key), "content": txt}
    elif payload.format == "github":
        result = {"issue": github_issue_payload(body)}
        key = f"{report.org_id}/exports/{report.id}/{export.id}.json"
        storage.put_json(key, result["issue"])
        export.storage_key = key
    elif payload.format == "ado":
        org_settings = (db.get(Organization, report.org_id).settings or {})
        item = create_ado_bug(body, org_settings, payload.options)
        export.external_url = item["url"]
        result = {"work_item_id": item["id"], "url": item["url"]}
    else:
        raise HTTPException(400, "format must be json|markdown|text|github|ado")

    db.flush()
    if report.status == "approved":
        report.status = "exported"
    return {"export_id": export.id, **result}


@router.get("/reports/{report_id}/exports/markdown", response_class=PlainTextResponse)
def preview_markdown(report_id: str, db: Session = Depends(get_db)):
    _, _, body = _load(db, report_id)
    return render_markdown(body)


@router.get("/reports/{report_id}/exports/text", response_class=PlainTextResponse)
def preview_text(report_id: str, db: Session = Depends(get_db)):
    _, _, body = _load(db, report_id)
    return render_text(body)
