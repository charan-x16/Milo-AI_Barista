"""Per-session orchestrator cache.

Specialists are shared across sessions because they're stateless (state lives
in StateStore).
"""

import time
from collections import OrderedDict
from threading import Lock

from agentscope.agent import ReActAgent

from cafe.agents.memory import DEFAULT_USER_ID
from cafe.agents.orchestrator import make_orchestrator
from cafe.config import get_settings


class SessionManager:
    def __init__(self) -> None:
        """Initialize the instance.

        Returns:
            - return None - The return value.
        """
        self._agents: OrderedDict[tuple[str, str], tuple[ReActAgent, float]] = (
            OrderedDict()
        )
        self._lock = Lock()

    def get_or_create(
        self,
        session_id: str,
        user_id: str = DEFAULT_USER_ID,
    ) -> ReActAgent:
        """Return the or create.

        Args:
            - session_id: str - The session id value.
            - user_id: str - The user id value.

        Returns:
            - return ReActAgent - The return value.
        """
        with self._lock:
            key = (user_id, session_id)
            self._evict_expired_locked()
            if key not in self._agents:
                self._agents[key] = (
                    make_orchestrator(
                        session_id=session_id,
                        user_id=user_id,
                    ),
                    time.monotonic(),
                )
            else:
                agent, _last_access = self._agents.pop(key)
                self._agents[key] = (agent, time.monotonic())
            self._evict_over_limit_locked()
            return self._agents[key][0]

    def reset(
        self,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Handle reset.

        Args:
            - session_id: str | None - The session id value.
            - user_id: str | None - The user id value.

        Returns:
            - return None - The return value.
        """
        with self._lock:
            if session_id is None:
                self._agents.clear()
            else:
                for key in list(self._agents):
                    if key[1] == session_id and (user_id is None or key[0] == user_id):
                        self._agents.pop(key, None)

    def session_ids(self) -> list[str]:
        """Handle session ids.

        Returns:
            - return list[str] - The return value.
        """
        with self._lock:
            return sorted({session_id for _, session_id in self._agents})

    def _evict_expired_locked(self) -> None:
        """Evict expired agents while holding the manager lock.

        Returns:
            - return None - This function has no return value.
        """
        ttl = get_settings().session_cache_ttl_seconds
        if ttl <= 0:
            return
        cutoff = time.monotonic() - ttl
        for key, (_agent, last_access) in list(self._agents.items()):
            if last_access < cutoff:
                self._agents.pop(key, None)

    def _evict_over_limit_locked(self) -> None:
        """Evict least-recently used agents over the configured cap.

        Returns:
            - return None - This function has no return value.
        """
        max_sessions = get_settings().session_cache_max_sessions
        if max_sessions <= 0:
            return
        while len(self._agents) > max_sessions:
            self._agents.popitem(last=False)


_mgr: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Return the session manager.

    Returns:
        - return SessionManager - The return value.
    """
    global _mgr
    if _mgr is None:
        _mgr = SessionManager()
    return _mgr
