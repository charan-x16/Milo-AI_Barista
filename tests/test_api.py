import asyncio
import os

import pytest
from agentscope.message import Msg
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from cafe.api import main as api_main
from cafe.api.main import app
from cafe.api.schemas import ChatRequest
from cafe.agents.memory import load_memory
from cafe.core.state import get_store
from cafe.services.cart_service import add_item


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset(client):
    client.post("/admin/reset")
    yield


def test_health(client):
    payload = client.get("/health").json()

    assert payload["status"] == "ok"
    assert "agent_cache_ready" in payload
    assert "timestamp" in payload


def test_menu_eight(client):
    assert len(client.get("/menu").json()["items"]) == 8


def test_new_session(client):
    assert "session_id" in client.post("/sessions").json()


def test_empty_cart(client):
    assert client.get("/sessions/abc/cart").json()["total_inr"] == 0


def test_reset_session_clears_current_cart(client):
    add_item(get_store(), "abc", "m001", quantity=2)

    assert client.get("/sessions/abc/cart").json()["total_inr"] == 360

    response = client.post("/sessions/abc/reset")

    assert response.status_code == 200
    assert client.get("/sessions/abc/cart").json()["total_inr"] == 0


def test_conversation_history_apis_return_sql_memory(client):
    async def seed_memory():
        memory = load_memory("history-session", user_id="anonymous")
        await memory.clear()
        await memory.add(
            [
                Msg(
                    "user",
                    "[session_id=history-session] show menu",
                    "user",
                    metadata={"display_text": "show menu"},
                ),
                Msg("assistant", "Of course. Here are the menu sections.", "assistant"),
            ]
        )

    asyncio.run(seed_memory())

    conversations = client.get("/users/anonymous/conversations").json()
    messages = client.get("/sessions/history-session/messages").json()

    assert conversations["user_id"] == "anonymous"
    assert conversations["conversations"][0]["session_id"] == "history-session"
    assert conversations["conversations"][0]["title"] == "show menu"
    assert conversations["conversations"][0]["last_message"] == (
        "Of course. Here are the menu sections."
    )
    assert [msg["role"] for msg in messages["messages"]] == ["user", "assistant"]
    assert messages["messages"][0]["content"] == "show menu"
    assert messages["messages"][1]["content"] == (
        "Of course. Here are the menu sections."
    )


def test_chat_validation(client):
    response = client.post("/chat", json={"session_id": "", "message": "hi"})

    assert response.status_code == 422


def test_chat_generates_session_when_omitted(client, monkeypatch):
    seen = {}

    class FakeOrchestrator:
        name = "Orchestrator"

        async def __call__(self, msg):
            seen["message_content"] = msg.content
            return Msg("assistant", "done", "assistant")

    class FakeSessionManager:
        def get_or_create(self, session_id, user_id="anonymous"):
            seen["session_id"] = session_id
            seen["user_id"] = user_id
            return FakeOrchestrator()

    async def fake_memory_summary(session_id, user_id="anonymous"):
        seen["summary_session_id"] = session_id
        seen["summary_user_id"] = user_id
        return None

    monkeypatch.setattr(api_main, "get_session_manager", lambda: FakeSessionManager())
    monkeypatch.setattr(api_main, "maybe_generate_memory_summary", fake_memory_summary)

    response = client.post("/chat", json={"message": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"]
    assert body["session_id"] == seen["session_id"]
    assert seen["user_id"] == "anonymous"
    assert f"[session_id={body['session_id']}]" in seen["message_content"]
    assert seen["summary_session_id"] == body["session_id"]


@pytest.mark.asyncio
async def test_chat_schedules_memory_summary_after_response(monkeypatch):
    summary_called = False

    class FakeOrchestrator:
        name = "Orchestrator"

        async def __call__(self, msg):
            return Msg("assistant", "done", "assistant")

    class FakeSessionManager:
        def get_or_create(self, session_id, user_id="anonymous"):
            return FakeOrchestrator()

    async def fake_memory_summary(session_id, user_id="anonymous"):
        nonlocal summary_called
        summary_called = True
        return {"summary_version": 1}

    monkeypatch.setattr(api_main, "get_session_manager", lambda: FakeSessionManager())
    monkeypatch.setattr(api_main, "maybe_generate_memory_summary", fake_memory_summary)

    background_tasks = BackgroundTasks()
    response = await api_main.chat(
        ChatRequest(session_id="background-session", message="hello"),
        background_tasks,
    )

    assert response.reply == "done"
    assert summary_called is False
    assert len(background_tasks.tasks) == 1

    await background_tasks()

    assert summary_called is True


@pytest.mark.skipif(
    not (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")),
    reason="needs LLM_API_KEY or OPENAI_API_KEY",
)
def test_chat_end_to_end(client):
    sid = client.post("/sessions").json()["session_id"]
    response = client.post(
        "/chat",
        json={
            "session_id": sid,
            "message": "Add a masala chai and place the order, budget ₹100.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"]
    orders = client.get(f"/sessions/{sid}/orders").json()["orders"]
    assert len(orders) >= 1
