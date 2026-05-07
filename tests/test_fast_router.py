import asyncio
import time

import pytest

from cafe.agents import session_manager as sm
from cafe.core.fast_router import fast_intent_router
from cafe.core.observability import (
    TurnObserver,
    reset_current_observer,
    set_current_observer,
)
from cafe.core.state import get_store
from cafe.core.turn_runtime import run_turn


def _fail_orchestrator(*args, **kwargs):
    raise AssertionError("fast path should not create the Orchestrator")


@pytest.mark.asyncio
async def test_fast_menu_bypasses_orchestrator(monkeypatch):
    monkeypatch.setattr(sm.SessionManager, "get_or_create", _fail_orchestrator)

    out = await run_turn("fast-menu", "can you show the menu")

    assert out["request_id"]
    assert out["tool_calls"] == []
    assert "menu sections" in out["reply"]
    assert "Beverages:" in out["reply"]


@pytest.mark.asyncio
async def test_fast_add_to_cart_bypasses_orchestrator(monkeypatch, store):
    monkeypatch.setattr(sm.SessionManager, "get_or_create", _fail_orchestrator)

    out = await run_turn("fast-cart", "add espresso to cart")
    cart = get_store().get_cart("fast-cart")

    assert "Added 1 Espresso" in out["reply"]
    assert cart.items[0].name == "Espresso"
    assert cart.total_inr > 0


@pytest.mark.asyncio
async def test_fast_place_order_bypasses_orchestrator(monkeypatch, store):
    monkeypatch.setattr(sm.SessionManager, "get_or_create", _fail_orchestrator)

    await run_turn("fast-order", "add espresso to cart")
    out = await run_turn("fast-order", "place order")

    assert "is confirmed" in out["reply"]
    assert get_store().get_cart("fast-order").is_empty()
    assert len(get_store().orders) == 1


@pytest.mark.asyncio
async def test_fast_timings_has_zero_llm_calls():
    observer = TurnObserver(
        session_id="fast-faq",
        user_id="anonymous",
        user_text="what are the timings?",
    )
    token = set_current_observer(observer)
    try:
        result = await fast_intent_router("fast-faq", "what are the timings?")
    finally:
        reset_current_observer(token)

    summary = observer.summary()
    assert result.matched
    assert result.intent == "timings"
    assert result.reply == "We are open daily 7 AM to 11 PM."
    assert summary["llm_calls"] == 0
    assert summary["tool_calls"] == 1


@pytest.mark.asyncio
async def test_fast_router_persists_turn_in_background(monkeypatch):
    saved = asyncio.Event()

    async def slow_save_fast_turn(**_kwargs):
        await asyncio.sleep(0.2)
        saved.set()

    monkeypatch.setattr(
        "cafe.core.fast_router.save_fast_turn",
        slow_save_fast_turn,
    )

    start = time.perf_counter()
    result = await fast_intent_router("fast-background", "what are the timings?")

    assert result.matched
    assert (time.perf_counter() - start) < 0.1
    assert not saved.is_set()
    await asyncio.wait_for(saved.wait(), timeout=1.0)
