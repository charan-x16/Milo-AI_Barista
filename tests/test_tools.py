import json
from types import SimpleNamespace

import pytest

from cafe.tools.cart_tools import add_to_cart, view_cart
from cafe.tools.order_tools import cancel_order, place_order
from cafe.tools import product_tools
from cafe.tools.product_tools import (
    get_product_details,
    search_product_and_attribute_knowledge,
    search_products,
)
from cafe.tools.support_tools import faq_lookup


def payload(resp):
    return json.loads(resp.content[0]["text"])


@pytest.mark.asyncio
async def test_search_products_success(store):
    data = payload(await search_products("coffee"))

    assert data["success"] is True
    assert data["data"]["count"] >= 1


@pytest.mark.asyncio
async def test_get_product_details_success(store):
    data = payload(await get_product_details("m001"))

    assert data["success"] is True
    assert data["data"]["item"]["name"] == "Cappuccino"


@pytest.mark.asyncio
async def test_get_product_details_unknown_returns_failure(store):
    data = payload(await get_product_details("nope"))

    assert data["success"] is False


@pytest.mark.asyncio
async def test_combined_product_attribute_search_returns_both_sources(monkeypatch):
    def fake_retrieve(source_key: str, query: str, max_results: int):
        return [
            SimpleNamespace(
                text=f"{source_key}: {query}",
                score=0.9,
                source=f"{source_key}.md",
                chunk_index=max_results,
            )
        ]

    monkeypatch.setattr(product_tools, "_retrieve_knowledge_source", fake_retrieve)

    data = payload(await search_product_and_attribute_knowledge("sweet light drink", max_results=2))

    assert data["success"] is True
    assert data["data"]["menu_count"] == 1
    assert data["data"]["attribute_count"] == 1
    assert data["data"]["menu_results"][0]["text"] == "product: sweet light drink"
    assert data["data"]["attribute_results"][0]["text"] == "menu_attributes: sweet light drink"


@pytest.mark.asyncio
async def test_add_to_cart_then_view_cart(store):
    await add_to_cart("s001", "m001", quantity=2)
    data = payload(await view_cart("s001"))

    assert data["success"] is True
    assert data["data"]["total_inr"] == 360
    assert data["data"]["item_count"] == 1


@pytest.mark.asyncio
async def test_add_to_cart_quantity_zero_returns_failure(store):
    data = payload(await add_to_cart("s001", "m001", quantity=0))

    assert data["success"] is False


@pytest.mark.asyncio
async def test_place_order_empty_cart_returns_failure(store):
    data = payload(await place_order("s001"))

    assert data["success"] is False
    assert "empty" in data["error"]


@pytest.mark.asyncio
async def test_full_happy_path_add_place_order_cart_empty(store):
    await add_to_cart("s001", "m001", quantity=2)

    order_data = payload(await place_order("s001"))
    cart_data = payload(await view_cart("s001"))

    assert order_data["success"] is True
    assert order_data["data"]["order"]["status"] == "confirmed"
    assert cart_data["success"] is True
    assert cart_data["data"]["cart"]["items"] == []


@pytest.mark.asyncio
async def test_cancel_unknown_order_returns_failure(store):
    data = payload(await cancel_order("ord-nope"))

    assert data["success"] is False


@pytest.mark.asyncio
async def test_faq_lookup_hours_success(store):
    data = payload(await faq_lookup("hours?"))

    assert data["success"] is True
    assert "7 AM" in data["data"]["answer"]


@pytest.mark.asyncio
async def test_faq_lookup_no_match_returns_failure(store):
    data = payload(await faq_lookup("unicorns?"))

    assert data["success"] is False
