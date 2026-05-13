"""Cafe tools product tools module."""

import asyncio
from contextvars import ContextVar

from agentscope.tool import ToolResponse

from cafe.core.observability import observe_tool
from cafe.core.state import get_store
from cafe.core.validator import ValidationError
from cafe.models.tool_io import ToolResult
from cafe.services import menu_service
from cafe.services.menu_index_service import (
    browse_menu_query,
    build_menu_item_match_index,
    extract_price_limit,
    filter_price_items,
    format_menu_categories,
    format_menu_item_matches,
    format_menu_recommendations,
    format_menu_section_items,
    format_price_filter_query,
    format_price_list_query,
    get_menu_categories,
    is_context_dependent_price_request,
    is_price_list_request,
    price_items_for_query,
    recommend_menu_items,
    requested_section_from_query,
    search_menu_item_matches,
)
from cafe.services.rag_service import RagHit, build_rag_service, rag_sources
from cafe.tools._wrap import wrap

_CURRENT_PRODUCT_QUERY: ContextVar[str | None] = ContextVar(
    "current_product_query",
    default=None,
)
_CURRENT_SESSION_ID: ContextVar[str | None] = ContextVar(
    "current_product_session_id",
    default=None,
)


def set_current_product_query(query: str):
    """Set the current product query.

    Args:
        - query: str - The query value.

    Returns:
        - return Any - The return value.
    """
    return _CURRENT_PRODUCT_QUERY.set(query)


def reset_current_product_query(token) -> None:
    """Reset the current product query.

    Args:
        - token: Any - The token value.

    Returns:
        - return None - The return value.
    """
    _CURRENT_PRODUCT_QUERY.reset(token)


def set_current_product_session_id(session_id: str | None):
    """Set the current product session id.

    Args:
        - session_id: str | None - The session id value.

    Returns:
        - return Any - The return value.
    """
    return _CURRENT_SESSION_ID.set(session_id)


def reset_current_product_session_id(token) -> None:
    """Reset the current product session id.

    Args:
        - token: Any - The token value.

    Returns:
        - return None - The return value.
    """
    _CURRENT_SESSION_ID.reset(token)


def _remember_menu_scope(query: str) -> None:
    """Handle remember menu scope.

    Args:
        - query: str - The query value.

    Returns:
        - return None - The return value.
    """
    session_id = _CURRENT_SESSION_ID.get()
    if not session_id:
        return
    section = requested_section_from_query(query)
    if section:
        get_store().last_menu_scope[session_id] = section


def _query_with_last_scope(query: str) -> str:
    """Handle query with last scope.

    Args:
        - query: str - The query value.

    Returns:
        - return str - The return value.
    """
    session_id = _CURRENT_SESSION_ID.get()
    if (
        session_id
        and is_context_dependent_price_request(query)
        and (last_scope := get_store().last_menu_scope.get(session_id))
    ):
        return f"{query} for {last_scope}"
    return query


def _active_product_preferences(query: str) -> set[str]:
    """Return active dietary preferences for the current product request.

    Args:
        - query: str - The current product query.

    Returns:
        - return set[str] - Active preference labels.
    """
    preferences: set[str] = set()
    session_id = _CURRENT_SESSION_ID.get()
    if session_id:
        preferences.update(get_store().session_preferences.get(session_id, set()))

    normalized = query.casefold()
    checks = {
        "vegan": ("vegan", "plant based"),
        "vegetarian": ("vegetarian", "veg ", "pure veg"),
        "no chicken": ("no chicken", "without chicken"),
        "no meat": ("no meat", "without meat"),
        "diabetic": ("diabetic", "low sugar", "no sugar", "sugar free"),
    }
    padded = f" {normalized} "
    for label, terms in checks.items():
        if any(term in padded for term in terms):
            preferences.add(label)
    return preferences


def _dietary_text(item) -> str:
    """Return normalized dietary text for a menu item match.

    Args:
        - item: Any - The menu item match.

    Returns:
        - return str - Normalized dietary text.
    """
    parts = [item.dietary_tags or "", " ".join(item.tags or ())]
    return " ".join(parts).casefold()


def _item_matches_preferences(item, preferences: set[str]) -> bool:
    """Return whether an item satisfies active preferences.

    Args:
        - item: Any - The menu item match.
        - preferences: set[str] - Active preference labels.

    Returns:
        - return bool - Whether the item satisfies the preferences.
    """
    dietary = _dietary_text(item)
    if "vegan" in preferences:
        return "vegan" in dietary
    if preferences & {"vegetarian", "no chicken", "no meat"}:
        return "non-vegetarian" not in dietary and "chicken" not in dietary
    return True


def _closest_preference_alternatives(items: tuple, preferences: set[str]) -> tuple:
    """Return safe nearby alternatives when strict matches are unavailable.

    Args:
        - items: tuple - Scoped menu items.
        - preferences: set[str] - Active preference labels.

    Returns:
        - return tuple - Nearby alternatives.
    """
    if "vegan" in preferences:
        return tuple(
            item
            for item in items
            if "non-vegetarian" not in _dietary_text(item)
            and "chicken" not in _dietary_text(item)
        )
    return tuple()


def _format_preference_item_line(item) -> str:
    """Format one preference-scoped item line.

    Args:
        - item: Any - The menu item match.

    Returns:
        - return str - The formatted item line.
    """
    details = []
    if item.price:
        details.append(f"INR {item.price}")
    if item.dietary_tags:
        details.append(item.dietary_tags)
    suffix = f" ({'; '.join(details)})" if details else ""
    return f"- {item.name}{suffix}"


def _preference_heading(preferences: set[str], section: str | None) -> str:
    """Return the heading for a preference-scoped browse response.

    Args:
        - preferences: set[str] - Active preference labels.
        - section: str | None - Requested menu section.

    Returns:
        - return str - Customer-facing heading.
    """
    label = "vegan" if "vegan" in preferences else "preference-friendly"
    target = f" under {section}" if section else ""
    return f"Here are the {label} options{target}:"


def _format_preference_scoped_browse(query: str, preferences: set[str]) -> dict | None:
    """Return a preference-aware menu browse payload when needed.

    Args:
        - query: str - The current product query.
        - preferences: set[str] - Active preference labels.

    Returns:
        - return dict | None - Tool payload override, if applicable.
    """
    if not preferences:
        return None

    supported = {"vegan", "vegetarian", "no chicken", "no meat"}
    if not (preferences & supported):
        return None

    section = requested_section_from_query(query)
    items = tuple(build_menu_item_match_index())
    if section:
        scoped_items = tuple(
            item for item in items if item.section.casefold() == section.casefold()
        )
    else:
        scoped_items = items

    if not scoped_items:
        return None

    matches = tuple(
        item for item in scoped_items if _item_matches_preferences(item, preferences)
    )
    if matches:
        lines = [_preference_heading(preferences, section)]
        lines.extend(_format_preference_item_line(item) for item in matches)
        return {
            "display_text": "\n".join(lines),
            "items": [item.as_dict() for item in matches],
            "count": len(matches),
            "response_kind": "preference_scoped_browse",
            "passthrough": True,
            "preferences": sorted(preferences),
        }

    alternatives = _closest_preference_alternatives(scoped_items, preferences)
    if "vegan" in preferences and alternatives:
        target = f"items under {section}" if section else "items for that request"
        lines = [
            f"I do not see any {target} marked Vegan on the current menu.",
            "",
            "Closest vegetarian options, not confirmed vegan:",
        ]
        lines.extend(_format_preference_item_line(item) for item in alternatives)
        lines.extend(
            [
                "",
                "Please check cheese, dairy, and toppings before ordering.",
            ]
        )
        return {
            "display_text": "\n".join(lines),
            "items": [item.as_dict() for item in alternatives],
            "count": len(alternatives),
            "response_kind": "preference_scoped_browse",
            "passthrough": True,
            "preferences": sorted(preferences),
            "strict_match_count": 0,
        }

    target = f" under {section}" if section else ""
    return {
        "display_text": f"No matching preference-friendly items are available{target}.",
        "items": [],
        "count": 0,
        "response_kind": "preference_scoped_browse",
        "passthrough": False,
        "preferences": sorted(preferences),
    }


def _serialize_hits(hits: list[RagHit]) -> list[dict[str, object]]:
    """Handle serialize hits.

    Args:
        - hits: list[RagHit] - The hits value.

    Returns:
        - return list[dict[str, object]] - The return value.
    """
    return [
        {
            "text": hit.text,
            "score": hit.score,
            "source": hit.source,
            "chunk_index": hit.chunk_index,
        }
        for hit in hits
    ]


def _retrieve_knowledge_source(
    source_key: str, query: str, max_results: int
) -> list[RagHit]:
    """Handle retrieve knowledge source.

    Args:
        - source_key: str - The source key value.
        - query: str - The query value.
        - max_results: int - The max results value.

    Returns:
        - return list[RagHit] - The return value.
    """
    source = rag_sources()[source_key]
    return build_rag_service().retrieve(
        source.collection_name, query, limit=max_results
    )


@observe_tool("browse_menu")
async def browse_menu(query: str, include_items: bool | None = None) -> ToolResponse:
    """Browse the menu index from a natural customer request.

    Args:
        - query: str - The query value.
        - include_items: bool | None - The include items value.

    Returns:
        - return ToolResponse - The return value.
    """
    try:
        browse_result = browse_menu_query(query, include_items=include_items)
        return wrap(
            ToolResult.ok(
                **browse_result.as_dict(),
            )
        )
    except Exception as e:
        return wrap(ToolResult.fail(f"Menu browse error: {e}"))


@observe_tool("browse_current_menu_request")
async def browse_current_menu_request(
    include_items: bool | None = None,
) -> ToolResponse:
    """Browse the menu using the original Product Search request.

    Args:
        - include_items: bool | None - The include items value.

    Returns:
        - return ToolResponse - The return value.
    """
    query = _CURRENT_PRODUCT_QUERY.get()
    if not query:
        return wrap(ToolResult.fail("No active Product Search request to browse."))

    preferences = _active_product_preferences(query)
    if preference_payload := _format_preference_scoped_browse(query, preferences):
        _remember_menu_scope(query)
        return wrap(ToolResult.ok(**preference_payload))

    response = await browse_menu(query=query, include_items=include_items)
    _remember_menu_scope(query)
    return response


@observe_tool("filter_current_menu_by_price")
async def filter_current_menu_by_price() -> ToolResponse:
    """Filter menu items by the price limit in the original Product Search request.

    Returns:
        - return ToolResponse - The return value.
    """
    query = _CURRENT_PRODUCT_QUERY.get()
    if not query:
        return wrap(ToolResult.fail("No active Product Search request to filter."))

    query = _query_with_last_scope(query)
    max_price = extract_price_limit(query)
    if max_price is None:
        return wrap(ToolResult.fail("No price limit found in the current request."))

    items = filter_price_items(max_price=max_price, query=query)
    return wrap(
        ToolResult.ok(
            display_text=format_price_filter_query(query, max_price=max_price),
            max_price=max_price,
            items=[item.as_dict() for item in items],
            count=len(items),
        )
    )


@observe_tool("list_current_menu_prices")
async def list_current_menu_prices() -> ToolResponse:
    """List menu prices for the original Product Search request.

    Returns:
        - return ToolResponse - The return value.
    """
    query = _CURRENT_PRODUCT_QUERY.get()
    if not query:
        return wrap(ToolResult.fail("No active Product Search request to price."))

    query = _query_with_last_scope(query)
    if not is_price_list_request(query):
        return wrap(
            ToolResult.fail(
                "The current request does not ask for prices. "
                "Use browse_current_menu_request for menu browsing."
            )
        )

    items = price_items_for_query(query)
    return wrap(
        ToolResult.ok(
            display_text=format_price_list_query(query),
            items=[item.as_dict() for item in items],
            count=len(items),
        )
    )


@observe_tool("find_current_menu_matches")
async def find_current_menu_matches(max_results: int = 5) -> ToolResponse:
    """Find canonical menu items that match the current product request.

    Args:
        - max_results: int - The max results value.

    Returns:
        - return ToolResponse - The return value.
    """
    query = _CURRENT_PRODUCT_QUERY.get()
    if not query:
        return wrap(ToolResult.fail("No active Product Search request to match."))

    try:
        items = search_menu_item_matches(query, max_results=max_results)
        return wrap(
            ToolResult.ok(
                display_text=format_menu_item_matches(query, max_results=max_results),
                items=[item.as_dict() for item in items],
                count=len(items),
                response_kind="item_matches",
                passthrough=bool(items),
            )
        )
    except Exception as e:
        return wrap(ToolResult.fail(f"Menu item match error: {e}"))


@observe_tool("recommend_current_menu_items")
async def recommend_current_menu_items(max_results: int = 5) -> ToolResponse:
    """Return data-derived representative menu recommendations.

    Args:
        - max_results: int - The max results value.

    Returns:
        - return ToolResponse - The return value.
    """
    try:
        items = recommend_menu_items(max_results=max_results)
        return wrap(
            ToolResult.ok(
                display_text=format_menu_recommendations(max_results=max_results),
                items=[item.as_dict() for item in items],
                count=len(items),
                response_kind="recommendations",
                passthrough=bool(items),
            )
        )
    except Exception as e:
        return wrap(ToolResult.fail(f"Menu recommendation error: {e}"))


@observe_tool("list_menu_categories")
async def list_menu_categories(
    include_items: bool = False,
    include_structured: bool = False,
) -> ToolResponse:
    """List menu categories from the canonical menu document.

    Args:
        - include_items: bool - The include items value.
        - include_structured: bool - The include structured value.

    Returns:
        - return ToolResponse - The return value.
    """
    try:
        data = {"display_text": format_menu_categories(include_items=include_items)}
        if include_structured:
            data.update(get_menu_categories(include_items=include_items))
        return wrap(ToolResult.ok(**data))
    except Exception as e:
        return wrap(ToolResult.fail(f"Menu category index error: {e}"))


@observe_tool("list_menu_section_items")
async def list_menu_section_items(section_name: str) -> ToolResponse:
    """List item names inside one menu section or section group.

    Args:
        - section_name: str - The section name value.

    Returns:
        - return ToolResponse - The return value.
    """
    try:
        return wrap(ToolResult.ok(display_text=format_menu_section_items(section_name)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Menu section item error: {e}"))


@observe_tool("search_products")
async def search_products(query: str, max_results: int = 5) -> ToolResponse:
    """Search the menu.

    Args:
        - query: str - The query value.
        - max_results: int - The max results value.

    Returns:
        - return ToolResponse - The return value.
    """
    try:
        items = menu_service.search_menu(get_store(), query, max_results)
        return wrap(
            ToolResult.ok(items=[item.model_dump() for item in items], count=len(items))
        )
    except ValidationError as e:
        return wrap(ToolResult.fail(str(e)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Unexpected error: {e}"))


@observe_tool("get_product_details")
async def get_product_details(item_id: str) -> ToolResponse:
    """Full details for a single menu item by id.

    Args:
        - item_id: str - The item id value.

    Returns:
        - return ToolResponse - The return value.
    """
    try:
        item = menu_service.get_item(get_store(), item_id)
        return wrap(ToolResult.ok(item=item.model_dump()))
    except ValidationError as e:
        return wrap(ToolResult.fail(str(e)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Unexpected error: {e}"))


@observe_tool("search_product_knowledge")
async def search_product_knowledge(query: str, max_results: int = 5) -> ToolResponse:
    """Retrieve menu knowledge from the product Qdrant collection.

    Args:
        - query: str - The query value.
        - max_results: int - The max results value.

    Returns:
        - return ToolResponse - The return value.
    """
    try:
        hits = _retrieve_knowledge_source("product", query, max_results)
        return wrap(ToolResult.ok(results=_serialize_hits(hits), count=len(hits)))
    except Exception as e:
        return wrap(ToolResult.fail(f"RAG retrieval error: {e}"))


@observe_tool("search_menu_attribute_knowledge")
async def search_menu_attribute_knowledge(
    query: str, max_results: int = 5
) -> ToolResponse:
    """Retrieve taste, ingredient, allergen, and suitability attributes.

    Args:
        - query: str - The query value.
        - max_results: int - The max results value.

    Returns:
        - return ToolResponse - The return value.
    """
    try:
        hits = _retrieve_knowledge_source("menu_attributes", query, max_results)
        return wrap(ToolResult.ok(results=_serialize_hits(hits), count=len(hits)))
    except Exception as e:
        return wrap(ToolResult.fail(f"RAG retrieval error: {e}"))


@observe_tool("search_product_and_attribute_knowledge")
async def search_product_and_attribute_knowledge(
    query: str, max_results: int = 5
) -> ToolResponse:
    """Retrieve menu facts and menu attributes in parallel.

    Args:
        - query: str - The query value.
        - max_results: int - The max results value.

    Returns:
        - return ToolResponse - The return value.
    """
    try:
        menu_hits, attribute_hits = await asyncio.gather(
            asyncio.to_thread(
                _retrieve_knowledge_source, "product", query, max_results
            ),
            asyncio.to_thread(
                _retrieve_knowledge_source, "menu_attributes", query, max_results
            ),
        )
        return wrap(
            ToolResult.ok(
                menu_results=_serialize_hits(menu_hits),
                menu_count=len(menu_hits),
                attribute_results=_serialize_hits(attribute_hits),
                attribute_count=len(attribute_hits),
            )
        )
    except Exception as e:
        return wrap(ToolResult.fail(f"RAG retrieval error: {e}"))
