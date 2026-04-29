import asyncio
from functools import lru_cache
from pathlib import Path

from agentscope.tool import ToolResponse

from cafe.core.state import get_store
from cafe.core.validator import ValidationError
from cafe.models.tool_io import ToolResult
from cafe.services import menu_service
from cafe.services.rag_service import RagHit, build_rag_service, rag_sources
from cafe.tools._wrap import wrap


MENU_DOC_PATH = Path(__file__).resolve().parents[1] / "Docs" / "BTB_Menu_Enhanced.md"
PIZZA_GROUP_HEADINGS = {"Veg Pizzas", "Non-Veg Pizzas"}


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


@lru_cache
def _menu_category_index() -> dict[str, object]:
    text = MENU_DOC_PATH.read_text(encoding="utf-8")
    top_level: dict[str, list[dict[str, object]]] = {}
    categories: list[dict[str, object]] = []
    current: dict[str, object] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if line.startswith("## Beverages > ") or line.startswith("## Food > "):
            path = [part.strip() for part in line[3:].split(">")]
            category = {
                "top_level": path[0],
                "name": " > ".join(path[1:]),
                "path": path,
                "items": [],
            }
            top_level.setdefault(path[0], []).append(category)
            categories.append(category)
            current = category
            continue

        if current is None:
            continue

        if line.startswith("## "):
            current = None
            continue

        if line.startswith("### "):
            item_name = line[4:].strip()
            if item_name not in PIZZA_GROUP_HEADINGS:
                current["items"].append(item_name)
            continue

        if line.startswith("#### "):
            current["items"].append(line[5:].strip())

    return {
        "top_level_categories": list(top_level),
        "categories": [
            {
                "top_level": category["top_level"],
                "name": category["name"],
                "path": category["path"],
                "items": category["items"],
                "item_count": len(category["items"]),
            }
            for category in categories
        ],
        "flat_category_names": [category["name"] for category in categories],
        "aliases": {
            "drinks": "Beverages",
            "drink": "Beverages",
            "beverages": "Beverages",
            "food": "Food",
        },
    }


def get_menu_categories(include_items: bool = True) -> dict[str, object]:
    index = _menu_category_index()
    categories = []
    for category in index["categories"]:
        category_data = dict(category)
        if not include_items:
            category_data.pop("items", None)
        categories.append(category_data)

    return {
        "top_level_categories": index["top_level_categories"],
        "categories": categories,
        "flat_category_names": index["flat_category_names"],
        "aliases": index["aliases"],
    }


def format_menu_categories(include_items: bool = True) -> str:
    data = get_menu_categories(include_items=include_items)
    lines = ["Here is the complete menu category list:"]

    for top_level in data["top_level_categories"]:
        lines.extend(["", f"{top_level}:"])
        for category in data["categories"]:
            if category["top_level"] != top_level:
                continue
            if include_items:
                items = ", ".join(category["items"])
                lines.append(f"- {category['name']}: {items}")
            else:
                lines.append(f"- {category['name']}")

    return "\n".join(lines)


async def list_menu_categories(include_items: bool = True) -> ToolResponse:
    """List menu categories from the canonical menu document.

    Args:
        include_items: When true, include item names under each category.

    Returns:
        ToolResult.ok(display_text=..., top_level_categories=..., categories=...).

    Example:
        list_menu_categories(include_items=True)
    """
    try:
        return wrap(
            ToolResult.ok(
                display_text=format_menu_categories(include_items=include_items),
                **get_menu_categories(include_items=include_items),
            )
        )
    except Exception as e:
        return wrap(ToolResult.fail(f"Menu category index error: {e}"))


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
