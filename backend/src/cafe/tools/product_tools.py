from agentscope.tool import ToolResponse

from cafe.core.state import get_store
from cafe.core.validator import ValidationError
from cafe.models.tool_io import ToolResult
from cafe.services import menu_service
from cafe.services.rag_service import build_rag_service, rag_sources
from cafe.tools._wrap import wrap


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
        source = rag_sources()["product"]
        hits = build_rag_service().retrieve(source.collection_name, query, limit=max_results)
        return wrap(
            ToolResult.ok(
                results=[
                    {
                        "text": hit.text,
                        "score": hit.score,
                        "source": hit.source,
                        "chunk_index": hit.chunk_index,
                    }
                    for hit in hits
                ],
                count=len(hits),
            )
        )
    except Exception as e:
        return wrap(ToolResult.fail(f"RAG retrieval error: {e}"))
