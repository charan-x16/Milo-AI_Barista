"""Small background task runner for non-blocking request persistence."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from contextvars import Context
from collections.abc import Hashable
from typing import Any, Awaitable


log = logging.getLogger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()
_BACKGROUND_CHAINS: dict[Hashable, asyncio.Task[Any]] = {}


def session_task_key(user_id: str, session_id: str) -> tuple[str, str]:
    """Return the ordering key used for session-level persistence tasks."""
    return (user_id, session_id)


def schedule_background(
    awaitable: Awaitable[Any],
    *,
    name: str,
    key: Hashable | None = None,
) -> asyncio.Task[Any] | None:
    """Run awaitable later without inheriting request observability context.

    A keyed task waits for the previous task with the same key first. This keeps
    chat-memory/cart/order persistence ordered for each session while removing
    SQL latency from the response path.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        log.warning("background task %s skipped: no running event loop", name)
        return None

    previous = _BACKGROUND_CHAINS.get(key) if key is not None else None

    async def runner() -> None:
        if previous is not None:
            with suppress(asyncio.CancelledError):
                await previous
        try:
            await awaitable
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("background task %s failed", name)

    task = loop.create_task(runner(), name=name, context=Context())
    _BACKGROUND_TASKS.add(task)
    if key is not None:
        _BACKGROUND_CHAINS[key] = task

    def cleanup(done: asyncio.Task[Any]) -> None:
        _BACKGROUND_TASKS.discard(done)
        if key is not None and _BACKGROUND_CHAINS.get(key) is done:
            _BACKGROUND_CHAINS.pop(key, None)

    task.add_done_callback(cleanup)
    return task


async def drain_background_tasks(
    *,
    key: Hashable | None = None,
    timeout: float | None = 5.0,
) -> None:
    """Wait for scheduled background work, mainly for shutdown/tests/history APIs."""
    if key is None:
        tasks = list(_BACKGROUND_TASKS)
    else:
        task = _BACKGROUND_CHAINS.get(key)
        tasks = [task] if task is not None else []

    if not tasks:
        return

    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout,
        )
