# VidRepro — Video → Bug Reproduction Steps

Upload a screen recording of a bug; get back an evidence-grounded, step-by-step
reproduction report you can review, edit, and push straight to Azure DevOps,
GitHub, Markdown, or JSON.

**The pipeline is fully deterministic** — FFmpeg + OpenCV + OCR + heuristic event
detection + rule-based step synthesis. No AI/LLM calls are required at runtime.
An optional LLM refinement plugin exists behind `VIDREPRO_LLM_PROVIDER` and is
**off by default** (`none`).

## Architecture

```
web (Next.js) ──► api (FastAPI) ──► Postgres (system of record)
                     │                 ▲
                     │ presigned URLs  │ job state, timeline, reports
                     ▼                 │
                  MinIO/S3 ◄─────── worker (Celery: ingest/vision/synthesis)
                     ▲                 │
                     └── Redis (broker + progress pub/sub)
```

Pipeline stages (each persists its output as an artifact JSON in S3, so any
stage can be re-run in isolation):

| Stage | What it does |
|---|---|
| s01_validate | ffprobe integrity/duration/codec checks, sha256, dedup key |
| s02_normalize | normalized MP4 (≤1080p CFR), 360p review proxy, blur/quality flags |
| s03_segment | scene/state segmentation via histogram + pixel-change scoring |
| s04_frames_ocr | keyframe extraction + OCR (Tesseract default, PaddleOCR optional) |
| s05_motion | global scroll motion (phase correlation) + cursor/touch blob tracking |
| s06_events | clicks, scrolls, typing, navigation, dialogs, error/blank anomalies |
| s09_bug | ranks anomaly candidates → bug manifestation point + observed result |
| s10_synthesis | rule-based numbered steps w/ OCR-named targets, compression |
| s11_validate_score | schema validation + per-step confidence scoring |
| s12_finalize | persist draft report revision, notify via SSE |

## Run locally

Prereqs: Docker + Docker Compose.

```bash
cp .env.example .env          # edit secrets if you like
docker compose up --build
```

- Web UI: http://localhost:3000
- API + OpenAPI docs: http://localhost:8000/docs
- MinIO console: http://localhost:9001 (minioadmin / minioadmin)

First run:
1. Open the web UI → Register (first registered user gets an org + project).
2. Upload a screen recording (mp4/mov/webm/mkv/avi, ≤2 GB, ≤45 min).
3. Watch the live stage progress; when the draft is ready, open the Review
   Studio, edit steps, approve, and export.

### Azure DevOps export

Set in `.env` (or per-org in Settings → org settings JSON):

```
VIDREPRO_ADO_ORG=your-ado-org
VIDREPRO_ADO_PROJECT=YourProject
VIDREPRO_ADO_PAT=<PAT with Work Items read/write>
```

"Export → Azure DevOps" creates a `Bug` work item with repro steps rendered
into `Microsoft.VSTS.TCM.ReproSteps` (HTML) and returns the work item URL.

## Dev without Docker

```bash
# backend (Python 3.12+; needs ffmpeg + tesseract-ocr on PATH)
cd backend && pip install -e .[dev]
uvicorn vidrepro.api.main:app --reload            # API
celery -A vidrepro.worker.celery_app worker -Q q.ingest,q.vision,q.reason,q.export -l info

# frontend
cd web && npm install && npm run dev
```

Tests: `cd backend && pytest`

## Repo layout

```
backend/vidrepro/
  api/         FastAPI app (auth, uploads, jobs, reports, exports, SSE)
  worker/      Celery app + pipeline stages + vision/synthesis modules
  db/          SQLAlchemy models + bootstrap
  contracts/   Pydantic contracts (ReportBody, events, stage IO)
  exports/     markdown / github / Azure DevOps renderers
  security/    JWT auth, API keys, RBAC
web/           Next.js app (dashboard, upload, job status, review studio)
infra/docker/  Dockerfiles
```

## Notes / current limitations

- DB schema is created automatically on API start (`VIDREPRO_DB_AUTO_CREATE=1`);
  switch to Alembic migrations before multi-node production.
- SSE and media URLs accept `?token=` because `EventSource`/`<video>` cannot
  set headers; tokens are short-lived JWTs.
- Click/tap detection is inference from visual evidence (no input telemetry);
  every step carries grounding (`observed|inferred|assumed`) and confidence.
