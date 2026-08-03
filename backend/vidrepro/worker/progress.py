import json
from functools import lru_cache

import redis

from vidrepro.config import get_settings


@lru_cache
def _redis() -> redis.Redis:
    return redis.from_url(get_settings().redis_url)


def publish(job_id: str, **payload) -> None:
    _redis().publish(f"progress:{job_id}", json.dumps(payload, default=str))
