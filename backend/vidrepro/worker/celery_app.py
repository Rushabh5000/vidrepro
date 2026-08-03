"""Celery application. The API imports this module only to send tasks — stage
modules (heavy: cv2, OCR) are loaded exclusively by worker processes via the
`imports` setting."""
from celery import Celery
from celery.signals import worker_process_init

from vidrepro.config import get_settings

app = Celery(
    "vidrepro",
    broker=get_settings().redis_url,
    backend=get_settings().redis_url,
)

app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="q.ingest",
    task_time_limit=60 * 60,
    task_soft_time_limit=55 * 60,
    imports=["vidrepro.worker.tasks"],
    broker_connection_retry_on_startup=True,
)


@worker_process_init.connect
def _init_worker(**_):
    from vidrepro.logging_setup import setup_logging
    setup_logging()
    # Prometheus metrics endpoint for the worker fleet (one port per pool).
    try:
        from prometheus_client import start_http_server
        start_http_server(9100)
    except OSError:
        pass  # port already bound by a sibling pool process
