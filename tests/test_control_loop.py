from types import SimpleNamespace

import pytest

from cafe.core.control_loop import _build_context, run_turn


@pytest.mark.asyncio
async def test_agent_exception_is_handled(monkeypatch):
    class BadAgent:
        memory = type("M", (), {"get_memory": lambda self: []})()

        async def __call__(self, msg):
            raise RuntimeError("boom")

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: BadAgent(),
    )

    out = await run_turn("s1", "hi")

    assert "went wrong" in out["reply"].lower()
    assert out["tool_calls"] == []


@pytest.mark.asyncio
async def test_deterministic_menu_turn_skips_orchestrator(monkeypatch):
    class BadAgent:
        memory = type("M", (), {"get_memory": lambda self: []})()

        async def __call__(self, msg):
            raise AssertionError("orchestrator should not be called")

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: BadAgent(),
    )

    out = await run_turn("s1", "show the menu please")

    assert "Here are the menu sections:" in out["reply"]
    assert "Coffee Fusions" in out["reply"]
    assert out["tool_calls"][0]["name"] == "browse_current_menu_request"


@pytest.mark.asyncio
async def test_deterministic_section_turn_remembers_scope_for_price_followup(monkeypatch):
    class BadAgent:
        memory = type("M", (), {"get_memory": lambda self: []})()

        async def __call__(self, msg):
            raise AssertionError("orchestrator should not be called")

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: BadAgent(),
    )

    first = await run_turn("s-scope", "show me the coffees")
    second = await run_turn("s-scope", "cool show me the prices of it")

    assert "Here are the items under Coffees:" in first["reply"]
    assert "Here are the prices for Coffees:" in second["reply"]
    assert "Espresso - " in second["reply"]
    assert "Tonic Espresso" not in second["reply"]


@pytest.mark.asyncio
async def test_unknown_menu_category_still_uses_orchestrator(monkeypatch):
    orchestrator_reply = "I did not find a dedicated desserts section, but I can show sweet drinks."

    class DessertAgent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            return SimpleNamespace(content=orchestrator_reply)

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: DessertAgent(),
    )

    out = await run_turn("s1", "any desserts?")

    assert out["reply"] == orchestrator_reply
    assert out["tool_calls"] == []


def test_context_includes_cart(store):
    from cafe.services.cart_service import add_item

    add_item(store, "s1", "m001", 1)

    ctx = _build_context("s1")

    assert "[session_id=s1]" in ctx
    assert "₹180" in ctx


def test_context_no_cart_no_orders():
    ctx = _build_context("brand_new")

    assert "[session_id=brand_new]" in ctx
    assert "cart" not in ctx


@pytest.mark.asyncio
async def test_product_only_menu_turn_uses_deterministic_reply(monkeypatch):
    specialist_reply = "this should not be used"

    class ProductOnlyAgent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            self.messages.extend([
                SimpleNamespace(content=[
                    {
                        "type": "tool_use",
                        "name": "ask_product_agent",
                        "input": {"query": "Show me the menu"},
                    },
                    {
                        "type": "tool_result",
                        "name": "ask_product_agent",
                        "output": [{"type": "text", "text": specialist_reply}],
                    },
                ])
            ])
            return SimpleNamespace(content="Here's the menu categories.")

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: ProductOnlyAgent(),
    )

    out = await run_turn("s1", "show the menu")

    assert "Here are the menu sections:" in out["reply"]
    assert "- Coffee Fusions" in out["reply"]
    assert out["tool_calls"][0]["name"] == "browse_current_menu_request"


@pytest.mark.parametrize(
    ("tool_name", "specialist_reply"),
    [
        ("ask_cart_agent", "Your cart has 1 Cappuccino."),
        ("ask_order_agent", "Order ord-123 is confirmed."),
        ("ask_support_agent", "We are open from 7 AM to 11 PM."),
    ],
)
@pytest.mark.asyncio
async def test_single_specialist_turn_returns_specialist_reply(
    monkeypatch,
    tool_name,
    specialist_reply,
):
    class SingleSpecialistAgent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            self.messages.append(
                SimpleNamespace(content=[
                    {
                        "type": "tool_use",
                        "name": tool_name,
                        "input": {"query": "specialist request"},
                    },
                    {
                        "type": "tool_result",
                        "name": tool_name,
                        "output": [{"type": "text", "text": specialist_reply}],
                    },
                ])
            )
            return SimpleNamespace(content="Summarized by orchestrator.")

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: SingleSpecialistAgent(),
    )

    out = await run_turn("s1", "specialist-only turn")

    assert out["reply"] == specialist_reply


@pytest.mark.asyncio
async def test_multi_specialist_turn_uses_orchestrator_reply(monkeypatch):
    orchestrator_reply = "I found the item and added it to your cart."

    class MultiSpecialistAgent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            self.messages.append(
                SimpleNamespace(content=[
                    {
                        "type": "tool_use",
                        "name": "ask_product_agent",
                        "input": {"query": "Find Cappuccino id"},
                    },
                    {
                        "type": "tool_result",
                        "name": "ask_product_agent",
                        "output": [{"type": "text", "text": "Cappuccino is m001."}],
                    },
                    {
                        "type": "tool_use",
                        "name": "ask_cart_agent",
                        "input": {"query": "Add m001"},
                    },
                    {
                        "type": "tool_result",
                        "name": "ask_cart_agent",
                        "output": [{"type": "text", "text": "Added Cappuccino."}],
                    },
                ])
            )
            return SimpleNamespace(content=orchestrator_reply)

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: MultiSpecialistAgent(),
    )

    out = await run_turn("s1", "add cappuccino")

    assert out["reply"] == orchestrator_reply


@pytest.mark.asyncio
async def test_product_passthrough_uses_only_current_turn_tools(monkeypatch):
    current_reply = "Here are the items under Pizzas:\n- Margherita Pizza"

    class AgentWithPreviousProductCalls:
        def __init__(self):
            self.messages = [
                SimpleNamespace(content=[
                    {
                        "type": "tool_use",
                        "name": "ask_product_agent",
                        "input": {"query": "Show menu"},
                    },
                    {
                        "type": "tool_result",
                        "name": "ask_product_agent",
                        "output": [{"type": "text", "text": "old menu"}],
                    },
                ]),
                SimpleNamespace(content=[
                    {
                        "type": "tool_use",
                        "name": "ask_product_agent",
                        "input": {"query": "Show coffees"},
                    },
                    {
                        "type": "tool_result",
                        "name": "ask_product_agent",
                        "output": [{"type": "text", "text": "old coffees"}],
                    },
                ]),
            ]
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            self.messages.append(
                SimpleNamespace(content=[
                    {
                        "type": "tool_use",
                        "name": "ask_product_agent",
                        "input": {"query": "Show pizzas"},
                    },
                    {
                        "type": "tool_result",
                        "name": "ask_product_agent",
                        "output": [{"type": "text", "text": current_reply}],
                    },
                ])
            )
            return SimpleNamespace(content="We have pizzas like Margherita.")

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: AgentWithPreviousProductCalls(),
    )

    out = await run_turn("s1", "what pizzas do you have?")

    assert "Here are the items under Pizzas:" in out["reply"]
    assert "- Margherita Pizza" in out["reply"]
    assert "- Kentucky Crunch Chicken Pizza" in out["reply"]
    assert out["tool_calls"] == [
        {
            "name": "browse_current_menu_request",
            "input": {
                "query": "what pizzas do you have?",
                "response_kind": "section_items",
                "requested_section": "Pizzas",
            },
        }
    ]


@pytest.mark.asyncio
async def test_single_specialist_passthrough_does_not_inspect_response_text(monkeypatch):
    specialist_reply = "Here is the complete menu, grouped by section:\n\nBeverages:\n- Coffees: Espresso"
    orchestrator_reply = "I did not find a dedicated desserts section, but I can show sweet drinks."

    class SingleSpecialistAgent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            self.messages.append(
                SimpleNamespace(content=[
                    {
                        "type": "tool_use",
                        "name": "ask_product_agent",
                        "input": {"query": "Show desserts"},
                    },
                    {
                        "type": "tool_result",
                        "name": "ask_product_agent",
                        "output": [{"type": "text", "text": specialist_reply}],
                    },
                ])
            )
            return SimpleNamespace(content=orchestrator_reply)

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: SingleSpecialistAgent(),
    )

    out = await run_turn("s1", "any desserts?")

    assert out["reply"] == specialist_reply
