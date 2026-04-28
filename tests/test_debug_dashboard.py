from fastapi.testclient import TestClient

from cafe.api.debug import build_flow_state
from cafe.api.main import app
from cafe.core.debug_trace import get_debug_trace_store


def test_debug_flow_state_shape():
    get_debug_trace_store().reset()

    state = build_flow_state()

    assert state["flow"]
    assert state["components"]
    assert state["runtime"]["memory_keep_recent_messages"] >= 1
    assert "carts" in state["state"]


def test_debug_dashboard_page_served():
    client = TestClient(app)

    response = client.get("/debug/flow")

    assert response.status_code == 200
    assert "Milo Architecture Console" in response.text
    assert "Chat Runner" in response.text
    assert "/debug/flow/events" in response.text


def test_root_redirects_to_dashboard():
    client = TestClient(app)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/debug/flow"
