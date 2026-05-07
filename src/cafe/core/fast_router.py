"""Deterministic chat fast path for common cafe operations.

The fast router runs before the AgentScope orchestrator. Matched intents use
plain Python services and return immediately, with zero LLM/specialist calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Awaitable, Callable

from agentscope.message import Msg

from cafe.agents.memory import (
    DEFAULT_USER_ID,
    clear_cart_snapshot,
    load_memory,
    save_cart_snapshot,
    save_messages,
    save_order_snapshot,
)
from cafe.core.background_tasks import schedule_background, session_task_key
from cafe.core.observability import observed_span
from cafe.core.runtime_cache import get_cache
from cafe.core.state import get_store
from cafe.core.validator import ValidationError
from cafe.models.cart import Cart
from cafe.models.menu import MenuItem
from cafe.services import cart_service, faq_service, order_service
from cafe.services.menu_index_service import (
    browse_menu_query,
    build_menu_item_match_index,
    format_menu_categories,
)


Handler = Callable[[str, str, str], Awaitable[str]]


@dataclass
class FastRouteResult:
    matched: bool
    intent: str | None = None
    reply: str = ""
    route: str = "orchestrator"
    tool_calls: list[dict] = field(default_factory=list)

    @classmethod
    def miss(cls) -> "FastRouteResult":
        return cls(matched=False)


async def fast_intent_router(
    session_id: str,
    user_text: str,
    user_id: str = DEFAULT_USER_ID,
) -> FastRouteResult:
    """Route deterministic intents without creating specialist agents."""
    with observed_span("fast_router", "fast_router.match") as span:
        intent = _detect_intent(user_text)
        span.update(intent=intent, matched=bool(intent))

    if intent is None:
        return FastRouteResult.miss()

    handler = _HANDLERS[intent]
    with observed_span("tool", f"fast.{intent}"):
        reply = await handler(session_id, user_text, user_id)

    schedule_background(
        save_fast_turn(
            session_id=session_id,
            user_id=user_id,
            user_text=user_text,
            reply=reply,
            intent=intent,
        ),
        name=f"save_fast_turn:{intent}",
        key=session_task_key(user_id, session_id),
    )
    return FastRouteResult(
        matched=True,
        intent=intent,
        reply=reply,
        route=f"fast_router->{intent}",
    )


async def save_fast_turn(
    *,
    session_id: str,
    user_id: str,
    user_text: str,
    reply: str,
    intent: str,
) -> None:
    """Persist the visible chat turn for frontend history and memory continuity."""
    with observed_span("memory", "memory.fast_turn_save"):
        memory = load_memory(session_id, user_id=user_id)
        await save_messages(
            memory,
            [
                Msg(
                    "user",
                    f"[session_id={session_id}] {user_text}",
                    "user",
                    metadata={"display_text": user_text, "fast_path_intent": intent},
                ),
                Msg(
                    "assistant",
                    reply,
                    "assistant",
                    metadata={"fast_path_intent": intent},
                ),
            ],
        )


def _detect_intent(user_text: str) -> str | None:
    text = _normalize(user_text)

    if _has_any(
        text,
        "clear cart",
        "clear my cart",
        "empty cart",
        "empty my cart",
        "reset cart",
    ):
        return "clear_cart"
    if _has_any(text, "remove ", "delete "):
        return "remove_from_cart"
    if _has_any(text, "add ", "put "):
        return "add_to_cart"
    if _has_any(
        text,
        "place order",
        "place my order",
        "checkout",
        "confirm order",
        "order now",
    ):
        return "place_order"
    if _has_any(
        text,
        "track order",
        "track my order",
        "order status",
        "where is my order",
    ):
        return "track_order"
    if _has_any(text, "view cart", "show cart", "my cart", "cart total"):
        return "view_cart"

    if _has_any(text, "offer", "offers", "discount", "coupon", "promo", "deal"):
        return "offers"
    if _has_any(text, "timing", "timings", "hours", "open", "close", "closing"):
        return "timings"
    if _looks_like_faq(text):
        return "faq"

    if _has_any(text, "category", "categories", "sections"):
        return "categories"
    if _has_any(text, "coffee", "coffees"):
        return "coffee"
    if _has_any(
        text,
        "beverages",
        "beverage",
        "drinks",
        "cool drinks",
        "cold drinks",
    ):
        return "beverages"
    if "menu" in text:
        return "show_menu"

    return None


async def _show_menu(_session_id: str, user_text: str, _user_id: str) -> str:
    return await _cached_static_reply(
        f"menu:{_normalize(user_text)}",
        lambda: browse_menu_query(user_text).display_text,
    )


async def _categories(_session_id: str, _user_text: str, _user_id: str) -> str:
    return await _cached_static_reply(
        "menu:categories",
        lambda: format_menu_categories(include_items=False),
    )


async def _coffee(_session_id: str, user_text: str, _user_id: str) -> str:
    return await _cached_static_reply(
        f"menu:{_normalize(user_text)}",
        lambda: browse_menu_query(user_text).display_text,
    )


async def _beverages(_session_id: str, user_text: str, _user_id: str) -> str:
    return await _cached_static_reply(
        f"menu:{_normalize(user_text)}",
        lambda: browse_menu_query(user_text).display_text,
    )


async def _add_to_cart(session_id: str, user_text: str, user_id: str) -> str:
    quantity = _extract_quantity(user_text)
    item_name = _extract_menu_item_name(user_text)
    if item_name is None:
        return "Which menu item would you like me to add to your cart?"

    try:
        item = _menu_item_from_match(item_name)
        cart = cart_service.add_resolved_item(get_store(), session_id, item, quantity)
        schedule_background(
            save_cart_snapshot(session_id, cart.model_copy(deep=True), user_id=user_id),
            name="save_cart_snapshot:add",
            key=session_task_key(user_id, session_id),
        )
    except ValidationError as exc:
        return str(exc)

    plural = "s" if quantity > 1 else ""
    return (
        f"Added {quantity} {item.name}{plural} to your cart.\n"
        f"{_format_cart(cart)}"
    )


async def _remove_from_cart(session_id: str, user_text: str, user_id: str) -> str:
    cart = cart_service.view_cart(get_store(), session_id)
    item = _match_cart_item(cart, user_text)
    if item is None:
        return "Which cart item should I remove?"

    try:
        updated = cart_service.remove_item(get_store(), session_id, item.item_id)
        schedule_background(
            save_cart_snapshot(
                session_id,
                updated.model_copy(deep=True),
                user_id=user_id,
            ),
            name="save_cart_snapshot:remove",
            key=session_task_key(user_id, session_id),
        )
    except ValidationError as exc:
        return str(exc)

    return f"Removed {item.name} from your cart.\n{_format_cart(updated)}"


async def _clear_cart(session_id: str, _user_text: str, user_id: str) -> str:
    cart_service.clear_cart(get_store(), session_id)
    schedule_background(
        clear_cart_snapshot(session_id, user_id=user_id),
        name="clear_cart_snapshot",
        key=session_task_key(user_id, session_id),
    )
    return "Your cart is cleared."


async def _view_cart(session_id: str, _user_text: str, user_id: str) -> str:
    cart = cart_service.view_cart(get_store(), session_id)
    schedule_background(
        save_cart_snapshot(session_id, cart.model_copy(deep=True), user_id=user_id),
        name="save_cart_snapshot:view",
        key=session_task_key(user_id, session_id),
    )
    return _format_cart(cart)


async def _place_order(session_id: str, user_text: str, user_id: str) -> str:
    try:
        order = order_service.place_order(
            get_store(),
            session_id,
            _extract_budget(user_text),
        )
        schedule_background(
            save_order_snapshot(order.model_copy(deep=True), user_id=user_id),
            name="save_order_snapshot:place",
            key=session_task_key(user_id, session_id),
        )
        schedule_background(
            clear_cart_snapshot(session_id, user_id=user_id),
            name="clear_cart_snapshot:place",
            key=session_task_key(user_id, session_id),
        )
    except ValidationError as exc:
        return str(exc)

    return _format_order(order, heading=f"Order {order.order_id} is confirmed.")


async def _track_order(session_id: str, user_text: str, user_id: str) -> str:
    order_id = _extract_order_id(user_text)
    if order_id is None:
        order = _latest_order_for_session(session_id)
        if order is None:
            return "Please share your order id so I can track it."
    else:
        try:
            order = order_service.get_order(get_store(), order_id)
        except ValidationError as exc:
            return str(exc)

    schedule_background(
        save_order_snapshot(order.model_copy(deep=True), user_id=user_id),
        name="save_order_snapshot:track",
        key=session_task_key(user_id, session_id),
    )
    return _format_order(order, heading=f"Order {order.order_id} is {order.status}.")


async def _faq(_session_id: str, user_text: str, _user_id: str) -> str:
    try:
        _topic, answer = faq_service.lookup_faq(user_text)
        return answer
    except ValidationError:
        return "I do not have a saved FAQ answer for that yet."


async def _timings(_session_id: str, _user_text: str, _user_id: str) -> str:
    return await _cached_static_reply(
        "faq:hours",
        lambda: faq_service.lookup_faq("hours")[1],
    )


async def _offers(_session_id: str, _user_text: str, _user_id: str) -> str:
    return await _cached_static_reply(
        "faq:offers",
        lambda: (
            "I do not have live discount details right now. "
            f"Current loyalty benefit: {faq_service.lookup_faq('loyalty')[1]}"
        ),
    )


async def _cached_static_reply(key: str, factory: Callable[[], str]) -> str:
    cache_key = f"milo:fast:{key}"
    cache = get_cache()
    cached = await cache.get(cache_key)
    if isinstance(cached, str):
        return cached

    value = factory()
    await cache.set(cache_key, value, ttl_seconds=3600)
    return value


def _format_cart(cart: Cart) -> str:
    if cart.is_empty():
        return "Your cart is empty."

    lines = ["Your cart:"]
    for item in cart.items:
        lines.append(
            f"- {item.quantity} x {item.name} - INR {item.unit_price_inr} each "
            f"= INR {item.line_total_inr}"
        )
    lines.append(f"Total: INR {cart.total_inr}")
    return "\n".join(lines)


def _format_order(order, *, heading: str) -> str:
    lines = [heading, "Items:"]
    for item in order.items:
        lines.append(
            f"- {item.quantity} x {item.name} - INR {item.line_total_inr}"
        )
    lines.append(f"Total: INR {order.total_inr}")
    return "\n".join(lines)


def _extract_menu_item_name(user_text: str) -> str | None:
    normalized_text = f" {_normalize(user_text)} "
    for item in _menu_item_candidates():
        normalized_name = _normalize(item.name)
        if f" {normalized_name} " in normalized_text:
            return item.name
    return None


@lru_cache(maxsize=1)
def _menu_item_candidates():
    return tuple(
        sorted(
            build_menu_item_match_index(),
            key=lambda item: len(_normalize(item.name)),
            reverse=True,
        )
    )


def _menu_item_from_match(item_name: str) -> MenuItem:
    match = _menu_items_by_name().get(_normalize(item_name))
    if match is None:
        raise ValidationError(f"Unknown menu item: {item_name}")

    price_inr = _first_price_inr(match.price)
    if price_inr is None:
        raise ValidationError(f"I found {match.name}, but it does not have a price.")

    tags = list(match.tags)
    for tag in (match.top_level, match.section, match.serving):
        if tag and tag not in tags:
            tags.append(tag)

    return MenuItem(
        id=_menu_item_id(match.name),
        name=match.name,
        category=match.section,
        price_inr=price_inr,
        available=True,
        tags=tags,
    )


@lru_cache(maxsize=1)
def _menu_items_by_name():
    return {_normalize(item.name): item for item in build_menu_item_match_index()}


def _match_cart_item(cart: Cart, user_text: str):
    normalized_text = f" {_normalize(user_text)} "
    for item in sorted(cart.items, key=lambda line: len(line.name), reverse=True):
        if f" {_normalize(item.name)} " in normalized_text or item.item_id in user_text:
            return item
    return None


def _extract_quantity(user_text: str) -> int:
    text = _normalize(user_text)
    if match := re.search(r"\b(\d{1,2})\b", text):
        return max(1, int(match.group(1)))

    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    for word, value in words.items():
        if f" {word} " in f" {text} ":
            return value
    return 1


def _extract_budget(user_text: str) -> int | None:
    text = _normalize(user_text)
    patterns = (
        r"\bbudget\s*(?:is|of|:)?\s*(?:inr|rs|rupees)?\s*(\d+)",
        r"\bunder\s*(?:inr|rs|rupees)?\s*(\d+)",
        r"\bwithin\s*(?:inr|rs|rupees)?\s*(\d+)",
    )
    for pattern in patterns:
        if match := re.search(pattern, text):
            return int(match.group(1))
    return None


def _extract_order_id(user_text: str) -> str | None:
    if match := re.search(r"\bord-[a-zA-Z0-9-]+\b", user_text):
        return match.group(0)
    return None


def _latest_order_for_session(session_id: str):
    orders = [
        order for order in get_store().orders.values() if order.session_id == session_id
    ]
    return orders[-1] if orders else None


def _looks_like_faq(text: str) -> bool:
    return _has_any(
        text,
        "faq",
        "wifi",
        "wi fi",
        "internet",
        "password",
        "vegan",
        "allergen",
        "allergens",
        "payment",
        "pay",
        "upi",
        "card",
        "cash",
        "location",
        "address",
        "loyalty",
        "reward",
        "rewards",
    )


def _has_any(text: str, *phrases: str) -> bool:
    return any(phrase in text for phrase in phrases)


def _first_price_inr(price_text: str | None) -> int | None:
    if not price_text:
        return None
    match = re.search(r"\d+", price_text)
    return int(match.group(0)) if match else None


def _menu_item_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return f"menu-{slug}"


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


_HANDLERS: dict[str, Handler] = {
    "show_menu": _show_menu,
    "categories": _categories,
    "beverages": _beverages,
    "coffee": _coffee,
    "add_to_cart": _add_to_cart,
    "remove_from_cart": _remove_from_cart,
    "clear_cart": _clear_cart,
    "view_cart": _view_cart,
    "place_order": _place_order,
    "track_order": _track_order,
    "faq": _faq,
    "timings": _timings,
    "offers": _offers,
}
