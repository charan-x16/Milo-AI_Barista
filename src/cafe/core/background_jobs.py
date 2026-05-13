"""Background jobs for non-blocking chat follow-up work."""

from __future__ import annotations

import logging

from cafe.agents.memory import DEFAULT_USER_ID, compress_memory_after_turn
from cafe.agents.session_manager import get_session_manager

log = logging.getLogger(__name__)


async def run_memory_compression_job(
    user_id: str = DEFAULT_USER_ID,
    session_id: str = "default_session",
) -> None:
    """Run memory compression outside the request path.

    Args:
        - user_id: str - The active user id.
        - session_id: str - The active session id.

    Returns:
        - return None - This function has no return value.
    """
    # FastAPI BackgroundTasks are enough for this repo-level latency fix. In a
    # multi-worker production deployment, move this job to a durable queue such
    # as Celery, Dramatiq, RQ, or ARQ.
    try:
        orchestrator = get_session_manager().get_or_create(
            session_id,
            user_id=user_id,
        )
        await compress_memory_after_turn(orchestrator)
    except Exception:
        log.exception(
            "memory compression background job failed for user=%s session=%s",
            user_id,
            session_id,
        )
