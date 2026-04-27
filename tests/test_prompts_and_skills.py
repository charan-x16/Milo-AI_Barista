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
