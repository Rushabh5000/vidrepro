"""Create tables on startup for dev/single-node deployments.

Production multi-node should switch to Alembic migrations; models are the
single source of truth either way.
"""
import logging

from vidrepro.config import get_settings
from vidrepro.db.models import Base
from vidrepro.db.session import get_engine

log = logging.getLogger(__name__)


def bootstrap_db() -> None:
    if not get_settings().db_auto_create:
        return
    Base.metadata.create_all(get_engine())
    log.info("database schema ensured")
