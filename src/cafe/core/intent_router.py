"""Deterministic intent router for simple cafe operations.

The router is deliberately conservative: anything ambiguous stays on the
AgentScope Orchestrator path. Matched routes use in-process services and never
perform synchronous SQL memory reads before returning the customer response.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Callable

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
from cafe.core.state import get_store
from cafe.core.validator import ValidationError
from cafe.models.cart import Cart
from cafe.models.menu import MenuItem
from cafe.services import cart_service, faq_service, order_service
from cafe.services.menu_index_service import (
    browse_menu_query,
    build_menu_index,
    build_menu_item_match_index,
    build_menu_match_aliases,
    format_menu_categories,
)

GREETING_REPLIES = {
    "good morning": (
        "Good morning, welcome to Milo at By The Brew.\n\n"
        "I can help with the menu, your cart, or an order whenever you are ready."
    ),
    "good afternoon": (
        "Good afternoon, welcome to Milo at By The Brew.\n\n"
        "I can help you check the menu, build a cart, or track an order."
    ),
    "good evening": (
        "Good evening, welcome to Milo at By The Brew.\n\n"
        "I can help with menu options, cart changes, or order updates."
    ),
    "namaste": (
        "Namaste, welcome to Milo at By The Brew.\n\n"
        "I can help you browse the menu, build a cart, or track an order."
    ),
}
DEFAULT_GREETING_REPLY = (
    "Hi, welcome to Milo at By The Brew.\n\n"
    "I can help you browse the menu, build a cart, or track an order."
)


class Route(str, Enum):
    """Supported deterministic route names."""

    GREETING = "greeting"
    MENU_BROWSE = "menu_browse"
    CATEGORIES = "categories"
    BEVERAGES = "beverages"
    COFFEE = "coffee"
    CART_ADD = "cart_add"
    CART_REMOVE = "cart_remove"
    CART_CLEAR = "cart_clear"
    CART_VIEW = "cart_view"
    ORDER_PLACE = "order_place"
    ORDER_TRACK = "order_track"
    FAQ = "faq"
    TIMINGS = "timings"
    OFFERS = "offers"
    AGENT = "agent"


@dataclass(frozen=True)
class RouteResult:
    """Decision returned by route_message."""

    route: Route
    reason: str = ""

    @property
    def is_fast_path(self) -> bool:
        """Return whether the route bypasses the Orchestrator.

        Returns:
            - return bool - Whether this route is deterministic.
        """
        return self.route is not Route.AGENT


Handler = Callable[[str, str, str], str]


def route_message(text: str) -> RouteResult:
    """Route a user message to a deterministic path or the Agent.

    Args:
        - text: str - The raw user message.

    Returns:
        - return RouteResult - The selected route.
    """
    normalized = _normalize(text)

    if _is_simple_greeting(normalized):
        return RouteResult(Route.GREETING, "simple greeting")

    if _looks_like_preference_context(normalized):
        return RouteResult(Route.AGENT, "preference or dietary context")

    if _has_any(
        normalized,
        "clear cart",
        "clear my cart",
        "empty cart",
        "empty my cart",
        "reset cart",
    ):
        return RouteResult(Route.CART_CLEAR, "clear cart")
    if _has_any(normalized, "remove ", "delete "):
        return RouteResult(Route.CART_REMOVE, "remove cart item")
    if _has_any(normalized, "add ", "put "):
        return RouteResult(Route.CART_ADD, "add cart item")
    if _has_any(
        normalized,
        "place order",
        "place my order",
        "checkout",
        "confirm order",
        "order now",
    ):
        return RouteResult(Route.ORDER_PLACE, "place order")
    if _has_any(
        normalized,
        "track order",
        "track my order",
        "order status",
        "where is my order",
    ):
        return RouteResult(Route.ORDER_TRACK, "track order")
    if _has_any(normalized, "view cart", "show cart", "my cart", "cart total"):
        return RouteResult(Route.CART_VIEW, "view cart")

    if _has_any(normalized, "offer", "offers", "discount", "coupon", "promo", "deal"):
        return RouteResult(Route.OFFERS, "offers")
    if _has_any(normalized, "timing", "timings", "hours", "open", "close", "closing"):
        return RouteResult(Route.TIMINGS, "timings")
    if _looks_like_faq(normalized):
        return RouteResult(Route.FAQ, "faq")

    if _has_any(normalized, "category", "categories", "sections"):
        return RouteResult(Route.CATEGORIES, "menu categories")
    if _has_any(
        normalized,
        "beverages",
        "beverage",
        "drinks",
        "cool drinks",
        "cold drinks",
    ):
        return RouteResult(Route.AGENT, "category browsing needs agent context")
    if _has_any(normalized, "coffee", "coffees"):
        return RouteResult(Route.AGENT, "category browsing needs agent context")
    if _is_menu_overview_query(normalized):
        return RouteResult(Route.MENU_BROWSE, "menu browse")
    if _looks_like_menu_query(normalized):
        return RouteResult(Route.AGENT, "menu browsing needs agent context")

    return RouteResult(Route.AGENT, "agent fallback")


async def execute_route(
    route: RouteResult,
    session_id: str,
    user_text: str,
    user_id: str = DEFAULT_USER_ID,
) -> str:
    """Execute a deterministic route.

    Args:
        - route: RouteResult - The selected deterministic route.
        - session_id: str - The active session id.
        - user_text: str - The raw user message.
        - user_id: str - The active user id.

    Returns:
        - return str - The customer-facing reply.
    """
    handler = _HANDLERS[route.route]
    return handler(session_id, user_text, user_id)


async def save_fast_turn(
    *,
    session_id: str,
    user_id: str,
    user_text: str,
    reply: str,
    route: Route,
) -> None:
    """Persist a fast-path visible chat turn for history.

    Args:
        - session_id: str - The active session id.
        - user_id: str - The active user id.
        - user_text: str - The raw user message.
        - reply: str - The assistant reply.
        - route: Route - The deterministic route.

    Returns:
        - return None - This function has no return value.
    """
    memory = load_memory(session_id, user_id=user_id)
    await save_messages(
        memory,
        [
            Msg(
                "user",
                f"[session_id={session_id}] {user_text}",
                "user",
                metadata={"display_text": user_text, "fast_path_route": route.value},
            ),
            Msg(
                "assistant",
                reply,
                "assistant",
                metadata={"fast_path_route": route.value},
            ),
        ],
    )


def schedule_fast_turn_persistence(
    *,
    session_id: str,
    user_id: str,
    user_text: str,
    reply: str,
    route: Route,
) -> None:
    """Schedule best-effort SQL memory persistence for a fast-path turn.

    Args:
        - session_id: str - The active session id.
        - user_id: str - The active user id.
        - user_text: str - The raw user message.
        - reply: str - The assistant reply.
        - route: Route - The deterministic route.

    Returns:
        - return None - This function has no return value.
    """
    schedule_background(
        save_fast_turn(
            session_id=session_id,
            user_id=user_id,
            user_text=user_text,
            reply=reply,
            route=route,
        ),
        name=f"save_fast_turn:{route.value}",
        key=session_task_key(user_id, session_id),
    )


def _greeting(_session_id: str, user_text: str, _user_id: str) -> str:
    """Return a natural deterministic greeting.

    Args:
        - _session_id: str - The active session id.
        - user_text: str - The raw user message.
        - _user_id: str - The active user id.

    Returns:
        - return str - The greeting reply.
    """
    normalized = _normalize(user_text)
    for phrase, reply in GREETING_REPLIES.items():
        if normalized.startswith(phrase):
            return _normalize_response_spacing(reply)
    return _normalize_response_spacing(DEFAULT_GREETING_REPLY)


def _menu_browse(_session_id: str, user_text: str, _user_id: str) -> str:
    """Return a deterministic menu browsing answer.

    Args:
        - _session_id: str - The active session id.
        - user_text: str - The raw user message.
        - _user_id: str - The active user id.

    Returns:
        - return str - The menu reply.
    """
    return _polish_menu_overview(browse_menu_query(user_text).display_text)


def _categories(_session_id: str, _user_text: str, _user_id: str) -> str:
    """Return menu sections.

    Args:
        - _session_id: str - The active session id.
        - _user_text: str - The raw user message.
        - _user_id: str - The active user id.

    Returns:
        - return str - The category reply.
    """
    return _polish_menu_overview(format_menu_categories(include_items=False))


def _cart_add(session_id: str, user_text: str, user_id: str) -> str:
    """Add an item to the in-process cart.

    Args:
        - session_id: str - The active session id.
        - user_text: str - The raw user message.
        - user_id: str - The active user id.

    Returns:
        - return str - The cart reply.
    """
    item_name = _extract_menu_item_name(user_text)
    if item_name is None:
        return _format_clarification(
            "Sure, what would you like me to add?",
            "You can say something like: add 2 Espressos.",
        )

    try:
        item = _menu_item_from_match(item_name)
        quantity = _extract_quantity(user_text)
        cart = cart_service.add_resolved_item(get_store(), session_id, item, quantity)
        schedule_background(
            save_cart_snapshot(session_id, cart.model_copy(deep=True), user_id=user_id),
            name="save_cart_snapshot:add",
            key=session_task_key(user_id, session_id),
        )
    except ValidationError as exc:
        return _format_validation_error(str(exc))

    plural = "s" if quantity > 1 else ""
    return _format_action_with_snapshot(
        f"Done, I added {quantity} {item.name}{plural} to your cart.",
        _format_cart(cart),
    )


def _cart_remove(session_id: str, user_text: str, user_id: str) -> str:
    """Remove an item from the in-process cart.

    Args:
        - session_id: str - The active session id.
        - user_text: str - The raw user message.
        - user_id: str - The active user id.

    Returns:
        - return str - The cart reply.
    """
    cart = cart_service.view_cart(get_store(), session_id)
    item = _match_cart_item(cart, user_text)
    if item is None:
        return _format_clarification("Sure, which item should I remove from your cart?")

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
        return _format_validation_error(str(exc))
    return _format_action_with_snapshot(
        f"Done, I removed {item.name} from your cart.",
        _format_cart(updated),
    )


def _cart_clear(session_id: str, _user_text: str, user_id: str) -> str:
    """Clear the in-process cart.

    Args:
        - session_id: str - The active session id.
        - _user_text: str - The raw user message.
        - user_id: str - The active user id.

    Returns:
        - return str - The cart reply.
    """
    cart_service.clear_cart(get_store(), session_id)
    schedule_background(
        clear_cart_snapshot(session_id, user_id=user_id),
        name="clear_cart_snapshot",
        key=session_task_key(user_id, session_id),
    )
    return "Done, your cart is cleared."


def _cart_view(session_id: str, _user_text: str, user_id: str) -> str:
    """Return the current in-process cart.

    Args:
        - session_id: str - The active session id.
        - _user_text: str - The raw user message.
        - user_id: str - The active user id.

    Returns:
        - return str - The cart reply.
    """
    cart = cart_service.view_cart(get_store(), session_id)
    schedule_background(
        save_cart_snapshot(session_id, cart.model_copy(deep=True), user_id=user_id),
        name="save_cart_snapshot:view",
        key=session_task_key(user_id, session_id),
    )
    return _format_cart(cart)


def _order_place(session_id: str, user_text: str, user_id: str) -> str:
    """Place an order from the active cart.

    Args:
        - session_id: str - The active session id.
        - user_text: str - The raw user message.
        - user_id: str - The active user id.

    Returns:
        - return str - The order reply.
    """
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
        return _format_validation_error(str(exc))
    return _format_order(order, heading=f"Order {order.order_id} is confirmed.")


def _order_track(session_id: str, user_text: str, user_id: str) -> str:
    """Track an order by id or latest session order.

    Args:
        - session_id: str - The active session id.
        - user_text: str - The raw user message.
        - user_id: str - The active user id.

    Returns:
        - return str - The order reply.
    """
    order_id = _extract_order_id(user_text)
    if order_id is None:
        order = _latest_order_for_session(session_id)
        if order is None:
            return _format_clarification(
                "Please share your order id, and I will check the status for you."
            )
    else:
        try:
            order = order_service.get_order(get_store(), order_id)
        except ValidationError as exc:
            return _format_validation_error(str(exc))

    schedule_background(
        save_order_snapshot(order.model_copy(deep=True), user_id=user_id),
        name="save_order_snapshot:track",
        key=session_task_key(user_id, session_id),
    )
    return _format_order(order, heading=f"Order {order.order_id} is {order.status}.")


def _faq(_session_id: str, user_text: str, _user_id: str) -> str:
    """Return a deterministic FAQ answer.

    Args:
        - _session_id: str - The active session id.
        - user_text: str - The raw user message.
        - _user_id: str - The active user id.

    Returns:
        - return str - The FAQ reply.
    """
    try:
        topic, answer = faq_service.lookup_faq(user_text)
        return _format_faq_answer(topic, answer)
    except ValidationError:
        return _format_clarification(
            "I do not have a saved answer for that yet.",
            "I can help with menu, cart, orders, timings, Wi-Fi, payments, "
            "location, and loyalty.",
        )


def _timings(_session_id: str, _user_text: str, _user_id: str) -> str:
    """Return cafe timings.

    Args:
        - _session_id: str - The active session id.
        - _user_text: str - The raw user message.
        - _user_id: str - The active user id.

    Returns:
        - return str - The timings reply.
    """
    _topic, answer = faq_service.lookup_faq("hours")
    return _format_faq_answer("hours", answer)


def _offers(_session_id: str, _user_text: str, _user_id: str) -> str:
    """Return current deterministic offer information.

    Args:
        - _session_id: str - The active session id.
        - _user_text: str - The raw user message.
        - _user_id: str - The active user id.

    Returns:
        - return str - The offers reply.
    """
    _topic, answer = faq_service.lookup_faq("loyalty")
    return (
        "I do not have live promo details connected right now.\n\n"
        "Loyalty benefit:\n"
        f"- {answer}"
    )


def _polish_menu_overview(reply: str) -> str:
    """Make deterministic menu overview replies sound more natural.

    Args:
        - reply: str - The menu service display text.

    Returns:
        - return str - The polished menu text.
    """
    polished = reply.replace(
        "Of course. Here are the menu sections:",
        "Of course, here are the menu sections at Milo:",
        1,
    )
    return _normalize_response_spacing(polished)


def _format_faq_answer(topic: str, answer: str) -> str:
    """Format deterministic FAQ answers with a human heading.

    Args:
        - topic: str - The matched FAQ topic.
        - answer: str - The stored FAQ answer.

    Returns:
        - return str - The customer-facing FAQ answer.
    """
    headings = {
        "hours": "Sure, here are our cafe timings:",
        "wifi": "Yes, Wi-Fi is available:",
        "vegan": "Yes, vegan options are available:",
        "allergens": "A quick allergen note:",
        "payment": "Sure, here are the payment options:",
        "location": "You can find us here:",
        "loyalty": "Here is the loyalty benefit:",
    }
    heading = headings.get(topic, "Sure, here is what I can confirm:")
    return _normalize_response_spacing(f"{heading}\n\n{answer}")


def _format_clarification(*lines: str) -> str:
    """Format a short clarification with readable spacing.

    Args:
        - lines: str - Customer-facing clarification lines.

    Returns:
        - return str - Formatted clarification.
    """
    return _normalize_response_spacing("\n".join(line for line in lines if line))


def _format_validation_error(message: str) -> str:
    """Format deterministic validation errors with a softer tone.

    Args:
        - message: str - The validation message.

    Returns:
        - return str - Customer-facing error text.
    """
    return _normalize_response_spacing(message.strip())


def _format_action_with_snapshot(action: str, snapshot: str) -> str:
    """Join a completed action and the resulting cart/order snapshot.

    Args:
        - action: str - The completed action sentence.
        - snapshot: str - The formatted snapshot.

    Returns:
        - return str - Combined customer-facing text.
    """
    return _normalize_response_spacing(f"{action}\n\n{snapshot}")


def _normalize_response_spacing(text: str) -> str:
    """Normalize blank lines in fast-path responses.

    Args:
        - text: str - The response text.

    Returns:
        - return str - Text with consistent spacing.
    """
    lines = [line.rstrip() for line in text.strip().splitlines()]
    normalized: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and previous_blank:
            continue
        normalized.append(line)
        previous_blank = is_blank
    return "\n".join(normalized).strip()


def _format_cart(cart: Cart) -> str:
    """Format a cart for the customer.

    Args:
        - cart: Cart - The cart to format.

    Returns:
        - return str - The cart text.
    """
    if cart.is_empty():
        return "Your cart is empty right now."

    lines = ["Here is your cart:"]
    for item in cart.items:
        lines.append(
            f"- {item.quantity} x {item.name} - INR {item.unit_price_inr} each "
            f"= INR {item.line_total_inr}"
        )
    lines.append("")
    lines.append(f"Total: INR {cart.total_inr}")
    return _normalize_response_spacing("\n".join(lines))


def _format_order(order, *, heading: str) -> str:
    """Format an order for the customer.

    Args:
        - order: Any - The order to format.
        - heading: str - The heading to show.

    Returns:
        - return str - The order text.
    """
    lines = [heading, "", "Items:"]
    for item in order.items:
        lines.append(f"- {item.quantity} x {item.name} - INR {item.line_total_inr}")
    lines.append("")
    lines.append(f"Total: INR {order.total_inr}")
    return _normalize_response_spacing("\n".join(lines))


def _is_simple_greeting(text: str) -> bool:
    """Return whether text is only a simple greeting.

    Args:
        - text: str - The normalized user message.

    Returns:
        - return bool - Whether this is a greeting.
    """
    greetings = {
        "hi",
        "hii",
        "hello",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
        "namaste",
    }
    words = text.split()
    return text in greetings or (
        len(words) <= 3
        and words
        and words[0] in {"hi", "hii", "hello", "hey", "namaste"}
    )


def _looks_like_menu_query(text: str) -> bool:
    """Return whether text is a known simple menu browse query.

    Args:
        - text: str - The normalized user message.

    Returns:
        - return bool - Whether this can be answered from the menu index.
    """
    padded = f" {text} "
    intent_words = {
        "show",
        "items",
        "item",
        "options",
        "option",
        "have",
        "under",
        "list",
        "menu",
        "available",
    }
    if not (set(text.split()) & intent_words):
        return False
    return any(f" {term} " in padded for term in _menu_search_terms())


def _is_menu_overview_query(text: str) -> bool:
    """Return whether the user wants only a broad menu overview.

    Args:
        - text: str - The normalized user message.

    Returns:
        - return bool - Whether this is safe for the stateless fast path.
    """
    if _looks_like_preference_context(text):
        return False
    overview_phrases = {
        "menu",
        "show menu",
        "show me menu",
        "show me the menu",
        "can you show menu",
        "can you show me the menu",
        "full menu",
        "complete menu",
        "whole menu",
    }
    return text in overview_phrases


def _looks_like_preference_context(text: str) -> bool:
    """Return whether the request contains preference/dietary context.

    Args:
        - text: str - The normalized user message.

    Returns:
        - return bool - Whether this should stay agentic.
    """
    preference_terms = {
        "vegan",
        "vegetarian",
        "veg",
        "jain",
        "allergy",
        "allergic",
        "allergen",
        "lactose",
        "dairy free",
        "no dairy",
        "plant based",
        "diabetic",
        "diabetes",
        "sugar free",
        "low sugar",
        "no sugar",
        "gluten free",
        "no chicken",
        "no meat",
        "halal",
        "eggless",
    }
    return any(term in text for term in preference_terms)


@lru_cache(maxsize=1)
def _menu_search_terms() -> tuple[str, ...]:
    """Build normalized menu terms that can be answered deterministically.

    Returns:
        - return tuple[str, ...] - The searchable menu terms.
    """
    terms: set[str] = set()

    def add(value: str | None) -> None:
        """Add a normalized term.

        Args:
            - value: str | None - The raw term.

        Returns:
            - return None - This function has no return value.
        """
        normalized = _normalize(value or "")
        if len(normalized) < 3:
            return
        terms.add(normalized)
        if normalized.endswith("s") and len(normalized) > 3:
            terms.add(normalized[:-1])

    index = build_menu_index()
    for top_level in index.top_level_categories:
        add(top_level)
    for section in index.sections:
        add(section.name)
        add(section.path[-1])
        for item in section.items:
            add(item)
    for alias in build_menu_match_aliases():
        add(alias)
    for item in build_menu_item_match_index():
        add(item.name)
        add(item.section)
        add(item.top_level)
        add(item.serving)
        add(item.dietary_tags)
        for tag in item.tags:
            add(tag)

    return tuple(sorted(terms, key=len, reverse=True))


def _extract_menu_item_name(user_text: str) -> str | None:
    """Extract a known menu item name from text.

    Args:
        - user_text: str - The raw user message.

    Returns:
        - return str | None - The matched menu item name.
    """
    normalized_text = f" {_normalize(user_text)} "
    for item in _menu_item_candidates():
        normalized_name = _normalize(item.name)
        if f" {normalized_name} " in normalized_text:
            return item.name
    return None


@lru_cache(maxsize=1)
def _menu_item_candidates():
    """Return menu items sorted by longest name first.

    Returns:
        - return tuple[Any, ...] - The sorted menu item matches.
    """
    return tuple(
        sorted(
            build_menu_item_match_index(),
            key=lambda item: len(_normalize(item.name)),
            reverse=True,
        )
    )


@lru_cache(maxsize=1)
def _menu_items_by_name():
    """Return menu items keyed by normalized name.

    Returns:
        - return dict[str, Any] - The menu item mapping.
    """
    return {_normalize(item.name): item for item in build_menu_item_match_index()}


def _menu_item_from_match(item_name: str) -> MenuItem:
    """Create a cart MenuItem from a menu-index match.

    Args:
        - item_name: str - The matched menu item name.

    Returns:
        - return MenuItem - The cart-ready menu item.
    """
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


def _match_cart_item(cart: Cart, user_text: str):
    """Find a cart item mentioned in text.

    Args:
        - cart: Cart - The active cart.
        - user_text: str - The raw user message.

    Returns:
        - return Any - The matched cart item or None.
    """
    normalized_text = f" {_normalize(user_text)} "
    for item in sorted(cart.items, key=lambda line: len(line.name), reverse=True):
        if f" {_normalize(item.name)} " in normalized_text or item.item_id in user_text:
            return item
    return None


def _extract_quantity(user_text: str) -> int:
    """Extract a small quantity from text.

    Args:
        - user_text: str - The raw user message.

    Returns:
        - return int - The requested quantity.
    """
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
    """Extract an optional order budget.

    Args:
        - user_text: str - The raw user message.

    Returns:
        - return int | None - The budget in INR.
    """
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
    """Extract an order id.

    Args:
        - user_text: str - The raw user message.

    Returns:
        - return str | None - The order id.
    """
    if match := re.search(r"\bord-[a-zA-Z0-9-]+\b", user_text):
        return match.group(0)
    return None


def _latest_order_for_session(session_id: str):
    """Return the latest in-process order for a session.

    Args:
        - session_id: str - The active session id.

    Returns:
        - return Any - The latest order or None.
    """
    orders = [
        order for order in get_store().orders.values() if order.session_id == session_id
    ]
    return orders[-1] if orders else None


def _looks_like_faq(text: str) -> bool:
    """Return whether text matches deterministic FAQ topics.

    Args:
        - text: str - The normalized user message.

    Returns:
        - return bool - Whether this is an FAQ.
    """
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
    """Return whether any phrase appears in text.

    Args:
        - text: str - The normalized text.
        - phrases: str - Phrases to search for.

    Returns:
        - return bool - Whether any phrase matched.
    """
    return any(phrase in text for phrase in phrases)


def _first_price_inr(price_text: str | None) -> int | None:
    """Extract the first INR price from text.

    Args:
        - price_text: str | None - The raw price text.

    Returns:
        - return int | None - The parsed price.
    """
    if not price_text:
        return None
    match = re.search(r"\d+", price_text)
    return int(match.group(0)) if match else None


def _menu_item_id(name: str) -> str:
    """Build a stable menu item id.

    Args:
        - name: str - The menu item name.

    Returns:
        - return str - The item id.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return f"menu-{slug}"


def _normalize(text: str) -> str:
    """Normalize text for routing.

    Args:
        - text: str - The raw text.

    Returns:
        - return str - The normalized text.
    """
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


_HANDLERS: dict[Route, Handler] = {
    Route.GREETING: _greeting,
    Route.MENU_BROWSE: _menu_browse,
    Route.CATEGORIES: _categories,
    Route.BEVERAGES: _menu_browse,
    Route.COFFEE: _menu_browse,
    Route.CART_ADD: _cart_add,
    Route.CART_REMOVE: _cart_remove,
    Route.CART_CLEAR: _cart_clear,
    Route.CART_VIEW: _cart_view,
    Route.ORDER_PLACE: _order_place,
    Route.ORDER_TRACK: _order_track,
    Route.FAQ: _faq,
    Route.TIMINGS: _timings,
    Route.OFFERS: _offers,
}
