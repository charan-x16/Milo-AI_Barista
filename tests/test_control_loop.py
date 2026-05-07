import pytest

from cafe.core.fast_router import FastRouteResult
from cafe.core.turn_runtime import _build_context, run_turn


@pytest.fixture(autouse=True)
def disable_fast_path(monkeypatch):
    async def miss(*args, **kwargs):
        return FastRouteResult.miss()

    monkeypatch.setattr("cafe.core.turn_runtime.fast_intent_router", miss)


@pytest.mark.asyncio
async def test_fallback_uses_single_llm_path(monkeypatch):
    seen = {}

    async def fake_single_llm_fallback(**kwargs):
        seen.update(kwargs)
        return "Single fallback reply."

    monkeypatch.setattr(
        "cafe.core.turn_runtime.run_single_llm_fallback",
        fake_single_llm_fallback,
    )

    out = await run_turn("s1", "something complex")

    assert out["request_id"]
    assert out["reply"] == "Single fallback reply."
    assert out["tool_calls"] == []
    assert seen["session_id"] == "s1"
    assert seen["user_text"] == "something complex"
    assert "[session_id=s1]" in seen["session_context"]


@pytest.mark.asyncio
async def test_single_llm_exception_is_handled(monkeypatch):
    async def bad_single_llm_fallback(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "cafe.core.turn_runtime.run_single_llm_fallback",
        bad_single_llm_fallback,
    )

    out = await run_turn("s1", "hi")

    assert "went wrong" in out["reply"].lower()
    assert out["tool_calls"] == []


def test_context_includes_cart(store):
    from cafe.services.cart_service import add_item

    add_item(store, "s1", "m001", 1)

    ctx = _build_context("s1")

    assert "[session_id=s1]" in ctx
    assert "INR 180" in ctx


def test_context_no_cart_no_orders():
    ctx = _build_context("brand_new")

    assert "[session_id=brand_new]" in ctx
    assert "cart" not in ctx
