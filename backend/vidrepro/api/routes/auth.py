from fastapi import APIRouter

from vidrepro.config import get_settings

router = APIRouter()


@router.get("/me")
def me():
    settings = get_settings()
    return {
        "orgs": [{"org_id": settings.default_org_id, "org_name": "Default", "role": "owner"}]
    }
