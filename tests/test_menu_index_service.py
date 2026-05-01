from cafe.services.menu_index_service import (
    build_menu_index,
    format_menu_browse_query,
    format_menu_categories,
    format_menu_section_items,
    resolve_sections,
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
