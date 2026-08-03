from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vidrepro import storage
from vidrepro.api.deps import get_db
from vidrepro.config import get_settings
from vidrepro.db.models import Project, Video

router = APIRouter()

ACCEPTED = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
    "video/x-matroska": ".mkv",
    "video/webm": ".webm",
}


class UploadIn(BaseModel):
    filename: str
    content_type: str
    byte_size: int


class UploadOut(BaseModel):
    video_id: str
    put_url: str


@router.post("/projects/{project_id}/uploads", response_model=UploadOut, status_code=201)
def create_upload(project_id: str, body: UploadIn, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    settings = get_settings()
    if body.byte_size > settings.max_upload_bytes:
        raise HTTPException(413, f"file exceeds {settings.max_upload_bytes} bytes")
    if body.content_type not in ACCEPTED:
        raise HTTPException(415, f"unsupported type; accepted: {', '.join(ACCEPTED)}")
    video = Video(
        org_id=project.org_id,
        project_id=project.id,
        uploader_id="system",
        original_filename=body.filename[:500],
        byte_size=body.byte_size,
        storage_key="",
        status="uploading",
    )
    db.add(video)
    db.flush()
    video.storage_key = f"{project.org_id}/raw/{video.id}/original{ACCEPTED[body.content_type]}"
    put_url = storage.presigned_put(video.storage_key, body.content_type)
    return UploadOut(video_id=video.id, put_url=put_url)


@router.post("/videos/{video_id}/complete")
def complete_upload(video_id: str, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(404, "video not found")
    if not storage.object_exists(video.storage_key):
        raise HTTPException(409, "object not found in storage — upload did not complete")
    video.byte_size = storage.object_size(video.storage_key)
    video.status = "uploaded"
    return {"ok": True, "video_id": video.id, "byte_size": video.byte_size}
