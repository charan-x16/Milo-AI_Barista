import asyncio
import json

import pytest
from agentscope.message import Msg
from fastapi.testclient import TestClient

from cafe.api import main as api_main
from cafe.api.main import app
from cafe.api.schemas import ChatRequest


def _event_payload(event: str) -> dict:
    assert event.startswith("data: ")
    return json.loads(event.removeprefix("data: ").strip())


@pytest.mark.asyncio
async def test_streaming_yields_status_before_orchestrator_finishes(monkeypatch):
    release_orchestrator = asyncio.Event()
    summary_done = asyncio.Event()

    class FakeOrchestrator:
        name = "Orchestrator"

        async def __call__(self, msg):
            await release_orchestrator.wait()
            return Msg("assistant", "Here are drinks under INR 200.", "assistant")

    class FakeSessionManager:
        def get_or_create(self, session_id, user_id="anonymous"):
            return FakeOrchestrator()

    async def fake_summary(session_id, user_id="anonymous", turn_id=None):
        summary_done.set()

    monkeypatch.setattr(api_main, "get_session_manager", lambda: FakeSessionManager())
    monkeypatch.setattr(api_main, "maybe_generate_memory_summary_safe", fake_summary)

    stream = api_main._chat_stream_events(
        ChatRequest(session_id="stream-session", message="show drinks under 200")
    )

    first = _event_payload(await anext(stream))
    assert first["type"] == "status"
    assert first["content"] == "Routing your request..."

    release_orchestrator.set()
    payloads = [_event_payload(event) async for event in stream]

    content = "".join(
        payload["content"] for payload in payloads if payload["type"] == "content"
    )
    assert content == "Here are drinks under INR 200."
    assert payloads[-1]["type"] == "done"
    assert payloads[-1]["session_id"] == "stream-session"

    await asyncio.wait_for(summary_done.wait(), timeout=1)


@pytest.mark.asyncio
async def test_streaming_yields_error_event(monkeypatch):
    class FailingOrchestrator:
        name = "Orchestrator"

        async def __call__(self, msg):
            raise RuntimeError("stream failed")

    class FakeSessionManager:
        def get_or_create(self, session_id, user_id="anonymous"):
            return FailingOrchestrator()

    monkeypatch.setattr(api_main, "get_session_manager", lambda: FakeSessionManager())

    stream = api_main._chat_stream_events(
        ChatRequest(session_id="stream-error", message="hello")
    )
    payloads = [_event_payload(event) async for event in stream]

    assert payloads[0]["type"] == "status"
    assert payloads[-1]["type"] == "error"
    assert "stream failed" in payloads[-1]["content"]


def test_streaming_endpoint_returns_sse(monkeypatch):
    class FakeOrchestrator:
        name = "Orchestrator"

        async def __call__(self, msg):
            return Msg("assistant", "Streaming works.", "assistant")

    class FakeSessionManager:
        def get_or_create(self, session_id, user_id="anonymous"):
            return FakeOrchestrator()

    async def fake_summary(session_id, user_id="anonymous", turn_id=None):
        return None

    monkeypatch.setattr(api_main, "get_session_manager", lambda: FakeSessionManager())
    monkeypatch.setattr(api_main, "maybe_generate_memory_summary_safe", fake_summary)

    client = TestClient(app)
    with client.stream(
        "POST",
        "/chat/stream",
        json={"session_id": "stream-endpoint", "message": "hello"},
    ) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"type": "status"' in body
    assert '"type": "content"' in body
    assert '"type": "done"' in body
