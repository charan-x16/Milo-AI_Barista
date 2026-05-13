"""Tests test specialists module."""

from pathlib import Path

from cafe.agents.specialists.cart_management_agent import make_cart_management_agent
from cafe.agents.specialists.customer_support_agent import make_customer_support_agent
from cafe.agents.specialists.order_management_agent import make_order_management_agent
from cafe.agents.specialists.product_search_agent import make_product_search_agent

AGENT_MD_DIR = Path("src/cafe/agents/agent_md")
SKILL_DIR = Path("src/cafe/skills")


def test_specialist_factory_functions_import():
    """Verify specialist factory functions import.

    Returns:
        - return None - The return value.
    """
    assert callable(make_product_search_agent)
    assert callable(make_cart_management_agent)
    assert callable(make_order_management_agent)
    assert callable(make_customer_support_agent)


def test_specialist_skill_directories_exist():
    """Verify specialist skill directories exist.

    Returns:
        - return None - The return value.
    """
    for name in [
        "menu_navigation",
        "cart_etiquette",
        "order_lifecycle",
        "support_playbook",
    ]:
        skill_path = SKILL_DIR / name / "SKILL.md"
        assert skill_path.exists()
        assert skill_path.read_text(encoding="utf-8").startswith("---")


def test_specialist_agent_markdown_exists_and_is_non_empty():
    """Verify specialist agent markdown exists and is non empty.

    Returns:
        - return None - The return value.
    """
    for name in [
        "product_search",
        "cart_management",
        "order_management",
        "customer_support",
    ]:
        prompt_path = AGENT_MD_DIR / f"{name}.md"
        assert prompt_path.exists()
        assert prompt_path.read_text(encoding="utf-8").strip()
