FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY backend/pyproject.toml backend/pyproject.toml
RUN pip install -e ./backend[api] || true
COPY backend backend
RUN pip install -e ./backend[api]
EXPOSE 8000
CMD ["uvicorn", "vidrepro.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
