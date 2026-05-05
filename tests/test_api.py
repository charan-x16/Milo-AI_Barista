import asyncio
import os

import pytest
from agentscope.message import Msg
from fastapi.testclient import TestClient

from cafe.api.main import app
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
    assert client.get("/health").json()["status"] == "ok"


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
