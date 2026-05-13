import re
from pathlib import Path

from cafe.agents import prompts


PROMPT_DIR = Path("src/cafe/agents/agent_md")
SKILL_DIR = Path("src/cafe/skills")


def _single_line(text: str) -> str:
    return " ".join(text.split())


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
    assert "Cart Management" in prompts.CART_MANAGEMENT_PROMPT
    assert "Order Management" in prompts.ORDER_MANAGEMENT_PROMPT
    assert "Customer Support" in prompts.CUSTOMER_SUPPORT_PROMPT


def test_agent_prompts_stay_compact():
    limits = {
        "orchestrator": 4500,
        "product_search": 4500,
        "cart_management": 2000,
        "order_management": 2000,
        "customer_support": 2000,
    }

    for name, limit in limits.items():
        text = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
        assert len(text) <= limit


def test_orchestrator_prompt_defines_main_agent_contract():
    prompt = _single_line(prompts.ORCHESTRATOR_PROMPT)

    assert "Every user chat message comes to you first" in prompt
    assert "Do not do specialist domain work yourself" in prompt
    assert "classify the request" in prompt
    assert "call the right specialist agent or agents" in prompt
    assert "return one clear customer-facing reply" in prompt


def test_orchestrator_prompt_defines_specialist_routing():
    prompt = _single_line(prompts.ORCHESTRATOR_PROMPT)

    assert "ask_product_agent(query)" in prompt
    assert "ask_cart_agent(query)" in prompt
    assert "ask_order_agent(query)" in prompt
    assert "ask_support_agent(query)" in prompt
    assert "Preserve `[session_id=...]` exactly" in prompt
    assert "Always call Product Search for menu/product requests" in prompt
    assert "Call Product Search before Cart" in prompt
    assert "Call Cart before Order" in prompt


def test_orchestrator_prompt_preserves_product_output_contract():
    prompt = _single_line(prompts.ORCHESTRATOR_PROMPT)

    assert "Product Search owns menu facts and menu formatting" in prompt
    assert "copy that answer exactly" in prompt
    assert "Preserve headings, blank lines, bullets" in prompt
    assert "Do not merge categories" in prompt
    assert "Never show the full menu unless the user explicitly asked" in prompt
    assert "Do not add generic closers" in prompt


def test_product_prompt_defines_tool_decision_contract():
    prompt = _single_line(prompts.PRODUCT_SEARCH_PROMPT)

    assert "browse_current_menu_request(include_items)" in prompt
    assert "find_current_menu_matches(max_results)" in prompt
    assert "recommend_current_menu_items(max_results)" in prompt
    assert "filter_current_menu_by_price()" in prompt
    assert "list_current_menu_prices()" in prompt
    assert "search_product_knowledge(query, max_results)" in prompt
    assert "search_menu_attribute_knowledge(query, max_results)" in prompt
    assert "search_product_and_attribute_knowledge(query, max_results)" in prompt
    assert "If `passthrough: true`, answer from `display_text`" in prompt
    assert "if `passthrough: false`, do not show the browse output" in prompt


def test_product_prompt_preserves_menu_answer_contract():
    prompt = _single_line(prompts.PRODUCT_SEARCH_PROMPT)

    assert "include the concrete sections, items, or prices" in prompt
    assert "Include every returned top-level category and subcategory" in prompt
    assert "do not merge, rename, abbreviate, or summarize categories" in prompt
    assert "show sections first" in prompt
    assert "Do not use browse tools for budget filtering" in prompt
    assert "Do not use it for ordinary browse requests" in prompt
    assert "Never invent menu items or categories" in prompt
    assert "Show the requested data and stop" in prompt


def test_product_prompt_handles_preferences_and_handoff():
    prompt = _single_line(prompts.PRODUCT_SEARCH_PROMPT)

    assert "list specific items and the confirmed reason each fits" in prompt
    assert "Do not say \"all of these\"" in prompt
    assert "start with \"Yes\", \"No\", or \"Not by default\"" in prompt
    assert "include an item id only if a tool or retrieved menu text confirms it" in prompt


def test_specialist_prompts_keep_domain_boundaries():
    cart = _single_line(prompts.CART_MANAGEMENT_PROMPT)
    order = _single_line(prompts.ORDER_MANAGEMENT_PROMPT)
    support = _single_line(prompts.CUSTOMER_SUPPORT_PROMPT)

    assert "You only add items, remove items, view the current cart, and clear the cart" in cart
    assert "Never guess item ids" in cart
    assert "add_to_cart(session_id, item_id, quantity, customizations)" in cart

    assert "You only place orders from the active cart" in order
    assert "Tracking and cancellation require an order id" in order
    assert "place_order(session_id, max_budget_inr)" in order

    assert "Every support answer must use `search_support_knowledge`" in support
    assert "Escalate when the knowledge base does not answer" in support
    assert "search_support_knowledge(query, max_results)" in support


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
