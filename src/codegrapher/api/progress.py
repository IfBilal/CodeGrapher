"""Live ingestion progress, backed by a Redis list per job (not pure
pub/sub): an SSE client can connect before, during, or after a job runs and
still see the full event history, since it reads from a persisted list
rather than only catching messages published after it subscribes.
"""

import json
import os
from datetime import UTC, datetime

import redis

_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    return _redis_client


def _key(job_id: str) -> str:
    return f"job:{job_id}:events"


def publish_event(job_id: str, message: str) -> None:
    event = json.dumps({"message": message, "ts": datetime.now(UTC).isoformat()})
    _get_redis().rpush(_key(job_id), event)


def read_events(job_id: str, start: int = 0) -> list[str]:
    return _get_redis().lrange(_key(job_id), start, -1)
