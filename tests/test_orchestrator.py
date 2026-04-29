from cafe.agents import specialist_tools
from cafe.agents.orchestrator import make_orchestrator
from cafe.agents.prompts import ORCHESTRATOR_PROMPT
from cafe.agents.session_manager import get_session_manager


def test_make_orchestrator_imports_without_api_key():
    assert callable(make_orchestrator)


def test_orchestrator_prompt_lists_specialist_tools():
    assert "ask_product_agent" in ORCHESTRATOR_PROMPT
    assert "ask_cart_agent" in ORCHESTRATOR_PROMPT
    assert "ask_order_agent" in ORCHESTRATOR_PROMPT
    assert "ask_support_agent" in ORCHESTRATOR_PROMPT


def test_specialist_cache_initially_empty():
    specialist_tools.reset_specialists()
    assert specialist_tools._AGENTS == {}


def test_get_session_manager_returns_singleton():
    assert get_session_manager() is get_session_manager()

