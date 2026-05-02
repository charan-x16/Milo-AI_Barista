"""Per-session orchestrator cache.

Specialists are shared across sessions because they're stateless (state lives
in StateStore).
"""

from threading import Lock

from agentscope.agent import ReActAgent

from cafe.agents.memory import DEFAULT_USER_ID
from cafe.agents.orchestrator import make_orchestrator


class SessionManager:
    def __init__(self) -> None:
        self._agents: dict[tuple[str, str], ReActAgent] = {}
        self._lock = Lock()

    def get_or_create(
        self,
        session_id: str,
        user_id: str = DEFAULT_USER_ID,
    ) -> ReActAgent:
        with self._lock:
            key = (user_id, session_id)
            if key not in self._agents:
                self._agents[key] = make_orchestrator(
                    session_id=session_id,
                    user_id=user_id,
                )
            return self._agents[key]

    def reset(
        self,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        with self._lock:
            if session_id is None:
                self._agents.clear()
            else:
                for key in list(self._agents):
                    if key[1] == session_id and (user_id is None or key[0] == user_id):
                        self._agents.pop(key, None)

    def session_ids(self) -> list[str]:
        with self._lock:
            return sorted({session_id for _, session_id in self._agents})


_mgr: SessionManager | None = None


def get_session_manager() -> SessionManager:
    global _mgr
    if _mgr is None:
        _mgr = SessionManager()
    return _mgr
