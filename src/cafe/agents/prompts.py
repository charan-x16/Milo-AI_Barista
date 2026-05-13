"""Loads agent system prompts from agent_md/*.md files."""

from pathlib import Path

_DIR = Path(__file__).parent / "agent_md"


def load(name: str) -> str:
    """Load `agent_md/<name>.md` as a string.

    Args:
        - name: str - The name value.

    Returns:
        - return str - The return value.
    """
    return (_DIR / f"{name}.md").read_text(encoding="utf-8")


ORCHESTRATOR_PROMPT = load("orchestrator")
PRODUCT_SEARCH_PROMPT = load("product_search")
CART_MANAGEMENT_PROMPT = load("cart_management")
ORDER_MANAGEMENT_PROMPT = load("order_management")
CUSTOMER_SUPPORT_PROMPT = load("customer_support")
