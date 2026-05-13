"""Runtime debug trace data for the architecture dashboard."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from time import perf_counter
from typing import Any

MAX_TURNS = 40


FLOW_STEPS = [
    {
        "id": "client",
        "label": "Client",
        "kind": "entry",
        "next": ["api"],
    },
    {
        "id": "api",
        "label": "FastAPI /chat",
        "kind": "http",
        "next": ["turn_runtime"],
    },
    {
        "id": "turn_runtime",
        "label": "turn_runtime",
        "kind": "core",
        "next": ["router"],
    },
    {
        "id": "router",
        "label": "Intent Router",
        "kind": "core",
        "next": ["context", "session_manager", "response"],
    },
    {
        "id": "context",
        "label": "Context Snapshot",
        "kind": "state",
        "next": ["orchestrator"],
    },
    {
        "id": "session_manager",
        "label": "Session Manager",
        "kind": "memory",
        "next": ["orchestrator"],
    },
    {
        "id": "orchestrator",
        "label": "Orchestrator",
        "kind": "agent",
        "next": ["specialists", "critic"],
    },
    {
        "id": "specialists",
        "label": "Specialists",
        "kind": "agent",
        "next": ["tools"],
    },
    {
        "id": "tools",
        "label": "Domain Tools",
        "kind": "tool",
        "next": ["state_store"],
    },
    {
        "id": "state_store",
        "label": "StateStore",
        "kind": "state",
        "next": ["orchestrator"],
    },
    {
        "id": "critic",
        "label": "Critic Hook",
        "kind": "validation",
        "next": ["response"],
    },
    {
        "id": "response",
        "label": "Final Response",
        "kind": "exit",
        "next": [],
    },
]


@dataclass
class TraceEvent:
    step: str
    status: str
    detail: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "status": self.status,
            "detail": self.detail,
            "timestamp": self.timestamp,
            "data": self.data,
        }


@dataclass
class TurnTrace:
    turn_id: int
    session_id: str
    user_text: str
    started_at: str
    status: str = "running"
    duration_ms: float | None = None
    context: str = ""
    reply_preview: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    critique: dict[str, Any] | None = None
    events: list[TraceEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "user_text": self.user_text,
            "started_at": self.started_at,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "context": self.context,
            "reply_preview": self.reply_preview,
            "tool_calls": self.tool_calls,
            "critique": self.critique,
            "events": [event.to_dict() for event in self.events],
        }


class DebugTraceStore:
    """Small in-process trace store for local debugging."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._turns: deque[TurnTrace] = deque(maxlen=MAX_TURNS)
        self._next_turn_id = 1
        self._version = 0
        self._start_times: dict[int, float] = {}

    def start_turn(self, session_id: str, user_text: str) -> int:
        with self._lock:
            turn_id = self._next_turn_id
            self._next_turn_id += 1
            self._turns.append(
                TurnTrace(
                    turn_id=turn_id,
                    session_id=session_id,
                    user_text=_preview(user_text, 180),
                    started_at=datetime.now(UTC).isoformat(),
                ),
            )
            self._start_times[turn_id] = perf_counter()
            self._version += 1
            return turn_id

    def add_event(
        self,
        turn_id: int,
        step: str,
        status: str,
        detail: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            turn = self._find_turn(turn_id)
            if turn is None:
                return
            turn.events.append(
                TraceEvent(
                    step=step,
                    status=status,
                    detail=detail,
                    data=data or {},
                ),
            )
            self._version += 1

    def set_context(self, turn_id: int, context: str) -> None:
        with self._lock:
            turn = self._find_turn(turn_id)
            if turn is not None:
                turn.context = context
                self._version += 1

    def finish_turn(
        self,
        turn_id: int,
        status: str,
        reply: str,
        tool_calls: list[dict[str, Any]],
        critique: dict[str, Any] | None,
    ) -> None:
        with self._lock:
            turn = self._find_turn(turn_id)
            if turn is None:
                return
            turn.status = status
            turn.reply_preview = _preview(reply, 260)
            turn.tool_calls = tool_calls
            turn.critique = critique
            started = self._start_times.pop(turn_id, None)
            if started is not None:
                turn.duration_ms = round((perf_counter() - started) * 1000, 1)
            self._version += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "version": self._version,
                "flow": FLOW_STEPS,
                "turns": [turn.to_dict() for turn in reversed(self._turns)],
            }

    def reset(self) -> None:
        with self._lock:
            self._turns.clear()
            self._start_times.clear()
            self._version += 1

    def _find_turn(self, turn_id: int) -> TurnTrace | None:
        for turn in self._turns:
            if turn.turn_id == turn_id:
                return turn
        return None


def _preview(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


_debug_trace_store = DebugTraceStore()


def get_debug_trace_store() -> DebugTraceStore:
    return _debug_trace_store
