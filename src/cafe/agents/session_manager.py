"""Per-session orchestrator cache.

Specialists are shared across sessions because they're stateless (state lives
in StateStore).
"""

from threading import Lock

from agentscope.agent import ReActAgent

from cafe.agents.orchestrator import make_orchestrator


class SessionManager:
    def __init__(self) -> None:
        self._agents: dict[str, ReActAgent] = {}
        self._lock = Lock()

    def get_or_create(self, session_id: str) -> ReActAgent:
        with self._lock:
            if session_id not in self._agents:
                self._agents[session_id] = make_orchestrator()
            return self._agents[session_id]

    def reset(self, session_id: str | None = None) -> None:
        with self._lock:
            if session_id is None:
                self._agents.clear()
            else:
                self._agents.pop(session_id, None)

    def session_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._agents.keys())


_mgr: SessionManager | None = None


def get_session_manager() -> SessionManager:
    global _mgr
    if _mgr is None:
        _mgr = SessionManager()
    return _mgr
