import asyncio
from contextvars import ContextVar

from agentscope.tool import ToolResponse

from cafe.core.state import get_store
from cafe.core.validator import ValidationError
from cafe.models.tool_io import ToolResult
from cafe.services import menu_service
from cafe.services.menu_index_service import (
    extract_price_limit,
    filter_price_items,
    format_price_list_query,
    format_price_filter_query,
    browse_menu_query,
    format_menu_categories,
    format_menu_item_matches,
    format_menu_recommendations,
    format_menu_section_items,
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
    return _CURRENT_PRODUCT_QUERY.set(query)


def reset_current_product_query(token) -> None:
    _CURRENT_PRODUCT_QUERY.reset(token)


def set_current_product_session_id(session_id: str | None):
    return _CURRENT_SESSION_ID.set(session_id)


def reset_current_product_session_id(token) -> None:
    _CURRENT_SESSION_ID.reset(token)


def _remember_menu_scope(query: str) -> None:
    session_id = _CURRENT_SESSION_ID.get()
    if not session_id:
        return
    section = requested_section_from_query(query)
    if section:
        get_store().last_menu_scope[session_id] = section


def _query_with_last_scope(query: str) -> str:
    session_id = _CURRENT_SESSION_ID.get()
    if (
        session_id
        and is_context_dependent_price_request(query)
        and (last_scope := get_store().last_menu_scope.get(session_id))
    ):
        return f"{query} for {last_scope}"
    return query


def _serialize_hits(hits: list[RagHit]) -> list[dict[str, object]]:
    return [
        {
            "text": hit.text,
            "score": hit.score,
            "source": hit.source,
            "chunk_index": hit.chunk_index,
        }
        for hit in hits
    ]


def _retrieve_knowledge_source(source_key: str, query: str, max_results: int) -> list[RagHit]:
    source = rag_sources()[source_key]
    return build_rag_service().retrieve(source.collection_name, query, limit=max_results)


async def browse_menu(query: str, include_items: bool | None = None) -> ToolResponse:
    """Browse the menu index from a natural customer request.

    Args:
        query: The user's menu browsing request, such as "show the menu",
            "show me the coffees", "mocktails", or "show all coffee options".
        include_items: Optional override. Use null/default for automatic
            routing: first menu browsing shows sections; named sections show
            their items; explicit detailed whole-menu requests show items.

    Returns:
        ToolResult.ok(display_text=...) with the exact customer-facing menu
        browsing response.

    Example:
        browse_menu(query="show me the coffees")
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


async def browse_current_menu_request(include_items: bool | None = None) -> ToolResponse:
    """Browse the menu using the original Product Search request.

    This avoids lossy tool arguments from the LLM. If the user asks "show pizza
    options", the tool uses that exact request even if the model would have
    paraphrased it.

    Args:
        include_items: Optional override for whole-menu item display.

    Returns:
        ToolResult.ok(display_text=...) with the exact customer-facing menu
        browsing response.

    Example:
        browse_current_menu_request()
    """
    query = _CURRENT_PRODUCT_QUERY.get()
    if not query:
        return wrap(ToolResult.fail("No active Product Search request to browse."))

    response = await browse_menu(query=query, include_items=include_items)
    _remember_menu_scope(query)
    return response


async def filter_current_menu_by_price() -> ToolResponse:
    """Filter menu items by the price limit in the original Product Search request.

    Use this for budget requests such as "items under 100", "drinks below 200",
    or "food under INR 300". The price limit and optional category scope are
    parsed from the original request so the model does not have to do numeric
    filtering from retrieved chunks.

    Returns:
        ToolResult.ok(display_text=..., items=..., max_price=...).

    Example:
        filter_current_menu_by_price()
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


async def list_current_menu_prices() -> ToolResponse:
    """List menu prices for the original Product Search request.

    Use this when the user asks for prices without a max/min budget, such as
    "show prices", "prices for all coffees", "pizza prices", or a follow-up
    like "show the prices for all".

    Returns:
        ToolResult.ok(display_text=..., items=..., count=...).

    Example:
        list_current_menu_prices()
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


async def find_current_menu_matches(max_results: int = 5) -> ToolResponse:
    """Find canonical menu items that match the current product request.

    Use this for concept or preference requests that are not exact menu
    sections, such as "any desserts", "something sweet", "light drinks",
    "chocolate options", or "creamy coffee". It searches structured item
    names, sections, tags, serving notes, dietary tags, descriptions, and
    match aliases from the canonical menu document.

    Args:
        max_results: Maximum number of matching menu items to return.

    Returns:
        ToolResult.ok(display_text=..., items=..., count=...).

    Example:
        find_current_menu_matches(max_results=4)
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


async def recommend_current_menu_items(max_results: int = 5) -> ToolResponse:
    """Return data-derived representative menu recommendations.

    The selection is generated from the canonical menu document by alternating
    through top-level groups and sections in document order. It does not use
    manually selected item names or category names.

    Args:
        max_results: Maximum number of menu items to return.

    Returns:
        ToolResult.ok(display_text=..., items=..., count=...).

    Example:
        recommend_current_menu_items(max_results=5)
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


async def list_menu_categories(
    include_items: bool = False,
    include_structured: bool = False,
) -> ToolResponse:
    """List menu categories from the canonical menu document.

    Args:
        include_items: When true, include item names under each category.
            The default is false so first menu-browsing replies show sections
            only.
        include_structured: When true, include structured category data.

    Returns:
        ToolResult.ok(display_text=...) by default. Structured category data is
        included only when include_structured is true.

    Example:
        list_menu_categories(include_items=False)
    """
    try:
        data = {"display_text": format_menu_categories(include_items=include_items)}
        if include_structured:
            data.update(get_menu_categories(include_items=include_items))
        return wrap(ToolResult.ok(**data))
    except Exception as e:
        return wrap(ToolResult.fail(f"Menu category index error: {e}"))


async def list_menu_section_items(section_name: str) -> ToolResponse:
    """List item names inside one menu section or section group.

    Args:
        section_name: Section name from the menu, such as "Coffees",
            "Mocktails", "Wraps", or a group alias like "drinks".

    Returns:
        ToolResult.ok(display_text=...) with the matching section item list.

    Example:
        list_menu_section_items(section_name="Coffees")
    """
    try:
        return wrap(ToolResult.ok(display_text=format_menu_section_items(section_name)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Menu section item error: {e}"))


async def search_products(query: str, max_results: int = 5) -> ToolResponse:
    """Search the menu.

    Args:
        query: Search text to match against item name, category, or tags.
        max_results: Maximum number of matching products to return.

    Returns:
        ToolResult.ok(items=..., count=...) or ToolResult.fail(error=...).

    Example:
        search_products(query="coffee", max_results=5)
    """
    try:
        items = menu_service.search_menu(get_store(), query, max_results)
        return wrap(ToolResult.ok(items=[item.model_dump() for item in items], count=len(items)))
    except ValidationError as e:
        return wrap(ToolResult.fail(str(e)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Unexpected error: {e}"))


async def get_product_details(item_id: str) -> ToolResponse:
    """Full details for a single menu item by id.

    Args:
        item_id: Menu item id (e.g. 'm001').

    Returns:
        ToolResult.ok(item=...) or ToolResult.fail(error=...).

    Example:
        get_product_details(item_id="m001")
    """
    try:
        item = menu_service.get_item(get_store(), item_id)
        return wrap(ToolResult.ok(item=item.model_dump()))
    except ValidationError as e:
        return wrap(ToolResult.fail(str(e)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Unexpected error: {e}"))


async def search_product_knowledge(query: str, max_results: int = 5) -> ToolResponse:
    """Retrieve menu knowledge from the product Qdrant collection.

    Args:
        query: Natural-language menu question.
        max_results: Maximum number of chunks to return.

    Returns:
        ToolResult.ok(results=..., count=...) or ToolResult.fail(error=...).

    Example:
        search_product_knowledge(query="vegan drinks under 300", max_results=3)
    """
    try:
        hits = _retrieve_knowledge_source("product", query, max_results)
        return wrap(ToolResult.ok(results=_serialize_hits(hits), count=len(hits)))
    except Exception as e:
        return wrap(ToolResult.fail(f"RAG retrieval error: {e}"))


async def search_menu_attribute_knowledge(query: str, max_results: int = 5) -> ToolResponse:
    """Retrieve taste, ingredient, allergen, and suitability attributes.

    Args:
        query: Natural-language menu attribute question.
        max_results: Maximum number of chunks to return.

    Returns:
        ToolResult.ok(results=..., count=...) or ToolResult.fail(error=...).

    Example:
        search_menu_attribute_knowledge(query="sweet light drink without milk", max_results=3)
    """
    try:
        hits = _retrieve_knowledge_source("menu_attributes", query, max_results)
        return wrap(ToolResult.ok(results=_serialize_hits(hits), count=len(hits)))
    except Exception as e:
        return wrap(ToolResult.fail(f"RAG retrieval error: {e}"))


async def search_product_and_attribute_knowledge(query: str, max_results: int = 5) -> ToolResponse:
    """Retrieve menu facts and menu attributes in parallel.

    Args:
        query: Natural-language product recommendation or matching question.
        max_results: Maximum chunks to return from each collection.

    Returns:
        ToolResult.ok(menu_results=..., attribute_results=...) or ToolResult.fail(error=...).

    Example:
        search_product_and_attribute_knowledge(query="sweet but light cold drink", max_results=3)
    """
    try:
        menu_hits, attribute_hits = await asyncio.gather(
            asyncio.to_thread(_retrieve_knowledge_source, "product", query, max_results),
            asyncio.to_thread(_retrieve_knowledge_source, "menu_attributes", query, max_results),
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
