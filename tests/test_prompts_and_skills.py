import re
from pathlib import Path

from cafe.agents import prompts


PROMPT_DIR = Path("src/cafe/agents/agent_md")
SKILL_DIR = Path("src/cafe/skills")


def test_all_agent_markdown_files_exist_and_load():
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
    assert "Orchestrator" in prompts.ORCHESTRATOR_PROMPT
    assert "Product Search" in prompts.PRODUCT_SEARCH_PROMPT
    assert "Cart" in prompts.CART_MANAGEMENT_PROMPT
    assert "Order" in prompts.ORDER_MANAGEMENT_PROMPT
    assert "Customer Support" in prompts.CUSTOMER_SUPPORT_PROMPT


def test_category_prompts_preserve_complete_category_lists():
    product_prompt = " ".join(prompts.PRODUCT_SEARCH_PROMPT.split())
    orchestrator_prompt = " ".join(prompts.ORCHESTRATOR_PROMPT.split())

    assert "include every returned top-level category" in product_prompt
    assert "do not merge, rename, abbreviate, or summarize categories" in product_prompt
    assert "call `browse_current_menu_request()` first" in product_prompt
    assert "leave `include_items` unset so the user sees sections first" in product_prompt
    assert '"show me the coffees", "show mocktails", "show pizza options", "show drinks", and "show cold beverages" are all browse requests' in product_prompt
    assert "Concise does not mean incomplete" in orchestrator_prompt
    assert "Do not merge categories" in orchestrator_prompt


def test_followup_scope_prompts_do_not_overcarry_old_category():
    product_prompt = " ".join(prompts.PRODUCT_SEARCH_PROMPT.split())
    orchestrator_prompt = " ".join(prompts.ORCHESTRATOR_PROMPT.split())

    assert "do not over-carry old category filters" in orchestrator_prompt
    assert '"anything under 100" means search the whole menu' in orchestrator_prompt
    assert "search across the whole menu instead of inheriting the previous category" in product_prompt
    assert 'If the user says "not in coffee"' in product_prompt


def test_preference_prompts_make_vegan_recommendations_specific():
    product_prompt = " ".join(prompts.PRODUCT_SEARCH_PROMPT.split())
    orchestrator_prompt = " ".join(prompts.ORCHESTRATOR_PROMPT.split())
    skill_text = (SKILL_DIR / "menu_navigation" / "SKILL.md").read_text(encoding="utf-8")
    skill_prompt = " ".join(skill_text.split())

    assert "Preserve explicit user preferences" in orchestrator_prompt
    assert "Treat short confirmations" in orchestrator_prompt
    assert "call Product Search for specific vegan options" in orchestrator_prompt
    assert "list two to four specific drinks" in product_prompt
    assert "Do not say \"all of these\"" in product_prompt
    assert "Never invent menu items or categories" in product_prompt
    assert "list specific item names" in skill_prompt


def test_prompts_avoid_progress_only_and_empty_followup_loops():
    product_prompt = " ".join(prompts.PRODUCT_SEARCH_PROMPT.split())
    orchestrator_prompt = " ".join(prompts.ORCHESTRATOR_PROMPT.split())

    assert "Do not give progress-only replies" in product_prompt
    assert "call it and return the answer in the same turn" in product_prompt
    assert "Show the list first" in product_prompt
    assert "Do not narrate internal work to the customer" in orchestrator_prompt
    assert "pass it through" in orchestrator_prompt
    assert "Do not overuse generic closers" in orchestrator_prompt
    assert "list_current_menu_prices()" in product_prompt
    assert "Do not call `browse_current_menu_request` for price-list requests" in product_prompt
    assert 'do not call `list_current_menu_prices` for browse requests like "show cold beverages"' in product_prompt


def test_orchestrator_prompt_preserves_specific_product_wording():
    orchestrator_prompt = " ".join(prompts.ORCHESTRATOR_PROMPT.split())

    assert "preserve the customer's wording" in orchestrator_prompt
    assert "Do not broaden a specific section" in orchestrator_prompt
    assert '"show me the coffee" into "show all coffee options"' in orchestrator_prompt
    assert '"show the prices for all" should become "show prices for all Coffees"' in orchestrator_prompt


def test_skill_files_exist_and_have_valid_yaml_frontmatter():
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
