FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# ffmpeg for transcode/probe, tesseract for OCR, libgl for opencv
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg tesseract-ocr libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY backend/pyproject.toml backend/pyproject.toml
RUN pip install -e ./backend[worker,dev] || true
COPY backend backend
RUN pip install -e ./backend[worker,dev]
EXPOSE 9100
CMD ["celery", "-A", "vidrepro.worker.celery_app", "worker", \
     "-Q", "q.ingest,q.vision,q.reason,q.export", "-l", "info", "--concurrency", "2"]
