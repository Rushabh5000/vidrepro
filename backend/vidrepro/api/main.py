import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from vidrepro.api.routes import auth, exports, jobs, projects, reports, uploads
from vidrepro.config import get_settings
from vidrepro.db.bootstrap import bootstrap_db
from vidrepro.db.models import Organization, Project
from vidrepro.db.session import db_session
from vidrepro.logging_setup import setup_logging

log = logging.getLogger(__name__)

REQUESTS = Counter("vidrepro_api_requests_total", "API requests", ["method", "route", "status"])
LATENCY = Histogram("vidrepro_api_request_seconds", "API latency", ["route"])


def _ensure_default_org() -> None:
    settings = get_settings()
    with db_session() as db:
        org = db.get(Organization, settings.default_org_id)
        if not org:
            org = Organization(id=settings.default_org_id, name="Default")
            db.add(org)
            db.flush()
            db.add(Project(org_id=org.id, name="Default Project"))
            log.info("created default org %s", settings.default_org_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    bootstrap_db()
    _ensure_default_org()
    yield


app = FastAPI(title="VidRepro API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def observe(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())[:8]
    start = time.monotonic()
    response: Response = await call_next(request)
    route = request.scope.get("route")
    path = route.path if route else request.url.path
    REQUESTS.labels(request.method, path, response.status_code).inc()
    LATENCY.labels(path).observe(time.monotonic() - start)
    response.headers["x-request-id"] = request.state.request_id
    return response


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(auth.router, prefix="/v1/auth", tags=["auth"])
app.include_router(projects.router, prefix="/v1", tags=["projects"])
app.include_router(uploads.router, prefix="/v1", tags=["uploads"])
app.include_router(jobs.router, prefix="/v1", tags=["jobs"])
app.include_router(reports.router, prefix="/v1", tags=["reports"])
app.include_router(exports.router, prefix="/v1", tags=["exports"])
