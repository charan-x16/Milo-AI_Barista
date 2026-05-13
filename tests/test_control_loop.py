from types import SimpleNamespace

import pytest
from agentscope.message import Msg

from cafe.core.intent_router import Route, RouteResult, execute_route, route_message
from cafe.core.session_context import (
    build_session_context,
    format_orchestrator_context,
)
from cafe.core.state import get_store
from cafe.core.turn_runtime import run_turn


@pytest.fixture
def force_agent_route(monkeypatch):
    """Force the control-loop tests onto the agentic path.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - This fixture has no return value.
    """
    monkeypatch.setattr(
        "cafe.core.turn_runtime.route_message",
        lambda _text: RouteResult(Route.AGENT, "test"),
    )


@pytest.mark.asyncio
async def test_greeting_fast_path_skips_orchestrator_and_compression(monkeypatch):
    """Verify greetings bypass Orchestrator and compression.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - This test has no return value.
    """
    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("orchestrator should not be called")
        ),
    )
    monkeypatch.setattr(
        "cafe.core.turn_runtime.schedule_fast_turn_persistence",
        lambda **_kwargs: None,
    )
    out = await run_turn("fast-hi", "hi")

    assert "Milo" in out["reply"]
    assert out["tool_calls"] == []
    assert out["critique"] is None
    assert out["needs_compression"] is False


@pytest.mark.asyncio
async def test_simple_menu_overview_fast_path_skips_orchestrator(monkeypatch):
    """Verify simple menu browsing bypasses the Orchestrator.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - This test has no return value.
    """
    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("orchestrator should not be called")
        ),
    )
    monkeypatch.setattr(
        "cafe.core.turn_runtime.schedule_fast_turn_persistence",
        lambda **_kwargs: None,
    )

    out = await run_turn("fast-menu", "show me the menu")

    assert "menu sections" in out["reply"]
    assert "Pizzas" in out["reply"]
    assert out["needs_compression"] is False


@pytest.mark.asyncio
async def test_fast_greeting_uses_readable_customer_format(monkeypatch):
    """Verify fast greetings keep natural spacing.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - This test has no return value.
    """
    monkeypatch.setattr(
        "cafe.core.turn_runtime.schedule_fast_turn_persistence",
        lambda **_kwargs: None,
    )

    out = await run_turn("fast-greeting-format", "hello")

    assert out["reply"].startswith("Hi, welcome to Milo")
    assert "\n\n" in out["reply"]


@pytest.mark.asyncio
async def test_fast_cart_reply_uses_customer_ready_format(monkeypatch, store):
    """Verify fast cart replies use headings, bullets, and totals.

    Args:
        - monkeypatch: Any - The monkeypatch value.
        - store: Any - The store fixture.

    Returns:
        - return None - This test has no return value.
    """

    def close_background(awaitable, **_kwargs):
        """Close scheduled coroutine objects in this unit test.

        Args:
            - awaitable: Any - The scheduled awaitable.

        Returns:
            - return None - This test helper has no return value.
        """
        awaitable.close()

    monkeypatch.setattr("cafe.core.intent_router.schedule_background", close_background)

    reply = await execute_route(
        RouteResult(Route.CART_ADD, "add cart item"),
        "fast-cart-format",
        "add espresso",
    )

    assert reply.startswith("Done, I added 1 Espresso to your cart.")
    assert "Here is your cart:" in reply
    assert "- 1 x Espresso - INR" in reply
    assert "\n\nTotal: INR" in reply


@pytest.mark.asyncio
async def test_fast_faq_reply_uses_heading_and_spacing():
    """Verify fast FAQ replies are formatted for chat display.

    Returns:
        - return None - This test has no return value.
    """
    reply = await execute_route(
        RouteResult(Route.TIMINGS, "timings"),
        "fast-faq-format",
        "what are your timings",
    )

    assert reply.startswith("Sure, here are our cafe timings:")
    assert "\n\nWe are open daily" in reply


def test_preference_and_section_menu_queries_stay_agentic():
    """Verify preference-sensitive menu work stays on the agent path.

    Returns:
        - return None - This test has no return value.
    """
    assert route_message("i am vegan").route is Route.AGENT
    assert route_message("i am diabetic please remember").route is Route.AGENT
    assert route_message("show me the pizzas").route is Route.AGENT
    assert route_message("show vegan drinks").route is Route.AGENT


@pytest.mark.asyncio
async def test_preference_context_is_passed_to_agent(monkeypatch):
    """Verify remembered preferences are included in agent context.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - This test has no return value.
    """

    class Agent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            assert "[preferences: vegan]" in msg.content
            assert "show me the pizzas" in msg.content
            return SimpleNamespace(content="Here are vegan-friendly pizza options.")

    from cafe.agents import session_manager as sm

    store = get_store()
    store.session_preferences.setdefault("pref-session", set()).add("vegan")
    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: Agent(),
    )

    out = await run_turn("pref-session", "show me the pizzas")

    assert out["reply"] == "Here are vegan-friendly pizza options."
    assert out["needs_compression"] is True


@pytest.mark.asyncio
async def test_diabetic_preference_context_is_passed_to_agent(monkeypatch):
    """Verify diabetic preferences are remembered for later agent turns.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - This test has no return value.
    """

    class Agent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            assert "[preferences: diabetic]" in msg.content
            return SimpleNamespace(
                content="I will keep diabetic-friendly options in mind."
            )

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: Agent(),
    )

    out = await run_turn("diabetic-session", "i am diabetic please remember")

    assert "diabetic-friendly" in out["reply"]


@pytest.mark.asyncio
async def test_product_query_uses_cleaned_followup_and_preferences(monkeypatch):
    """Verify Product Search gets cleaned follow-up queries plus preferences.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - This test has no return value.
    """
    from cafe.agents import specialist_tools as tools
    from cafe.tools import product_tools

    class ProductAgent:
        async def __call__(self, msg):
            assert "active preferences/constraints: diabetic" in msg.content
            assert "Task: Flat White" in msg.content
            assert "yeah please tell me about this flat white" not in msg.content
            assert product_tools._CURRENT_PRODUCT_QUERY.get() == "Flat White"
            return SimpleNamespace(content="Flat White details.")

    get_store().session_preferences.setdefault("prod-pref-session", set()).add(
        "diabetic"
    )
    user_token = tools.set_current_user_request(
        "yeah please tell me about this flat white"
    )
    session_token = tools.set_current_session_id("prod-pref-session")
    monkeypatch.setattr(
        tools,
        "make_product_search_agent",
        lambda: ProductAgent(),
    )
    try:
        response = await tools.ask_product_agent("Flat White")
    finally:
        tools.reset_current_user_request(user_token)
        tools.reset_current_session_id(session_token)

    assert response.content[0]["text"] == "Flat White details."


@pytest.mark.asyncio
async def test_product_query_keeps_preference_aware_orchestrator_task(monkeypatch):
    """Verify Product tools keep Orchestrator-expanded preference tasks.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - This test has no return value.
    """
    from cafe.agents import specialist_tools as tools
    from cafe.tools import product_tools

    class ProductAgent:
        async def __call__(self, msg):
            assert "active preferences/constraints: diabetic" in msg.content
            assert "Task: Suggest diabetic-friendly coffees" in msg.content
            assert (
                product_tools._CURRENT_PRODUCT_QUERY.get()
                == "Suggest diabetic-friendly coffees"
            )
            return SimpleNamespace(content="Diabetic-friendly coffee suggestions.")

    get_store().session_preferences.setdefault("prod-aware-session", set()).add(
        "diabetic"
    )
    user_token = tools.set_current_user_request("yeah please suggest some coffees")
    session_token = tools.set_current_session_id("prod-aware-session")
    monkeypatch.setattr(
        tools,
        "make_product_search_agent",
        lambda: ProductAgent(),
    )
    try:
        response = await tools.ask_product_agent("Suggest diabetic-friendly coffees")
    finally:
        tools.reset_current_user_request(user_token)
        tools.reset_current_session_id(session_token)

    assert response.content[0]["text"] == "Diabetic-friendly coffee suggestions."


@pytest.mark.asyncio
async def test_specialist_receives_conversation_summary(monkeypatch):
    """Verify specialists receive the cumulative memory summary.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - This test has no return value.
    """
    from cafe.agents import specialist_tools as tools

    class ProductAgent:
        async def __call__(self, msg):
            assert "Conversation memory:" in msg.content
            assert "Customer prefers no added sugar." in msg.content
            assert "Task: Suggest diabetic-friendly coffees" in msg.content
            return SimpleNamespace(content="Low-sugar coffee suggestions.")

    async def cached_summary(_user_id, _session_id):
        return SimpleNamespace(
            found=True,
            summary="Customer prefers no added sugar.",
        )

    monkeypatch.setattr(tools, "get_cached_summary", cached_summary)
    monkeypatch.setattr(
        tools,
        "make_product_search_agent",
        lambda: ProductAgent(),
    )

    session_token = tools.set_current_session_id("summary-session")
    try:
        response = await tools.ask_product_agent("Suggest diabetic-friendly coffees")
    finally:
        tools.reset_current_session_id(session_token)

    assert response.content[0]["text"] == "Low-sugar coffee suggestions."


@pytest.mark.asyncio
async def test_single_customer_ready_specialist_result_skips_orchestrator_rewrite(
    monkeypatch,
    force_agent_route,
):
    """Verify a customer-ready specialist result is returned directly.

    Args:
        - monkeypatch: Any - The monkeypatch value.
        - force_agent_route: Any - Forces the agent path.

    Returns:
        - return None - This test has no return value.
    """

    class CaptureMemory:
        def __init__(self):
            self.messages = []
            self._capture_turn_messages = False
            self._turn_messages = []

        def begin_turn_capture(self):
            self._capture_turn_messages = True
            self._turn_messages = []

        def consume_turn_capture(self):
            captured = list(self._turn_messages)
            self._capture_turn_messages = False
            self._turn_messages = []
            return captured

        async def add(self, memories, **_kwargs):
            if memories is None:
                return
            if isinstance(memories, Msg):
                memories = [memories]
            self.messages.extend(memories)
            if self._capture_turn_messages:
                self._turn_messages.extend(memories)

        def get_memory(self, **_kwargs):
            return list(self.messages)

    class ManualOrchestrator:
        def __init__(self):
            self.name = "Orchestrator"
            self.max_iters = 4
            self.memory = CaptureMemory()
            self.reasoning_calls = 0

        async def _reasoning(self, _tool_choice=None):
            self.reasoning_calls += 1
            if self.reasoning_calls > 1:
                raise AssertionError("final Orchestrator rewrite should be skipped")
            msg = Msg(
                "Orchestrator",
                [
                    {
                        "type": "tool_use",
                        "id": "call-cart",
                        "name": "ask_cart_agent",
                        "input": {"query": "show cart"},
                    }
                ],
                "assistant",
            )
            await self.memory.add(msg)
            return msg

        async def _acting(self, tool_call):
            msg = Msg(
                "system",
                [
                    {
                        "type": "tool_result",
                        "id": tool_call["id"],
                        "name": tool_call["name"],
                        "output": [
                            {"type": "text", "text": "Your cart has 1 Cappuccino."}
                        ],
                    }
                ],
                "system",
            )
            await self.memory.add(msg)

        async def _summarizing(self):
            raise AssertionError("summarizer should not run")

    agent = ManualOrchestrator()

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: agent,
    )

    out = await run_turn("direct-session", "show my cart")

    assert out["reply"] == "Your cart has 1 Cappuccino."
    assert agent.reasoning_calls == 1
    assert out["tool_calls"] == [
        {"name": "ask_cart_agent", "input": {"query": "show cart"}}
    ]


@pytest.mark.asyncio
async def test_menu_agentic_turn_requires_orchestrator_tool_call(monkeypatch):
    """Verify specialist-owned menu turns require Orchestrator tool dispatch.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - This test has no return value.
    """

    class CaptureMemory:
        def __init__(self):
            self.messages = []
            self._capture_turn_messages = False
            self._turn_messages = []

        def begin_turn_capture(self):
            self._capture_turn_messages = True
            self._turn_messages = []

        def consume_turn_capture(self):
            captured = list(self._turn_messages)
            self._capture_turn_messages = False
            self._turn_messages = []
            return captured

        async def add(self, memories, **_kwargs):
            if memories is None:
                return
            if isinstance(memories, Msg):
                memories = [memories]
            self.messages.extend(memories)
            if self._capture_turn_messages:
                self._turn_messages.extend(memories)

        def get_memory(self, **_kwargs):
            return list(self.messages)

    class DispatchOnlyOrchestrator:
        def __init__(self):
            self.name = "Orchestrator"
            self.max_iters = 4
            self.memory = CaptureMemory()
            self.reasoning_calls = 0

        async def _reasoning(self, tool_choice=None):
            self.reasoning_calls += 1
            assert tool_choice == "required"
            msg = Msg(
                "Orchestrator",
                [
                    {
                        "type": "tool_use",
                        "id": "call-product",
                        "name": "ask_product_agent",
                        "input": {"query": "show me the coffees"},
                    }
                ],
                "assistant",
            )
            await self.memory.add(msg)
            return msg

        async def _acting(self, tool_call):
            msg = Msg(
                "system",
                [
                    {
                        "type": "tool_result",
                        "id": tool_call["id"],
                        "name": tool_call["name"],
                        "output": [
                            {
                                "type": "text",
                                "text": (
                                    "Absolutely. Here are the items under Coffees:\n"
                                    "- Espresso"
                                ),
                            }
                        ],
                    }
                ],
                "system",
            )
            await self.memory.add(msg)

    agent = DispatchOnlyOrchestrator()

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        "cafe.core.turn_runtime.route_message",
        lambda _text: RouteResult(Route.AGENT, "menu browsing needs agent context"),
    )
    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: agent,
    )

    out = await run_turn("tool-required-menu", "show me the coffees")

    assert out["reply"] == "Absolutely. Here are the items under Coffees:\n- Espresso"
    assert agent.reasoning_calls == 1
    assert out["tool_calls"] == [
        {"name": "ask_product_agent", "input": {"query": "show me the coffees"}}
    ]


@pytest.mark.asyncio
async def test_run_turn_schedules_background_summary(monkeypatch, force_agent_route):
    """Verify agentic turns only signal background memory summarization.

    Args:
        - monkeypatch: Any - The monkeypatch value.
        - force_agent_route: Any - Forces the agent path.

    Returns:
        - return None - This test has no return value.
    """

    class Agent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            return SimpleNamespace(content="Agent reply.")

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: Agent(),
    )
    out = await run_turn("agent-no-inline-compress", "complex message")

    assert out["reply"] == "Agent reply."
    assert out["needs_compression"] is True


@pytest.mark.asyncio
async def test_agent_exception_is_handled(monkeypatch, force_agent_route):
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
async def test_product_menu_turn_returns_orchestrator_final_answer(
    monkeypatch,
    force_agent_route,
):
    specialist_reply = (
        "Of course. Here are the menu sections:\n\nBeverages:\n- Coffee Fusions"
    )

    class ProductMenuAgent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            self.messages.append(
                SimpleNamespace(
                    content=[
                        {
                            "type": "tool_use",
                            "name": "ask_product_agent",
                            "input": {"query": "show the menu please"},
                        },
                        {
                            "type": "tool_result",
                            "name": "ask_product_agent",
                            "output": [{"type": "text", "text": specialist_reply}],
                        },
                    ]
                )
            )
            return SimpleNamespace(content="Here are a few categories.")

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: ProductMenuAgent(),
    )

    out = await run_turn("s1", "show the menu please")

    assert out["reply"] == specialist_reply
    assert out["tool_calls"] == [
        {
            "name": "ask_product_agent",
            "input": {"query": "show the menu please"},
        }
    ]


@pytest.mark.asyncio
async def test_unknown_menu_category_still_uses_orchestrator(
    monkeypatch,
    force_agent_route,
):
    orchestrator_reply = (
        "I did not find a dedicated desserts section, but I can show sweet drinks."
    )

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

    ctx = format_orchestrator_context(build_session_context(session_id="s1"))

    assert "[session_id=s1]" in ctx
    assert "INR 180" in ctx


def test_context_no_cart_no_orders():
    ctx = format_orchestrator_context(
        build_session_context(session_id="brand_new")
    )

    assert "[session_id=brand_new]" in ctx
    assert "cart" not in ctx


def test_session_manager_evicts_over_limit(monkeypatch):
    """Verify the session manager evicts least-recent sessions.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - This test has no return value.
    """
    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm,
        "get_settings",
        lambda: SimpleNamespace(
            session_cache_max_sessions=2,
            session_cache_ttl_seconds=1800,
        ),
    )
    monkeypatch.setattr(
        sm,
        "make_orchestrator",
        lambda session_id, user_id: SimpleNamespace(
            session_id=session_id,
            user_id=user_id,
        ),
    )
    manager = sm.SessionManager()

    manager.get_or_create("s1")
    manager.get_or_create("s2")
    manager.get_or_create("s3")

    assert manager.session_ids() == ["s2", "s3"]


@pytest.mark.asyncio
async def test_product_only_menu_turn_uses_orchestrator_final_answer(
    monkeypatch,
    force_agent_route,
):
    specialist_reply = "Here are the items under Coffees:\n- Espresso\n- Affogato"

    class ProductOnlyAgent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            self.messages.extend(
                [
                    SimpleNamespace(
                        content=[
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
                        ]
                    )
                ]
            )
            return SimpleNamespace(content="Here's the menu categories.")

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: ProductOnlyAgent(),
    )

    out = await run_turn("s1", "show the menu")

    assert out["reply"] == specialist_reply
    assert out["tool_calls"] == [
        {
            "name": "ask_product_agent",
            "input": {"query": "Show me the menu"},
        }
    ]


@pytest.mark.parametrize(
    ("tool_name", "specialist_reply"),
    [
        ("ask_cart_agent", "Your cart has 1 Cappuccino."),
        ("ask_order_agent", "Order ord-123 is confirmed."),
        ("ask_support_agent", "We are open from 7 AM to 11 PM."),
    ],
)
@pytest.mark.asyncio
async def test_single_specialist_turn_returns_orchestrator_final_answer(
    monkeypatch,
    force_agent_route,
    tool_name,
    specialist_reply,
):
    class SingleSpecialistAgent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            self.messages.append(
                SimpleNamespace(
                    content=[
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
                    ]
                )
            )
            return SimpleNamespace(content="Summarized by orchestrator.")

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: SingleSpecialistAgent(),
    )

    out = await run_turn("s1", "specialist-only turn")

    assert out["reply"] == "Summarized by orchestrator."


@pytest.mark.asyncio
async def test_multi_specialist_turn_uses_orchestrator_reply(
    monkeypatch,
    force_agent_route,
):
    orchestrator_reply = "I found the item and added it to your cart."

    class MultiSpecialistAgent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            self.messages.append(
                SimpleNamespace(
                    content=[
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
                    ]
                )
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
async def test_product_tool_extraction_uses_only_current_turn_tools(
    monkeypatch,
    force_agent_route,
):
    current_reply = "Here are the items under Pizzas:\n- Margherita Pizza"

    class AgentWithPreviousProductCalls:
        def __init__(self):
            self.messages = [
                SimpleNamespace(
                    content=[
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
                    ]
                ),
                SimpleNamespace(
                    content=[
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
                    ]
                ),
            ]
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            self.messages.append(
                SimpleNamespace(
                    content=[
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
                    ]
                )
            )
            return SimpleNamespace(content="We have pizzas like Margherita.")

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: AgentWithPreviousProductCalls(),
    )

    out = await run_turn("s1", "what pizzas do you have?")

    assert out["reply"] == current_reply
    assert out["tool_calls"] == [
        {
            "name": "ask_product_agent",
            "input": {"query": "Show pizzas"},
        }
    ]


@pytest.mark.asyncio
async def test_single_specialist_final_answer_comes_from_orchestrator(
    monkeypatch,
    force_agent_route,
):
    specialist_reply = "Here is the complete menu, grouped by section:\n\nBeverages:\n- Coffees: Espresso"
    orchestrator_reply = (
        "I did not find a dedicated desserts section, but I can show sweet drinks."
    )

    class SingleSpecialistAgent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            self.messages.append(
                SimpleNamespace(
                    content=[
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
                    ]
                )
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


@pytest.mark.asyncio
async def test_product_final_reply_preserves_specialist_wording(
    monkeypatch,
    force_agent_route,
):
    class NaturalProductAgent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            self.messages.append(
                SimpleNamespace(
                    content=[
                        {
                            "type": "tool_use",
                            "name": "ask_product_agent",
                            "input": {"query": "what do you recommend?"},
                        },
                        {
                            "type": "tool_result",
                            "name": "ask_product_agent",
                            "output": [
                                {
                                    "type": "text",
                                    "text": "We have many options. Would you like to explore?",
                                }
                            ],
                        },
                    ]
                )
            )
            return SimpleNamespace(content="We have many options.")

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: NaturalProductAgent(),
    )

    out = await run_turn("s1", "what do you recommend?")

    assert out["reply"] == "We have many options."


@pytest.mark.asyncio
async def test_product_formatted_list_drops_orchestrator_markdown_and_closer(
    monkeypatch,
    force_agent_route,
):
    specialist_reply = (
        "Absolutely. Here are the items under Coffees:\n"
        "- Espresso\n"
        "- Doppio\n"
        "- Americano"
    )

    class RewritingProductAgent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            self.messages.append(
                SimpleNamespace(
                    content=[
                        {
                            "type": "tool_use",
                            "name": "ask_product_agent",
                            "input": {"query": "coffees"},
                        },
                        {
                            "type": "tool_result",
                            "name": "ask_product_agent",
                            "output": [{"type": "text", "text": specialist_reply}],
                        },
                    ]
                )
            )
            return SimpleNamespace(
                content=(
                    "**Coffees:**\n- Espresso\n- Doppio\n- Americano\n\n"
                    "Would you like to know the prices?"
                )
            )

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: RewritingProductAgent(),
    )

    out = await run_turn("s1", "can you provide the items under coffees")

    assert out["reply"] == specialist_reply


@pytest.mark.asyncio
async def test_product_final_reply_is_not_rewritten(monkeypatch, force_agent_route):
    class ProductAgent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            self.messages.append(
                SimpleNamespace(
                    content=[
                        {
                            "type": "tool_use",
                            "name": "ask_product_agent",
                            "input": {"query": "dessert"},
                        },
                        {
                            "type": "tool_result",
                            "name": "ask_product_agent",
                            "output": [
                                {
                                    "type": "text",
                                    "text": "Here are dessert items:\n- Unicorn Cake",
                                }
                            ],
                        },
                    ]
                )
            )
            return SimpleNamespace(content="Here are dessert items:\n- Unicorn Cake")

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: ProductAgent(),
    )

    out = await run_turn("s1", "dessert")

    assert out["reply"] == "Here are dessert items:\n- Unicorn Cake"


@pytest.mark.asyncio
async def test_single_product_agent_reply_does_not_override_orchestrator_summary(
    monkeypatch,
    force_agent_route,
):
    product_agent_reply = (
        "I found the menu sections for you. Beverages include Coffees and Mocktails."
    )

    class SummarizingProductAgent:
        def __init__(self):
            self.messages = []
            self.memory = SimpleNamespace(get_memory=lambda: self.messages)

        async def __call__(self, msg):
            self.messages.append(
                SimpleNamespace(
                    content=[
                        {
                            "type": "tool_use",
                            "name": "ask_product_agent",
                            "input": {"query": "Show the menu"},
                        },
                        {
                            "type": "tool_result",
                            "name": "ask_product_agent",
                            "output": [{"type": "text", "text": product_agent_reply}],
                        },
                    ]
                )
            )
            return SimpleNamespace(content="Here are our menu sections:")

    from cafe.agents import session_manager as sm

    monkeypatch.setattr(
        sm.SessionManager,
        "get_or_create",
        lambda self, session_id: SummarizingProductAgent(),
    )

    out = await run_turn("s1", "show me the menu")

    assert out["reply"] == "Here are our menu sections:"
