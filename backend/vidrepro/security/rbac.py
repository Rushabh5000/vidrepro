"""Role-based access control. Single policy module — no inline role checks
anywhere else in the codebase."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from vidrepro.db.models import Membership

ROLE_ORDER = {"viewer": 0, "reviewer": 1, "editor": 2, "admin": 3, "owner": 4}


def get_role(db: Session, org_id: str, user_id: str) -> str | None:
    row = db.execute(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
    ).scalar_one_or_none()
    return row.role if row else None


def require_role(db: Session, org_id: str, user_id: str, minimum: str) -> str:
    role = get_role(db, org_id, user_id)
    if role is None:
        raise HTTPException(status_code=404, detail="not found")  # don't leak org existence
    if ROLE_ORDER[role] < ROLE_ORDER[minimum]:
        raise HTTPException(status_code=403, detail=f"requires {minimum} role")
    return role
