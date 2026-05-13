"""Tests test tools module."""

import json
from types import SimpleNamespace

import pytest
from agentscope.message import TextBlock
from agentscope.tool import ToolResponse
from sqlalchemy import func, select

from cafe.agents.memory import storage as memory_storage
from cafe.agents.specialists.product_search_agent import _menu_answer_postprocess
from cafe.tools import product_tools
from cafe.tools.cart_tools import add_to_cart, view_cart
from cafe.tools.order_tools import cancel_order, place_order
from cafe.tools.product_tools import (
    browse_current_menu_request,
    browse_menu,
    filter_current_menu_by_price,
    find_current_menu_matches,
    format_menu_categories,
    format_menu_section_items,
    get_product_details,
    list_current_menu_prices,
    list_menu_categories,
    list_menu_section_items,
    recommend_current_menu_items,
    reset_current_product_query,
    reset_current_product_session_id,
    search_product_and_attribute_knowledge,
    search_products,
    set_current_product_query,
    set_current_product_session_id,
)
from cafe.tools.support_tools import faq_lookup


def payload(resp):
    """Verify payload.

    Args:
        - resp: Any - The resp value.

    Returns:
        - return Any - The return value.
    """
    return json.loads(resp.content[0]["text"])


def test_product_agent_menu_tool_postprocess_renders_final_answer_data():
    """Verify product agent menu tool postprocess renders final answer data.

    Returns:
        - return None - The return value.
    """
    tool_response = ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=json.dumps(
                    {
                        "success": True,
                        "data": {
                            "display_text": "Of course. Here are the menu sections:\n- Coffees",
                            "response_kind": "menu_sections",
                        },
                        "error": None,
                    }
                ),
            )
        ]
    )

    processed = _menu_answer_postprocess({}, tool_response)

    assert processed is not None
    text = processed.content[0]["text"]
    assert "FINAL_ANSWER_DATA:" in text
    assert "Of course. Here are the menu sections:" in text
    assert "Do not convert the list into prose or inline text" in text
    assert "Keep the heading, blank lines, top-level headings, and bullet" in text


def test_product_agent_section_tool_postprocess_keeps_list_style():
    """Verify product agent section tool postprocess keeps list style.

    Returns:
        - return None - The return value.
    """
    tool_response = ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=json.dumps(
                    {
                        "success": True,
                        "data": {
                            "display_text": "Here are the items under Pizzas:\n- Margherita Pizza",
                            "response_kind": "section_items",
                        },
                        "error": None,
                    }
                ),
            )
        ]
    )

    processed = _menu_answer_postprocess({}, tool_response)

    assert processed is not None
    text = processed.content[0]["text"]
    assert "Keep the direct heading and list formatting" in text
    assert "Do not start with 'I found'" in text


@pytest.mark.asyncio
async def test_search_products_success(store):
    """Verify search products success.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    data = payload(await search_products("coffee"))

    assert data["success"] is True
    assert data["data"]["count"] >= 1


@pytest.mark.asyncio
async def test_get_product_details_success(store):
    """Verify get product details success.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    data = payload(await get_product_details("m001"))

    assert data["success"] is True
    assert data["data"]["item"]["name"] == "Cappuccino"


@pytest.mark.asyncio
async def test_get_product_details_unknown_returns_failure(store):
    """Verify get product details unknown returns failure.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    data = payload(await get_product_details("nope"))

    assert data["success"] is False


@pytest.mark.asyncio
async def test_list_menu_categories_includes_beverages_and_food_items(store):
    """Verify list menu categories includes beverages and food items.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    data = payload(
        await list_menu_categories(include_items=True, include_structured=True)
    )

    assert data["success"] is True
    assert "display_text" in data["data"]
    assert "Coffee Fusions" in data["data"]["display_text"]
    category_names = data["data"]["flat_category_names"]
    assert "Coffees" in category_names
    assert "Mocktails" in category_names
    assert "Pizzas" in category_names

    categories = data["data"]["categories"]
    coffees = next(category for category in categories if category["name"] == "Coffees")
    pizzas = next(category for category in categories if category["name"] == "Pizzas")
    assert "Espresso" in coffees["items"]
    assert "Kentucky Crunch Chicken Pizza" in pizzas["items"]


def test_format_menu_categories_includes_all_sections_and_items(store):
    """Verify format menu categories includes all sections and items.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    text = format_menu_categories(include_items=True)

    assert "Beverages:" in text
    assert "- Coffee Fusions:" in text
    assert "- Cold Brews:" in text
    assert "- Cold Coffees:" in text
    assert "- Appetizers > French Fries:" in text
    assert "Kentucky Crunch Chicken Pizza" in text


def test_format_menu_categories_defaults_to_sections_only(store):
    """Verify format menu categories defaults to sections only.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    text = format_menu_categories()

    assert "Here are the menu sections:" in text
    assert "- Mocktails" in text
    assert "- Pizzas" in text
    assert "Virgin Mojito" not in text
    assert "Kentucky Crunch Chicken Pizza" not in text


def test_format_menu_section_items_returns_named_section_items(store):
    """Verify format menu section items returns named section items.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    text = format_menu_section_items("Coffees")

    assert "Here are the items under Coffees:" in text
    assert "- Espresso" in text
    assert "- Affogato" in text
    assert "Tonic Espresso" not in text


def test_format_menu_section_items_supports_group_aliases(store):
    """Verify format menu section items supports group aliases.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    text = format_menu_section_items("coffee")

    assert "Absolutely. Here are the matching sections for coffee:" in text
    assert "Coffees:" in text
    assert "Coffee Fusions:" in text
    assert "Cold Brews:" in text
    assert "Cold Coffees:" in text
    assert "- Brownie Cold Coffee" in text


@pytest.mark.asyncio
async def test_list_menu_section_items_returns_display_text(store):
    """Verify list menu section items returns display text.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    data = payload(await list_menu_section_items("Mocktails"))

    assert data["success"] is True
    assert "Here are the items under Mocktails:" in data["data"]["display_text"]
    assert "- Virgin Mojito" in data["data"]["display_text"]


@pytest.mark.asyncio
async def test_browse_menu_routes_to_section_items(store):
    """Verify browse menu routes to section items.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    data = payload(await browse_menu("show me the coffees"))

    assert data["success"] is True
    assert "Here are the items under Coffees:" in data["data"]["display_text"]
    assert "- Espresso" in data["data"]["display_text"]


@pytest.mark.asyncio
async def test_browse_current_menu_request_uses_original_product_query(store):
    """Verify browse current menu request uses original product query.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    token = set_current_product_query("Show pizza options")
    try:
        data = payload(await browse_current_menu_request())
    finally:
        reset_current_product_query(token)

    assert data["success"] is True
    assert "Here are the items under Pizzas:" in data["data"]["display_text"]
    assert "- Kentucky Crunch Chicken Pizza" in data["data"]["display_text"]


@pytest.mark.asyncio
async def test_browse_current_menu_request_routes_cold_beverages(store):
    """Verify browse current menu request routes cold beverages.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    token = set_current_product_query("show me the cold beverages")
    try:
        data = payload(await browse_current_menu_request())
    finally:
        reset_current_product_query(token)

    assert data["success"] is True
    display_text = data["data"]["display_text"]
    assert data["data"]["passthrough"] is True
    assert data["data"]["requested_section"] == "cold beverages"
    assert "Cold Brews:" in display_text
    assert "Cold Coffees:" in display_text
    assert "Shakes:" in display_text
    assert "Iced Teas:" in display_text
    assert "Mocktails:" in display_text
    assert "\nCoffees:" not in display_text
    assert "Hot Chocolate" not in display_text


@pytest.mark.asyncio
async def test_browse_current_menu_request_routes_cool_drinks(store):
    """Verify browse current menu request routes cool drinks.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    token = set_current_product_query("show me the cool drinks")
    try:
        data = payload(await browse_current_menu_request())
    finally:
        reset_current_product_query(token)

    assert data["success"] is True
    display_text = data["data"]["display_text"]
    assert data["data"]["passthrough"] is True
    assert data["data"]["requested_section"] == "cool drinks"
    assert "Cold Brews:" in display_text
    assert "Cold Coffees:" in display_text
    assert "Shakes:" in display_text
    assert "Iced Teas:" in display_text
    assert "Mocktails:" in display_text
    assert "\nCoffees:" not in display_text
    assert "Hot Chocolate" not in display_text


@pytest.mark.asyncio
async def test_browse_current_menu_request_marks_unknown_browse_as_non_passthrough(
    store,
):
    """Verify browse current menu request marks unknown browse as non passthrough.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    token = set_current_product_query("any desserts")
    try:
        data = payload(await browse_current_menu_request(include_items=True))
    finally:
        reset_current_product_query(token)

    assert data["success"] is True
    assert data["data"]["passthrough"] is False
    assert data["data"]["response_kind"] == "menu_sections"
    assert "Of course. Here are the menu sections:" in data["data"]["display_text"]


@pytest.mark.asyncio
async def test_browse_current_menu_request_does_not_let_model_force_full_menu(store):
    """Verify browse current menu request does not let model force full menu.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    token = set_current_product_query("show the menu")
    try:
        data = payload(await browse_current_menu_request(include_items=True))
    finally:
        reset_current_product_query(token)

    assert data["success"] is True
    assert data["data"]["passthrough"] is True
    assert data["data"]["response_kind"] == "menu_sections"
    assert "Of course. Here are the menu sections:" in data["data"]["display_text"]
    assert (
        "Here is the complete menu, grouped by section:"
        not in data["data"]["display_text"]
    )
    assert "- Coffees:" not in data["data"]["display_text"]


@pytest.mark.asyncio
async def test_find_current_menu_matches_returns_dessert_style_items(store):
    """Verify find current menu matches returns dessert style items.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    token = set_current_product_query("any desserts")
    try:
        data = payload(await find_current_menu_matches(max_results=4))
    finally:
        reset_current_product_query(token)

    assert data["success"] is True
    assert data["data"]["passthrough"] is True
    assert data["data"]["response_kind"] == "item_matches"
    assert data["data"]["count"] >= 3
    display_text = data["data"]["display_text"]
    assert "dedicated Desserts section" in display_text
    assert "Affogato" in display_text
    assert "Brownie Cold Coffee" in display_text
    assert "Brownie Shake" in display_text
    assert "menu sections" not in display_text


@pytest.mark.asyncio
async def test_find_current_menu_matches_no_match_is_not_passthrough(store):
    """Verify find current menu matches no match is not passthrough.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    token = set_current_product_query("show unicorn snacks")
    try:
        data = payload(await find_current_menu_matches(max_results=4))
    finally:
        reset_current_product_query(token)

    assert data["success"] is True
    assert data["data"]["count"] == 0
    assert data["data"]["passthrough"] is False
    assert data["data"]["items"] == []


@pytest.mark.asyncio
async def test_recommend_current_menu_items_returns_structured_data_driven_list(store):
    """Verify recommend current menu items returns structured data driven list.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    data = payload(await recommend_current_menu_items(max_results=5))

    assert data["success"] is True
    assert data["data"]["passthrough"] is True
    assert data["data"]["response_kind"] == "recommendations"
    assert 1 <= data["data"]["count"] <= 5
    assert data["data"]["items"]
    assert "Representative picks from the current menu:" in data["data"]["display_text"]
    assert "I can show" not in data["data"]["display_text"]
    assert "Would you like" not in data["data"]["display_text"]


@pytest.mark.asyncio
async def test_browse_current_menu_request_marks_preference_scoped_browse_non_passthrough(
    store,
):
    """Verify browse current menu request marks preference scoped browse non passthrough.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    token = set_current_product_query("sweet cold drinks")
    try:
        data = payload(await browse_current_menu_request())
    finally:
        reset_current_product_query(token)

    assert data["success"] is True
    assert data["data"]["passthrough"] is False
    assert data["data"]["requested_section"] == "cold drinks"


@pytest.mark.asyncio
async def test_browse_current_menu_request_applies_active_vegan_preference(store):
    """Verify remembered vegan preference scopes menu browsing.

    Args:
        - store: Any - The store fixture.

    Returns:
        - return None - This test has no return value.
    """
    store.session_preferences.setdefault("vegan-session", set()).add("vegan")
    query_token = set_current_product_query("Pizzas")
    session_token = set_current_product_session_id("vegan-session")
    try:
        data = payload(await browse_current_menu_request(include_items=True))
    finally:
        reset_current_product_query(query_token)
        reset_current_product_session_id(session_token)

    assert data["success"] is True
    assert data["data"]["passthrough"] is True
    assert data["data"]["response_kind"] == "preference_scoped_browse"
    text = data["data"]["display_text"]
    assert "marked Vegan" in text
    assert "Closest vegetarian options" in text
    assert "Margherita Pizza" in text
    assert "Chicken Tikka Pizza" not in text
    assert "Kentucky Crunch Chicken Pizza" not in text


@pytest.mark.asyncio
async def test_filter_current_menu_by_price_uses_structured_prices(store):
    """Verify filter current menu by price uses structured prices.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    token = set_current_product_query("items under INR 100")
    try:
        data = payload(await filter_current_menu_by_price())
    finally:
        reset_current_product_query(token)

    assert data["success"] is True
    assert data["data"]["count"] == 1
    assert data["data"]["items"][0]["name"] == "Espresso"
    assert "Espresso - ₹99" in data["data"]["display_text"]
    assert "Salted Fries" not in data["data"]["display_text"]


@pytest.mark.asyncio
async def test_filter_current_menu_by_price_reports_no_food_matches(store):
    """Verify filter current menu by price reports no food matches.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    token = set_current_product_query("food under 100 rupees")
    try:
        data = payload(await filter_current_menu_by_price())
    finally:
        reset_current_product_query(token)

    assert data["success"] is True
    assert data["data"]["count"] == 0
    assert "I could not find any Food items under ₹100" in data["data"]["display_text"]
    assert "lowest Food option" in data["data"]["display_text"]
    assert "Classic Nachos at ₹199" in data["data"]["display_text"]
    assert "Espresso" not in data["data"]["display_text"]


@pytest.mark.asyncio
async def test_filter_current_menu_by_price_supports_plural_category_scope(store):
    """Verify filter current menu by price supports plural category scope.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    token = set_current_product_query("pizzas under 400")
    try:
        data = payload(await filter_current_menu_by_price())
    finally:
        reset_current_product_query(token)

    assert data["success"] is True
    assert data["data"]["count"] == 4
    assert "Here are the items in Pizzas under ₹400" in data["data"]["display_text"]
    assert "Margherita Pizza - ₹329" in data["data"]["display_text"]
    assert "Mediterranean Pizza - ₹389" in data["data"]["display_text"]
    assert "Veg Pesto Pizza" not in data["data"]["display_text"]


@pytest.mark.asyncio
async def test_list_current_menu_prices_returns_scoped_prices(store):
    """Verify list current menu prices returns scoped prices.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    token = set_current_product_query("show prices for all Coffees")
    try:
        data = payload(await list_current_menu_prices())
    finally:
        reset_current_product_query(token)

    assert data["success"] is True
    assert data["data"]["count"] == 18
    assert "Here are the prices for Coffees:" in data["data"]["display_text"]
    assert "Espresso - ₹99 [Coffee] (Hot)" in data["data"]["display_text"]
    assert "Americano - ₹169 [Coffee] (Hot)" in data["data"]["display_text"]
    assert "Americano - ₹199 [Coffee] (Iced)" in data["data"]["display_text"]
    assert "Tonic Espresso" not in data["data"]["display_text"]


@pytest.mark.asyncio
async def test_list_current_menu_prices_rejects_non_price_browse_request(store):
    """Verify list current menu prices rejects non price browse request.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    token = set_current_product_query("show me the cold beverages")
    try:
        data = payload(await list_current_menu_prices())
    finally:
        reset_current_product_query(token)

    assert data["success"] is False
    assert "does not ask for prices" in data["error"]


@pytest.mark.asyncio
async def test_list_current_menu_prices_scopes_cold_beverages(store):
    """Verify list current menu prices scopes cold beverages.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    token = set_current_product_query("show prices for cold beverages")
    try:
        data = payload(await list_current_menu_prices())
    finally:
        reset_current_product_query(token)

    assert data["success"] is True
    display_text = data["data"]["display_text"]
    assert "Original Cold Brew" in display_text
    assert "Virgin Mojito" in display_text
    assert "Oreo Shake" in display_text
    assert "Espresso" not in display_text
    assert "Apple Cinnamon Herbal Tea" not in display_text
    assert "Hot Chocolate" not in display_text


@pytest.mark.asyncio
async def test_context_dependent_price_request_uses_last_menu_scope(store):
    """Verify context dependent price request uses last menu scope.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    session_token = set_current_product_session_id("s-price")
    browse_token = set_current_product_query("show me the coffees")
    try:
        await browse_current_menu_request()
    finally:
        reset_current_product_query(browse_token)

    price_token = set_current_product_query("show the prices")
    try:
        data = payload(await list_current_menu_prices())
    finally:
        reset_current_product_query(price_token)
        reset_current_product_session_id(session_token)

    assert data["success"] is True
    assert "Here are the prices for Coffees:" in data["data"]["display_text"]
    assert "Espresso - ₹99 [Coffee] (Hot)" in data["data"]["display_text"]
    assert "Tonic Espresso" not in data["data"]["display_text"]
    assert "Original Cold Brew" not in data["data"]["display_text"]


@pytest.mark.asyncio
async def test_combined_product_attribute_search_returns_both_sources(monkeypatch):
    """Verify combined product attribute search returns both sources.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return Any - The return value.
    """

    def fake_retrieve(source_key: str, query: str, max_results: int):
        """Verify fake retrieve.

        Args:
            - source_key: str - The source key value.
            - query: str - The query value.
            - max_results: int - The max results value.

        Returns:
            - return Any - The return value.
        """
        return [
            SimpleNamespace(
                text=f"{source_key}: {query}",
                score=0.9,
                source=f"{source_key}.md",
                chunk_index=max_results,
            )
        ]

    monkeypatch.setattr(product_tools, "_retrieve_knowledge_source", fake_retrieve)

    data = payload(
        await search_product_and_attribute_knowledge("sweet light drink", max_results=2)
    )

    assert data["success"] is True
    assert data["data"]["menu_count"] == 1
    assert data["data"]["attribute_count"] == 1
    assert data["data"]["menu_results"][0]["text"] == "product: sweet light drink"
    assert (
        data["data"]["attribute_results"][0]["text"]
        == "menu_attributes: sweet light drink"
    )


@pytest.mark.asyncio
async def test_add_to_cart_then_view_cart(store):
    """Verify add to cart then view cart.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    await add_to_cart("s001", "m001", quantity=2)
    data = payload(await view_cart("s001"))

    assert data["success"] is True
    assert data["data"]["total_inr"] == 360
    assert data["data"]["item_count"] == 1


@pytest.mark.asyncio
async def test_add_to_cart_resolves_exact_menu_item_name_from_sql_catalog(store):
    """Verify add to cart resolves exact menu item name from sql catalog.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    add_data = payload(await add_to_cart("s001", "Espresso", quantity=1))
    cart_data = payload(await view_cart("s001"))

    assert add_data["success"] is True
    assert cart_data["success"] is True
    assert cart_data["data"]["item_count"] == 1
    assert cart_data["data"]["cart"]["items"][0]["name"] == "Espresso"
    assert cart_data["data"]["cart"]["items"][0]["item_id"] == "menu-espresso"
    assert cart_data["data"]["total_inr"] == 99

    memory = memory_storage.load_memory("s001")
    await memory._create_table()
    async with memory.engine.connect() as conn:
        saved_item = (
            await conn.execute(
                select(memory_storage.MENU_ITEMS_TABLE.c.name).where(
                    memory_storage.MENU_ITEMS_TABLE.c.id == "menu-espresso"
                )
            )
        ).scalar_one()

    assert saved_item == "Espresso"


@pytest.mark.asyncio
async def test_add_to_cart_quantity_zero_returns_failure(store):
    """Verify add to cart quantity zero returns failure.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    data = payload(await add_to_cart("s001", "m001", quantity=0))

    assert data["success"] is False


@pytest.mark.asyncio
async def test_place_order_empty_cart_returns_failure(store):
    """Verify place order empty cart returns failure.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    data = payload(await place_order("s001"))

    assert data["success"] is False
    assert "empty" in data["error"]


@pytest.mark.asyncio
async def test_full_happy_path_add_place_order_cart_empty(store):
    """Verify full happy path add place order cart empty.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    await add_to_cart("s001", "m001", quantity=2)

    order_data = payload(await place_order("s001"))
    cart_data = payload(await view_cart("s001"))

    assert order_data["success"] is True
    assert order_data["data"]["order"]["status"] == "confirmed"
    assert cart_data["success"] is True
    assert cart_data["data"]["cart"]["items"] == []

    memory = memory_storage.load_memory("s001")
    await memory._create_table()
    async with memory.engine.connect() as conn:
        cart_total = await conn.scalar(
            select(memory_storage.CARTS_TABLE.c.total_inr).where(
                memory_storage.CARTS_TABLE.c.conversation_id == memory.conversation_id
            )
        )
        order_count = await conn.scalar(
            select(func.count(memory_storage.ORDERS_TABLE.c.id)).where(
                memory_storage.ORDERS_TABLE.c.session_id == "s001"
            )
        )
        order_item_count = await conn.scalar(
            select(func.count(memory_storage.ORDER_ITEMS_TABLE.c.id))
        )

    assert cart_total == 0
    assert order_count == 1
    assert order_item_count == 1


@pytest.mark.asyncio
async def test_cancel_unknown_order_returns_failure(store):
    """Verify cancel unknown order returns failure.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    data = payload(await cancel_order("ord-nope"))

    assert data["success"] is False


@pytest.mark.asyncio
async def test_faq_lookup_hours_success(store):
    """Verify faq lookup hours success.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    data = payload(await faq_lookup("hours?"))

    assert data["success"] is True
    assert "7 AM" in data["data"]["answer"]


@pytest.mark.asyncio
async def test_faq_lookup_no_match_returns_failure(store):
    """Verify faq lookup no match returns failure.

    Args:
        - store: Any - The store value.

    Returns:
        - return None - The return value.
    """
    data = payload(await faq_lookup("unicorns?"))

    assert data["success"] is False
