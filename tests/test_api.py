import os

import pytest
from fastapi.testclient import TestClient

from cafe.api.main import app
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
