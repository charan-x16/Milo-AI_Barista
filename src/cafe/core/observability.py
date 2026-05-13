"""Request-scoped latency and API-call observability.

This module is intentionally lightweight: it uses contextvars so lower layers
can record spans without changing function signatures across the app.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from functools import wraps
from inspect import isawaitable
from typing import Any, Callable

log = logging.getLogger("cafe.observability")

_CURRENT_OBSERVER: ContextVar["TurnObserver | None"] = ContextVar(
    "current_turn_observer",
    default=None,
)


@dataclass
class ObservabilitySpan:
    name: str
    category: str
    started_at: float
    metadata: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    status: str = "complete"

    def finish(self, status: str = "complete") -> None:
        """Handle finish.

        Args:
            - status: str - The status value.

        Returns:
            - return None - The return value.
        """
        self.status = status
        self.latency_ms = round((time.perf_counter() - self.started_at) * 1000, 1)

    def update(self, **metadata: Any) -> None:
        """Handle update.

        Args:
            - metadata: Any - The metadata value.

        Returns:
            - return None - The return value.
        """
        self.metadata.update({k: v for k, v in metadata.items() if v is not None})


class TurnObserver:
    """Collects all timings/counters for one chat turn."""

    def __init__(self, *, session_id: str, user_id: str, user_text: str) -> None:
        """Initialize the instance.

        Args:
            - session_id: str - The session id value.
            - user_id: str - The user id value.
            - user_text: str - The user text value.

        Returns:
            - return None - The return value.
        """
        self.request_id = uuid.uuid4().hex[:12]
        self.session_id = session_id
        self.user_id = user_id
        self.user_text = user_text
        self.started_at = time.perf_counter()
        self.spans: list[ObservabilitySpan] = []
        self.reply_source: str | None = None
        self.tool_calls: list[dict[str, Any]] = []
        self.intent: str | None = None
        self.fallback_path: str | None = None
        self.status = "running"
        self.error: str | None = None

    @contextmanager
    def span(
        self,
        category: str,
        name: str,
        metadata: dict[str, Any] | None = None,
    ):
        """Handle span.

        Args:
            - category: str - The category value.
            - name: str - The name value.
            - metadata: dict[str, Any] | None - The metadata value.

        Returns:
            - return None - The return value.
        """
        span = ObservabilitySpan(
            category=category,
            name=name,
            started_at=time.perf_counter(),
            metadata=dict(metadata or {}),
        )
        try:
            yield span
        except Exception:
            span.finish("error")
            self.spans.append(span)
            raise
        else:
            span.finish()
            self.spans.append(span)

    def set_result(
        self,
        *,
        status: str,
        reply_source: str | None,
        tool_calls: list[dict[str, Any]] | None,
        intent: str | None = None,
        fallback_path: str | None = None,
        error: str | None = None,
    ) -> None:
        """Set the result.

        Args:
            - status: str - The status value.
            - reply_source: str | None - The reply source value.
            - tool_calls: list[dict[str, Any]] | None - The tool calls value.
            - intent: str | None - The intent value.
            - fallback_path: str | None - The fallback path value.
            - error: str | None - The error value.

        Returns:
            - return None - The return value.
        """
        self.status = status
        self.reply_source = reply_source
        self.tool_calls = tool_calls or []
        self.intent = intent
        self.fallback_path = fallback_path
        self.error = error

    def summary(self) -> dict[str, Any]:
        """Handle summary.

        Returns:
            - return dict[str, Any] - The return value.
        """
        counters = {
            "llm_calls": self._count("llm"),
            "tool_calls": self._count("tool"),
            "qdrant_calls": self._count("qdrant"),
            "sql_calls": self._count("sql"),
            "memory_compression_calls": self._count("memory_compression"),
        }
        token_usage = self._token_usage()
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "status": self.status,
            "intent": self._infer_intent(),
            "fallback_path_taken": self._fallback_path_taken(),
            **counters,
            "latency_ms": self._latency_map(),
            "average_latency_ms": self._average_latency_by_category(),
            "token_usage": token_usage,
            "total_ms": round((time.perf_counter() - self.started_at) * 1000, 1),
            "reply_source": self.reply_source,
            "error": self.error,
        }

    def log_summary(self) -> None:
        """Handle log summary.

        Returns:
            - return None - The return value.
        """
        log.info("chat_turn_observability %s", json.dumps(self.summary(), default=str))

    def _count(self, category: str) -> int:
        """Handle count.

        Args:
            - category: str - The category value.

        Returns:
            - return int - The return value.
        """
        return sum(1 for span in self.spans if span.category == category)

    def _latency_map(self) -> dict[str, float]:
        """Handle latency map.

        Returns:
            - return dict[str, float] - The return value.
        """
        counts: dict[str, int] = {}
        latencies: dict[str, float] = {}
        for span in self.spans:
            base = _safe_key(span.name)
            counts[base] = counts.get(base, 0) + 1
            latencies[f"{base}_{counts[base]}"] = span.latency_ms
        return latencies

    def _average_latency_by_category(self) -> dict[str, float]:
        """Handle average latency by category.

        Returns:
            - return dict[str, float] - The return value.
        """
        grouped: dict[str, list[float]] = {}
        for span in self.spans:
            grouped.setdefault(span.category, []).append(span.latency_ms)
        return {
            category: round(sum(values) / len(values), 1)
            for category, values in grouped.items()
            if values
        }

    def _token_usage(self) -> dict[str, Any]:
        """Handle token usage.

        Returns:
            - return dict[str, Any] - The return value.
        """
        input_tokens = 0
        output_tokens = 0
        calls: list[dict[str, Any]] = []
        for span in self.spans:
            if span.category != "llm":
                continue
            usage = {
                "name": span.name,
                "input_tokens": int(span.metadata.get("input_tokens") or 0),
                "output_tokens": int(span.metadata.get("output_tokens") or 0),
            }
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
            input_tokens += usage["input_tokens"]
            output_tokens += usage["output_tokens"]
            calls.append(usage)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "calls": calls,
        }

    def _infer_intent(self) -> str:
        """Handle infer intent.

        Returns:
            - return str - The return value.
        """
        if self.intent:
            return self.intent
        tool_names = [call.get("name") for call in self.tool_calls]
        if "ask_product_agent" in tool_names:
            return "menu_search"
        if "ask_cart_agent" in tool_names:
            return "cart"
        if "ask_order_agent" in tool_names:
            return "order"
        if "ask_support_agent" in tool_names:
            return "support"
        return "orchestrator_direct"

    def _fallback_path_taken(self) -> str:
        """Handle fallback path taken.

        Returns:
            - return str - The return value.
        """
        if self.fallback_path:
            return self.fallback_path
        specialists = [
            str(call.get("name", "")).removeprefix("ask_").removesuffix("_agent")
            for call in self.tool_calls
            if str(call.get("name", "")).startswith("ask_")
        ]
        if specialists:
            return "orchestrator->" + "->".join(specialists)
        return "orchestrator"


class ObservedChatModel:
    """Proxy that records one span for each model API invocation."""

    def __init__(self, wrapped: Any, *, agent_name: str) -> None:
        """Initialize the instance.

        Args:
            - wrapped: Any - The wrapped value.
            - agent_name: str - The agent name value.

        Returns:
            - return None - The return value.
        """
        self._wrapped = wrapped
        self.agent_name = agent_name

    def __getattr__(self, name: str) -> Any:
        """Handle getattr.

        Args:
            - name: str - The name value.

        Returns:
            - return Any - The return value.
        """
        return getattr(self._wrapped, name)

    @property
    def model_name(self) -> str:
        """Handle model name.

        Returns:
            - return str - The return value.
        """
        return self._wrapped.model_name

    @property
    def stream(self) -> bool:
        """Handle stream.

        Returns:
            - return bool - The return value.
        """
        return self._wrapped.stream

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Handle call.

        Args:
            - args: Any - The args value.
            - kwargs: Any - The kwargs value.

        Returns:
            - return Any - The return value.
        """
        observer = current_observer()
        if observer is None:
            return await self._wrapped(*args, **kwargs)

        name = f"{_safe_key(self.agent_name)}_llm"
        metadata = {
            "agent": self.agent_name,
            "model": getattr(self._wrapped, "model_name", None),
            "provider_call": "chat",
        }
        with observer.span("llm", name, metadata) as span:
            response = await self._wrapped(*args, **kwargs)
            _record_usage(span, response)
            return response


def current_observer() -> TurnObserver | None:
    """Handle current observer.

    Returns:
        - return TurnObserver | None - The return value.
    """
    return _CURRENT_OBSERVER.get()


def set_current_observer(observer: TurnObserver) -> Token:
    """Set the current observer.

    Args:
        - observer: TurnObserver - The observer value.

    Returns:
        - return Token - The return value.
    """
    return _CURRENT_OBSERVER.set(observer)


def reset_current_observer(token: Token) -> None:
    """Reset the current observer.

    Args:
        - token: Token - The token value.

    Returns:
        - return None - The return value.
    """
    _CURRENT_OBSERVER.reset(token)


def observed_span(
    category: str,
    name: str,
    metadata: dict[str, Any] | None = None,
):
    """Handle observed span.

    Args:
        - category: str - The category value.
        - name: str - The name value.
        - metadata: dict[str, Any] | None - The metadata value.

    Returns:
        - return Any - The return value.
    """
    observer = current_observer()
    if observer is None:
        return _null_span()
    return observer.span(category, name, metadata)


def observe_tool(name: str | None = None) -> Callable:
    """Decorator for async/sync tool functions.

    Args:
        - name: str | None - The name value.

    Returns:
        - return Callable - The return value.
    """

    def decorator(func: Callable) -> Callable:
        """Handle decorator.

        Args:
            - func: Callable - The func value.

        Returns:
            - return Callable - The return value.
        """
        span_name = name or func.__name__

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            """Handle async wrapper.

            Args:
                - args: Any - The args value.
                - kwargs: Any - The kwargs value.

            Returns:
                - return Any - The return value.
            """
            with observed_span("tool", f"tool.{span_name}"):
                result = func(*args, **kwargs)
                if isawaitable(result):
                    return await result
                return result

        return async_wrapper

    return decorator


@contextmanager
def _null_span():
    """Handle null span.

    Returns:
        - return None - The return value.
    """
    span = ObservabilitySpan("noop", "noop", time.perf_counter())
    yield span


def _record_usage(span: ObservabilitySpan, response: Any) -> None:
    """Handle record usage.

    Args:
        - span: ObservabilitySpan - The span value.
        - response: Any - The response value.

    Returns:
        - return None - The return value.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if isinstance(usage, dict):
        input_tokens = usage.get("input_tokens", input_tokens)
        output_tokens = usage.get("output_tokens", output_tokens)
    span.update(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usage_time_s=getattr(usage, "time", None),
    )


def _safe_key(value: str) -> str:
    """Handle safe key.

    Args:
        - value: str - The value value.

    Returns:
        - return str - The return value.
    """
    return (
        str(value).strip().replace(" ", "_").replace("-", "_").replace(".", "_").lower()
    )
