from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from vidrepro.api.deps import get_db
from vidrepro.db.models import Organization, Project

router = APIRouter()


@router.get("/orgs/{org_id}/projects")
def list_projects(org_id: str, db: Session = Depends(get_db)):
    projects = db.execute(select(Project).where(Project.org_id == org_id)).scalars().all()
    return [{"id": p.id, "name": p.name, "created_at": p.created_at} for p in projects]


class ProjectIn(BaseModel):
    name: str


@router.post("/orgs/{org_id}/projects", status_code=201)
def create_project(org_id: str, body: ProjectIn, db: Session = Depends(get_db)):
    project = Project(org_id=org_id, name=body.name)
    db.add(project)
    db.flush()
    return {"id": project.id, "name": project.name}


class OrgSettingsIn(BaseModel):
    settings: dict


@router.put("/orgs/{org_id}/settings")
def update_org_settings(org_id: str, body: OrgSettingsIn, db: Session = Depends(get_db)):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(404, "not found")
    org.settings = {**(org.settings or {}), **body.settings}
    return {"ok": True, "settings": org.settings}
