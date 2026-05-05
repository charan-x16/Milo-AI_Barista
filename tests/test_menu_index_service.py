from pathlib import Path

from cafe.services.menu_index_service import (
    build_menu_index,
    format_menu_browse_query,
    format_menu_categories,
    format_menu_item_matches,
    format_menu_recommendations,
    format_menu_section_items,
    recommend_menu_items,
    resolve_sections,
    search_menu_item_matches,
)


def test_menu_index_parses_sections_and_items():
    index = build_menu_index()

    assert "Beverages" in index.top_level_categories
    assert "Food" in index.top_level_categories
    assert "Mocktails" in index.flat_category_names

    mocktails = next(section for section in index.sections if section.name == "Mocktails")
    assert "Virgin Mojito" in mocktails.items


def test_menu_index_parses_browse_aliases_from_document():
    index = build_menu_index()

    assert index.aliases["drinks"] == ("Beverages",)
    assert index.aliases["coffee"] == (
        "Coffees",
        "Coffee Fusions",
        "Cold Brews",
        "Cold Coffees",
    )


def test_menu_index_treats_markdown_subgroups_as_groups_not_items():
    index = build_menu_index()

    pizzas = next(section for section in index.sections if section.name == "Pizzas")
    assert "Veg Pizzas" not in pizzas.items
    assert "Non-Veg Pizzas" not in pizzas.items
    assert "Margherita Pizza" in pizzas.items
    assert "Kentucky Crunch Chicken Pizza" in pizzas.items


def test_menu_sections_default_to_browsable_index():
    text = format_menu_categories()

    assert "Here are the menu sections:" in text
    assert "- Coffees" in text
    assert "- Mocktails" in text
    assert "Virgin Mojito" not in text


def test_menu_browse_query_ignores_full_menu_override_without_explicit_item_request():
    text = format_menu_browse_query("show the menu", include_items=True)

    assert "Here are the menu sections:" in text
    assert "Here is the complete menu, grouped by section:" not in text
    assert "- Coffees:" not in text


def test_menu_section_items_resolves_exact_section():
    text = format_menu_section_items("Mocktails")

    assert "Here are the items under Mocktails:" in text
    assert "- Virgin Mojito" in text
    assert "- Sparkling Peach Mojito" in text


def test_menu_section_items_resolves_coffee_group():
    sections = resolve_sections("coffee")
    section_names = {section.name for section in sections}

    assert {"Coffees", "Coffee Fusions", "Cold Brews", "Cold Coffees"} <= section_names


def test_menu_browse_query_routes_named_section_to_items():
    text = format_menu_browse_query("show me the coffees")

    assert "Here are the items under Coffees:" in text
    assert "- Espresso" in text
    assert "Coffee Fusions:" not in text


def test_menu_browse_query_routes_singular_coffee_to_coffees_section():
    text = format_menu_browse_query("show me the coffee")

    assert "Here are the items under Coffees:" in text
    assert "- Espresso" in text
    assert "- Affogato" in text
    assert "Coffee Fusions:" not in text
    assert "Cold Brews:" not in text


def test_menu_browse_query_routes_singular_section_to_items():
    text = format_menu_browse_query("show pizza options")

    assert "Here are the items under Pizzas:" in text
    assert "- Margherita Pizza" in text
    assert "- Kentucky Crunch Chicken Pizza" in text


def test_menu_browse_query_routes_coffee_options_to_group_items():
    text = format_menu_browse_query("show all coffee options")

    assert "Absolutely. Here are the matching sections for coffee options:" in text
    assert "Coffees:" in text
    assert "Coffee Fusions:" in text
    assert "- Brownie Cold Coffee" in text


def test_menu_browse_query_routes_cold_beverages_to_cold_sections():
    text = format_menu_browse_query("show me the cold beverages")

    assert "Absolutely. Here are the matching sections for cold beverages:" in text
    assert "Cold Brews:" in text
    assert "Cold Coffees:" in text
    assert "Shakes:" in text
    assert "Iced Teas:" in text
    assert "Mocktails:" in text
    assert "\nCoffees:" not in text
    assert "Herbal Teas:" not in text
    assert "Hot Chocolate" not in text


def test_menu_browse_query_routes_cool_drinks_to_cold_sections():
    text = format_menu_browse_query("show me the cool drinks")

    assert "Absolutely. Here are the matching sections for cool drinks:" in text
    assert "Cold Brews:" in text
    assert "Cold Coffees:" in text
    assert "Shakes:" in text
    assert "Iced Teas:" in text
    assert "Mocktails:" in text
    assert "\nCoffees:" not in text
    assert "Herbal Teas:" not in text
    assert "Hot Chocolate" not in text


def test_search_menu_item_matches_finds_dessert_style_items():
    matches = search_menu_item_matches("any desserts", max_results=4)
    names = {item.name for item in matches}

    assert "Affogato" in names
    assert "Brownie Cold Coffee" in names
    assert "Brownie Shake" in names


def test_search_menu_item_matches_uses_data_layer_match_aliases():
    matches = search_menu_item_matches("after food", max_results=4)
    names = {item.name for item in matches}
    text = format_menu_item_matches("after food", max_results=4)

    assert "Affogato" in names
    assert "Brownie Cold Coffee" in names
    assert "for food" not in text


def test_search_menu_item_match_aliases_adapt_to_menu_document(monkeypatch):
    menu_text = "\n".join(
        [
            "# Custom Menu",
            "### Match Aliases",
            "- **celebration:** festive",
            "## Drinks > Specials",
            "### Festive Sip",
            "- **Price:** INR 20",
            "- **Serving:** Chilled",
            "- **Tags:** festive",
            "- **Description:** From custom menu data.",
        ]
    )
    original_read_text = Path.read_text

    def fake_read_text(path: Path, *args, **kwargs):
        if str(path) == "custom_alias_menu.md":
            return menu_text
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    matches = search_menu_item_matches(
        "celebration",
        max_results=2,
        menu_doc_path="custom_alias_menu.md",
    )

    assert [item.name for item in matches] == ["Festive Sip"]


def test_format_menu_item_matches_does_not_show_menu_overview_for_desserts():
    text = format_menu_item_matches("any desserts", max_results=4)

    assert "dedicated Desserts section" in text
    assert "Affogato" in text
    assert "Here are the menu sections" not in text


def test_menu_item_matches_rejects_noisy_nonexistent_category():
    matches = search_menu_item_matches("show unicorn snacks", max_results=6)

    assert matches == ()


def test_menu_item_matches_filters_preference_inside_category_scope():
    browse = format_menu_browse_query("sweet cold drinks")
    matches = search_menu_item_matches("sweet cold drinks", max_results=6)
    names = {item.name for item in matches}

    assert "matching sections for cold drinks" in browse
    assert "Caramel Cold Coffee" in names
    assert "Black Currant Mojito" in names
    assert "Cafe Cola Cold Brew" not in names


def test_menu_browse_query_handles_multi_category_request():
    text = format_menu_browse_query("coffee and mocktails")

    assert "matching sections for coffee and mocktails" in text
    assert "Mocktails:" in text
    assert "Coffees:" in text
    assert "Pizzas:" not in text


def test_menu_item_matches_are_deterministic_for_same_input():
    first = [item.as_dict() for item in search_menu_item_matches("something sweet", max_results=5)]
    second = [item.as_dict() for item in search_menu_item_matches("something sweet", max_results=5)]

    assert first == second


def test_recommend_menu_items_are_data_driven_and_deterministic():
    first = [item.as_dict() for item in recommend_menu_items(max_results=5)]
    second = [item.as_dict() for item in recommend_menu_items(max_results=5)]
    menu_names = {
        item
        for section in build_menu_index().sections
        for item in section.items
    }

    assert first == second
    assert 1 <= len(first) <= 5
    assert all(item["name"] in menu_names for item in first)
    assert len({item["top_level"] for item in first}) > 1


def test_recommend_menu_items_adapts_to_menu_document(monkeypatch):
    menu_text = "\n".join(
        [
            "# Custom Menu",
            "## Alpha > First",
            "### Alpha Bowl",
            "- **Price:** INR 10",
            "- **Serving:** Small",
            "- **Tags:** bright",
            "- **Description:** From custom menu data.",
            "## Beta > Second",
            "### Beta Sip",
            "- **Price:** INR 20",
            "- **Serving:** Chilled",
            "- **Tags:** smooth",
            "- **Description:** From custom menu data.",
            "## Alpha > Later",
            "### Alpha Tart",
            "- **Price:** INR 30",
            "- **Serving:** Plate",
            "- **Tags:** sweet",
            "- **Description:** From custom menu data.",
        ]
    )
    original_read_text = Path.read_text

    def fake_read_text(path: Path, *args, **kwargs):
        if str(path) == "custom_menu.md":
            return menu_text
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    items = recommend_menu_items(max_results=3, menu_doc_path="custom_menu.md")

    assert [item.name for item in items] == ["Alpha Bowl", "Beta Sip", "Alpha Tart"]
    assert [item.top_level for item in items] == ["Alpha", "Beta", "Alpha"]


def test_format_menu_recommendations_has_concrete_items_without_generic_prompt():
    text = format_menu_recommendations(max_results=5)

    assert "Representative picks from the current menu:" in text
    assert "- " in text
    assert "Would you like" not in text
    assert "I can show" not in text
