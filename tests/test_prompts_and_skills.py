"""Tests test prompts and skills module."""

import re
from pathlib import Path

from cafe.agents import prompts

PROMPT_DIR = Path("src/cafe/agents/agent_md")
SKILL_DIR = Path("src/cafe/skills")


def test_all_agent_markdown_files_exist_and_load():
    """Verify all agent markdown files exist and load.

    Returns:
        - return None - The return value.
    """
    expected = [
        "orchestrator",
        "product_search",
        "cart_management",
        "order_management",
        "customer_support",
    ]

    for name in expected:
        path = PROMPT_DIR / f"{name}.md"
        assert path.exists()
        assert prompts.load(name)


def test_loaded_prompts_contain_expected_keywords():
    """Verify loaded prompts contain expected keywords.

    Returns:
        - return None - The return value.
    """
    assert "Orchestrator" in prompts.ORCHESTRATOR_PROMPT
    assert "Product Search" in prompts.PRODUCT_SEARCH_PROMPT
    assert "Cart" in prompts.CART_MANAGEMENT_PROMPT
    assert "Order" in prompts.ORDER_MANAGEMENT_PROMPT
    assert "Customer Support" in prompts.CUSTOMER_SUPPORT_PROMPT


def test_category_prompts_preserve_complete_category_lists():
    """Verify category prompts preserve complete category lists.

    Returns:
        - return None - The return value.
    """
    product_prompt = " ".join(prompts.PRODUCT_SEARCH_PROMPT.split())
    orchestrator_prompt = " ".join(prompts.ORCHESTRATOR_PROMPT.split())

    assert "include every returned top-level category" in product_prompt
    assert "do not merge, rename, abbreviate, or summarize categories" in product_prompt
    assert "call `browse_current_menu_request()` first" in product_prompt
    assert (
        "leave `include_items` unset so the user sees sections first" in product_prompt
    )
    assert (
        '"show me the coffees", "show mocktails", "show pizza options", "show drinks", "show cold beverages", and "show cool drinks" are all browse requests'
        in product_prompt
    )
    assert "Concise does not mean incomplete" in orchestrator_prompt
    assert "Do not merge categories" in orchestrator_prompt


def test_followup_scope_prompts_do_not_overcarry_old_category():
    """Verify followup scope prompts do not overcarry old category.

    Returns:
        - return None - The return value.
    """
    product_prompt = " ".join(prompts.PRODUCT_SEARCH_PROMPT.split())
    orchestrator_prompt = " ".join(prompts.ORCHESTRATOR_PROMPT.split())

    assert "do not over-carry old category filters" in orchestrator_prompt
    assert '"anything under 100" means search the whole menu' in orchestrator_prompt
    assert (
        "search across the whole menu instead of inheriting the previous category"
        in product_prompt
    )
    assert 'If the user says "not in coffee"' in product_prompt


def test_preference_prompts_make_vegan_recommendations_specific():
    """Verify preference prompts make vegan recommendations specific.

    Returns:
        - return None - The return value.
    """
    product_prompt = " ".join(prompts.PRODUCT_SEARCH_PROMPT.split())
    orchestrator_prompt = " ".join(prompts.ORCHESTRATOR_PROMPT.split())
    skill_text = (SKILL_DIR / "menu_navigation" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    skill_prompt = " ".join(skill_text.split())

    assert "Preserve explicit user preferences" in orchestrator_prompt
    assert "Pure preference, profile, or memory-update statements" in orchestrator_prompt
    assert "do not call `ask_product_agent`" in orchestrator_prompt
    assert "No matching menu items are available" in orchestrator_prompt
    assert "Treat short confirmations" in orchestrator_prompt
    assert "call Product Search for specific vegan options" in orchestrator_prompt
    assert "list two to four specific drinks" in product_prompt
    assert 'Do not say "all of these"' in product_prompt
    assert "Never invent menu items or categories" in product_prompt
    assert "list specific item names" in skill_prompt


def test_prompts_avoid_progress_only_and_empty_followup_loops():
    """Verify prompts avoid progress only and empty followup loops.

    Returns:
        - return None - The return value.
    """
    product_prompt = " ".join(prompts.PRODUCT_SEARCH_PROMPT.split())
    orchestrator_prompt = " ".join(prompts.ORCHESTRATOR_PROMPT.split())

    assert "Do not give progress-only replies" in product_prompt
    assert "call it and return the answer in the same turn" in product_prompt
    assert "must include the concrete sections or items" in product_prompt
    assert "I found the menu" in product_prompt
    assert 'Do not start with "I found..."' in product_prompt
    assert "do not end with a follow-up question" in product_prompt
    assert "Show the requested menu data and stop" in product_prompt
    assert "keep the returned grouped list format" in product_prompt
    assert "Of course. Here are the menu sections:" in product_prompt
    assert "Do not narrate internal work to the customer" in orchestrator_prompt
    assert "copy that response exactly" in orchestrator_prompt
    assert "Do not use generic closers" in orchestrator_prompt
    assert "Do not convert menu sections into inline prose" in orchestrator_prompt
    assert "Let me know" in orchestrator_prompt
    assert "list_current_menu_prices()" in product_prompt
    assert (
        "Do not call `browse_current_menu_request` for price-list requests"
        in product_prompt
    )
    assert (
        'do not call `list_current_menu_prices` for browse requests like "show cold beverages"'
        in product_prompt
    )


def test_product_prompt_uses_match_tool_when_browse_is_not_passthrough():
    """Verify product prompt uses match tool when browse is not passthrough.

    Returns:
        - return None - The return value.
    """
    product_prompt = " ".join(prompts.PRODUCT_SEARCH_PROMPT.split())
    skill_text = (SKILL_DIR / "menu_navigation" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    skill_prompt = " ".join(skill_text.split())

    assert "`passthrough: false`" in product_prompt
    assert "`find_current_menu_matches` or RAG" in product_prompt
    assert "do not copy the menu overview" in product_prompt
    assert "find_current_menu_matches()" in product_prompt
    assert "there is no dedicated Desserts section" in product_prompt
    assert "Continue with `find_current_menu_matches()` first" in skill_prompt


def test_product_prompt_uses_data_driven_recommendation_tool():
    """Verify product prompt uses data driven recommendation tool.

    Returns:
        - return None - The return value.
    """
    product_prompt = " ".join(prompts.PRODUCT_SEARCH_PROMPT.split())

    assert "recommend_current_menu_items(max_results)" in product_prompt
    assert "does not use manually selected picks" in product_prompt
    assert "Do not ask a follow-up and do not show the full menu" in product_prompt


def test_product_prompt_uses_human_yes_no_dietary_style():
    """Verify product prompt uses human yes no dietary style.

    Returns:
        - return None - The return value.
    """
    product_prompt = " ".join(prompts.PRODUCT_SEARCH_PROMPT.split())

    assert "For yes/no dietary questions, answer directly first" in product_prompt
    assert "Yes, if you choose oat or almond milk" in product_prompt
    assert 'Start with "Yes", "No", or "Not by default"' in product_prompt
    assert 'Avoid phrases like "The menu confirms that..."' in product_prompt


def test_orchestrator_prompt_preserves_specific_product_wording():
    """Verify orchestrator prompt preserves specific product wording.

    Returns:
        - return None - The return value.
    """
    orchestrator_prompt = " ".join(prompts.ORCHESTRATOR_PROMPT.split())

    assert "preserve the customer's wording" in orchestrator_prompt
    assert "preserve the customer's wording exactly" in orchestrator_prompt
    assert "Do not broaden a specific section" in orchestrator_prompt
    assert '"show me the menu" into "show the full menu"' in orchestrator_prompt
    assert '"show me the coffee" into "show all coffee options"' in orchestrator_prompt
    assert (
        '"show the prices for all" should become "show prices for all Coffees"'
        in orchestrator_prompt
    )


def test_orchestrator_prompt_enforces_precise_product_answers():
    """Verify orchestrator prompt enforces precise product answers.

    Returns:
        - return None - The return value.
    """
    orchestrator_prompt = " ".join(prompts.ORCHESTRATOR_PROMPT.split())

    assert "behave like a precise router" in orchestrator_prompt
    assert (
        "Product Search owns the menu facts and tool formatting" in orchestrator_prompt
    )
    assert "Identify the exact menu intent" in orchestrator_prompt
    assert "Always call Product Search for menu/product requests" in orchestrator_prompt
    assert "Do not answer these from Orchestrator memory" in orchestrator_prompt
    assert (
        "Never show the full menu unless the user explicitly asked"
        in orchestrator_prompt
    )
    assert (
        "Do not ask generic follow-up questions when the answer is already available"
        in orchestrator_prompt
    )
    assert "We currently do not have <category> on the menu." in orchestrator_prompt
    assert (
        "Never replace a concrete Product Search answer with vague wording"
        in orchestrator_prompt
    )


def test_skill_files_exist_and_have_valid_yaml_frontmatter():
    """Verify skill files exist and have valid yaml frontmatter.

    Returns:
        - return None - The return value.
    """
    expected = [
        "menu_navigation",
        "cart_etiquette",
        "order_lifecycle",
        "support_playbook",
    ]
    frontmatter_pattern = re.compile(
        r"\A---\r?\nname: [a-z_]+\r?\ndescription: .+\r?\n---\r?\n",
        re.DOTALL,
    )

    for name in expected:
        path = SKILL_DIR / name / "SKILL.md"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert frontmatter_pattern.match(text)
