"""Shared session context for Orchestrator and specialist agents."""

from __future__ import annotations

import re
from contextvars import ContextVar, Token
from dataclasses import dataclass

from cafe.agents.memory import DEFAULT_USER_ID
from cafe.core.state import get_store


@dataclass(frozen=True)
class SessionContext:
    """Runtime context shared across one agentic turn."""

    user_id: str
    session_id: str
    preferences: tuple[str, ...] = ()
    cart_items_count: int = 0
    cart_total_inr: int = 0
    recent_orders: tuple[str, ...] = ()
    last_menu_scope: str | None = None

    @property
    def has_preferences(self) -> bool:
        """Return whether the customer has active preferences.

        Returns:
            - return bool - Whether preferences are active.
        """
        return bool(self.preferences)


_CURRENT_SESSION_CONTEXT: ContextVar[SessionContext | None] = ContextVar(
    "current_session_context",
    default=None,
)


def set_current_session_context(context: SessionContext) -> Token:
    """Set the current turn session context.

    Args:
        - context: SessionContext - The context for the current turn.

    Returns:
        - return Token - The context variable reset token.
    """
    return _CURRENT_SESSION_CONTEXT.set(context)


def reset_current_session_context(token: Token) -> None:
    """Reset the current turn session context.

    Args:
        - token: Token - The reset token.

    Returns:
        - return None - This function has no return value.
    """
    _CURRENT_SESSION_CONTEXT.reset(token)


def get_current_session_context() -> SessionContext | None:
    """Return the current turn session context, if one exists.

    Returns:
        - return SessionContext | None - The current context.
    """
    return _CURRENT_SESSION_CONTEXT.get()


def extract_session_preferences(user_text: str) -> set[str]:
    """Extract durable preference markers from one customer message.

    Args:
        - user_text: str - The raw customer message.

    Returns:
        - return set[str] - Detected preference labels.
    """
    text = " ".join(re.sub(r"[^a-z0-9]+", " ", user_text.casefold()).split())
    preferences: set[str] = set()
    checks = {
        "vegan": ("vegan", "plant based"),
        "vegetarian": ("vegetarian", "i am veg", "i m veg", "pure veg"),
        "jain": ("jain",),
        "dairy-free": ("dairy free", "no dairy", "lactose"),
        "diabetic": ("diabetic", "diabetes", "sugar free", "low sugar", "no sugar"),
        "gluten-free": ("gluten free",),
        "eggless": ("eggless", "no egg"),
        "no chicken": ("no chicken",),
        "no meat": ("no meat",),
        "nut allergy": ("nut allergy", "allergic to nut", "allergy to nut"),
    }
    for label, terms in checks.items():
        if any(term in text for term in terms):
            preferences.add(label)
    return preferences


def record_session_preferences(session_id: str, user_text: str) -> None:
    """Remember active preferences in the in-process session state.

    Args:
        - session_id: str - The active session id.
        - user_text: str - The raw customer message.

    Returns:
        - return None - This function has no return value.
    """
    preferences = extract_session_preferences(user_text)
    if not preferences:
        return
    store = get_store()
    store.session_preferences.setdefault(session_id, set()).update(preferences)


def session_has_preferences(session_id: str) -> bool:
    """Return whether the session has active preferences.

    Args:
        - session_id: str - The active session id.

    Returns:
        - return bool - Whether preferences are active.
    """
    return bool(get_store().session_preferences.get(session_id))


def build_session_context(
    user_id: str = DEFAULT_USER_ID,
    session_id: str = "default_session",
) -> SessionContext:
    """Build the shared context object for one session.

    Args:
        - user_id: str - The active user id.
        - session_id: str - The active session id.

    Returns:
        - return SessionContext - The current session context.
    """
    store = get_store()
    cart = store.get_cart(session_id)
    recent_orders = tuple(
        f"{order.order_id}({order.status})"
        for order in [
            order for order in store.orders.values() if order.session_id == session_id
        ][-3:]
    )
    preferences = tuple(sorted(store.session_preferences.get(session_id, set())))
    return SessionContext(
        user_id=user_id,
        session_id=session_id,
        preferences=preferences,
        cart_items_count=len(cart.items),
        cart_total_inr=cart.total_inr,
        recent_orders=recent_orders,
        last_menu_scope=store.last_menu_scope.get(session_id),
    )


def format_orchestrator_context(context: SessionContext) -> str:
    """Format compact context for the Orchestrator input message.

    Args:
        - context: SessionContext - The shared session context.

    Returns:
        - return str - Compact context text.
    """
    parts = [f"[session_id={context.session_id}]"]
    if context.cart_items_count:
        parts.append(
            f"[cart: {context.cart_items_count} item(s), "
            f"INR {context.cart_total_inr}]"
        )
    if context.recent_orders:
        parts.append(f"[recent_orders: {', '.join(context.recent_orders)}]")
    if context.preferences:
        parts.append(f"[preferences: {', '.join(context.preferences)}]")
    if context.last_menu_scope:
        parts.append(f"[last_menu_scope: {context.last_menu_scope}]")
    return " ".join(parts)


def format_specialist_context(
    context: SessionContext | None,
    task: str,
    *,
    memory_summary: str = "",
) -> str:
    """Format specialist-facing task text with shared session context.

    Args:
        - context: SessionContext | None - The shared session context.
        - task: str - The specialist task.
        - memory_summary: str - Optional cumulative conversation summary.

    Returns:
        - return str - The specialist-facing message.
    """
    if context is None:
        if memory_summary.strip():
            return f"Conversation memory:\n{memory_summary.strip()}\n\nTask: {task}"
        return task

    lines = [
        "Customer context:",
        f"- session_id: {context.session_id}",
    ]
    if context.preferences:
        lines.append(
            f"- active preferences/constraints: {', '.join(context.preferences)}"
        )
    if context.cart_items_count:
        lines.append(
            f"- cart: {context.cart_items_count} item(s), "
            f"INR {context.cart_total_inr}"
        )
    if context.recent_orders:
        lines.append(f"- recent orders: {', '.join(context.recent_orders)}")
    if context.last_menu_scope:
        lines.append(f"- last menu scope: {context.last_menu_scope}")
    if context.preferences:
        lines.append(
            "- apply active preferences/constraints to the task; do not return "
            "a generic list when the constraint should change the answer"
        )
        lines.append(
            "- for broad category requests, use the constraint to select or caveat "
            "specific items unless the customer explicitly asks to ignore it"
        )
    if memory_summary.strip():
        lines.extend(["", "Conversation memory:", memory_summary.strip()])
    lines.extend(["", f"Task: {task}"])
    return "\n".join(lines)
