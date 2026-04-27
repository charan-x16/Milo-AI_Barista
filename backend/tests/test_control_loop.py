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
