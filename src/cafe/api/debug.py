"""Developer dashboard for visualizing the runtime agent flow."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, StreamingResponse

from cafe.agents.llm import normalized_provider
from cafe.agents.session_manager import get_session_manager
from cafe.api.debug_dashboard import DASHBOARD_HTML as DASHBOARD_PAGE_HTML
from cafe.config import get_settings
from cafe.core.debug_trace import get_debug_trace_store
from cafe.core.state import get_store


router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/flow", response_class=HTMLResponse)
async def flow_dashboard() -> HTMLResponse:
    return HTMLResponse(DASHBOARD_PAGE_HTML)


@router.get("/flow/state")
async def flow_state() -> dict:
    return build_flow_state()


@router.get("/flow/events")
async def flow_events() -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        last_version = -1
        while True:
            state = build_flow_state()
            if state["version"] != last_version:
                last_version = state["version"]
                payload = json.dumps(state, ensure_ascii=False)
                yield f"data: {payload}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


def build_flow_state() -> dict:
    trace = get_debug_trace_store().snapshot()
    store = get_store()
    session_ids = get_session_manager().session_ids()
    settings = get_settings()

    carts = [
        {
            "session_id": session_id,
            "items": len(cart.items),
            "total_inr": cart.total_inr,
        }
        for session_id, cart in sorted(store.carts.items())
    ]
    orders = [
        {
            "order_id": order.order_id,
            "session_id": order.session_id,
            "status": order.status,
            "total_inr": order.total_inr,
        }
        for order in store.orders.values()
    ]

    return {
        **trace,
        "components": [
            {"name": "FastAPI", "status": "online"},
            {"name": "SessionManager", "status": f"{len(session_ids)} active"},
            {"name": "StateStore", "status": f"{len(carts)} cart(s), {len(orders)} order(s)"},
            {"name": "FastRouter", "status": "hot path"},
            {"name": "SingleLLMFormatter", "status": "fallback only"},
            {"name": "AgentScope specialists", "status": "legacy/off hot path"},
            {
                "name": "Memory",
                "status": (
                    f"keep {settings.memory_keep_recent_messages} exact; "
                    "no inline compression"
                ),
            },
        ],
        "runtime": {
            "provider": normalized_provider(settings),
            "model": settings.openai_model,
            "active_sessions": session_ids,
            "memory_max_prompt_tokens": settings.memory_max_prompt_tokens,
            "memory_compression_trigger_tokens": settings.memory_compression_trigger_tokens,
            "memory_keep_recent_messages": settings.memory_keep_recent_messages,
        },
        "state": {
            "carts": carts,
            "orders": orders[-12:],
            "menu_items": len(store.menu),
        },
    }


DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Milo Flow Monitor</title>
  <style>
    :root {
      --ink: #191816;
      --paper: #f7f3ec;
      --muted: #716b61;
      --line: #d8cfbf;
      --panel: #fffaf1;
      --coffee: #70452f;
      --green: #22745a;
      --amber: #b26c24;
      --red: #a33b30;
      --blue: #2e667a;
      --shadow: rgba(25, 24, 22, 0.08);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      background: var(--paper);
      color: var(--ink);
      font-family: Georgia, "Times New Roman", serif;
    }

    .shell {
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr;
    }

    header {
      border-bottom: 1px solid var(--line);
      background:
        linear-gradient(90deg, rgba(112, 69, 47, 0.10), transparent 38%),
        var(--panel);
      padding: 18px 24px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 18px;
      align-items: end;
    }

    h1 {
      margin: 0;
      font-size: clamp(26px, 4vw, 44px);
      font-weight: 700;
      letter-spacing: 0;
    }

    .subtitle {
      margin-top: 6px;
      color: var(--muted);
      font-size: 15px;
    }

    .pulse {
      display: inline-flex;
      gap: 10px;
      align-items: center;
      padding: 8px 12px;
      border: 1px solid var(--line);
      background: #fffdf8;
      font-size: 14px;
      white-space: nowrap;
    }

    .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--green);
      box-shadow: 0 0 0 6px rgba(34, 116, 90, 0.14);
    }

    main {
      display: grid;
      grid-template-columns: minmax(0, 1.4fr) minmax(320px, 0.6fr);
      min-height: 0;
    }

    .left, .right {
      padding: 22px;
      min-width: 0;
    }

    .right {
      border-left: 1px solid var(--line);
      background: rgba(255, 250, 241, 0.7);
    }

    .section-title {
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
    }

    .flow {
      position: relative;
      display: grid;
      grid-template-columns: repeat(4, minmax(150px, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }

    .node {
      min-height: 86px;
      border: 1px solid var(--line);
      background: #fffdf8;
      box-shadow: 0 8px 24px var(--shadow);
      padding: 14px;
      display: grid;
      align-content: space-between;
      transition: transform .2s ease, border-color .2s ease, background .2s ease;
    }

    .node.active {
      border-color: var(--coffee);
      background: #fff6e6;
      transform: translateY(-2px);
    }

    .node .label {
      font-size: 18px;
      font-weight: 700;
      overflow-wrap: anywhere;
    }

    .node .kind {
      margin-top: 14px;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .08em;
    }

    .connector {
      height: 1px;
      background: var(--line);
      margin: 6px 0 18px;
    }

    .metrics {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      border: 1px solid var(--line);
      background: #fffdf8;
      margin-bottom: 18px;
    }

    .metric {
      padding: 14px;
      border-right: 1px solid var(--line);
      min-width: 0;
    }

    .metric:last-child { border-right: 0; }

    .metric strong {
      display: block;
      font-size: 24px;
      margin-bottom: 4px;
    }

    .metric span {
      color: var(--muted);
      font-size: 13px;
    }

    .turns {
      display: grid;
      gap: 12px;
    }

    .turn {
      border: 1px solid var(--line);
      background: #fffdf8;
      padding: 14px;
    }

    .turn-head {
      display: flex;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 10px;
    }

    .badge {
      border: 1px solid var(--line);
      padding: 4px 8px;
      font-size: 12px;
      background: var(--paper);
    }

    .complete { color: var(--green); }
    .running { color: var(--blue); }
    .error { color: var(--red); }
    .skipped { color: var(--amber); }

    .event-list {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }

    .event {
      display: grid;
      grid-template-columns: 110px 1fr;
      gap: 10px;
      font-size: 13px;
      border-top: 1px solid #eee4d4;
      padding-top: 8px;
    }

    .event code, .mono {
      font-family: "Cascadia Mono", Consolas, monospace;
      font-size: 12px;
    }

    .side-list {
      display: grid;
      gap: 10px;
      margin-bottom: 22px;
    }

    .row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      border-bottom: 1px solid var(--line);
      padding: 8px 0;
      font-size: 14px;
    }

    .empty {
      color: var(--muted);
      font-size: 14px;
      padding: 12px 0;
    }

    @media (max-width: 980px) {
      main { grid-template-columns: 1fr; }
      .right { border-left: 0; border-top: 1px solid var(--line); }
      .flow { grid-template-columns: repeat(2, minmax(140px, 1fr)); }
      .metrics { grid-template-columns: 1fr; }
      .metric { border-right: 0; border-bottom: 1px solid var(--line); }
      .metric:last-child { border-bottom: 0; }
      header { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div>
        <h1>Milo Flow Monitor</h1>
        <div class="subtitle">Fast router, tools, memory, state, and response trace in one live view.</div>
      </div>
      <div class="pulse"><span class="dot"></span><span id="live-status">connecting</span></div>
    </header>

    <main>
      <section class="left">
        <h2 class="section-title">System Flow</h2>
        <div id="flow" class="flow"></div>
        <div class="connector"></div>
        <div id="metrics" class="metrics"></div>
        <h2 class="section-title">Recent Turns</h2>
        <div id="turns" class="turns"></div>
      </section>

      <aside class="right">
        <h2 class="section-title">Runtime</h2>
        <div id="runtime" class="side-list"></div>
        <h2 class="section-title">Components</h2>
        <div id="components" class="side-list"></div>
        <h2 class="section-title">StateStore</h2>
        <div id="state-store" class="side-list"></div>
      </aside>
    </main>
  </div>

  <script>
    const statusEl = document.getElementById("live-status");
    let lastState = null;

    function text(value) {
      return value === null || value === undefined || value === "" ? "none" : String(value);
    }

    function latestStep(state) {
      const turn = state.turns && state.turns[0];
      if (!turn || !turn.events.length) return "";
      return turn.events[turn.events.length - 1].step;
    }

    function render(state) {
      lastState = state;
      const active = latestStep(state);
      document.getElementById("flow").innerHTML = state.flow.map(node => `
        <div class="node ${node.id === active ? "active" : ""}">
          <div class="label">${node.label}</div>
          <div class="kind">${node.kind}</div>
        </div>
      `).join("");

      const turns = state.turns || [];
      const running = turns.filter(t => t.status === "running").length;
      document.getElementById("metrics").innerHTML = `
        <div class="metric"><strong>${turns.length}</strong><span>recent turn traces</span></div>
        <div class="metric"><strong>${running}</strong><span>currently running</span></div>
        <div class="metric"><strong>${state.state.menu_items}</strong><span>menu items loaded</span></div>
      `;

      document.getElementById("turns").innerHTML = turns.length ? turns.map(turn => `
        <article class="turn">
          <div class="turn-head">
            <strong>#${turn.turn_id} <span class="mono">${turn.session_id}</span></strong>
            <span class="badge ${turn.status}">${turn.status}${turn.duration_ms ? " / " + turn.duration_ms + "ms" : ""}</span>
          </div>
          <div><strong>User:</strong> ${text(turn.user_text)}</div>
          <div><strong>Context:</strong> <span class="mono">${text(turn.context)}</span></div>
          <div><strong>Reply:</strong> ${text(turn.reply_preview)}</div>
          <div><strong>Tool calls:</strong> ${turn.tool_calls.length}</div>
          <div class="event-list">
            ${turn.events.map(event => `
              <div class="event">
                <code class="${event.status}">${event.step}</code>
                <div>${event.detail}</div>
              </div>
            `).join("")}
          </div>
        </article>
      `).join("") : `<div class="empty">No chat turns yet.</div>`;

      document.getElementById("runtime").innerHTML = [
        ["provider", state.runtime.provider],
        ["model", state.runtime.model],
        ["active sessions", state.runtime.active_sessions.length],
        ["max prompt tokens", state.runtime.memory_max_prompt_tokens],
        ["compression trigger", state.runtime.memory_compression_trigger_tokens],
        ["recent kept exact", state.runtime.memory_keep_recent_messages]
      ].map(([k, v]) => `<div class="row"><span>${k}</span><strong>${text(v)}</strong></div>`).join("");

      document.getElementById("components").innerHTML = state.components
        .map(item => `<div class="row"><span>${item.name}</span><strong>${item.status}</strong></div>`)
        .join("");

      const carts = state.state.carts.length ? state.state.carts
        .map(cart => `<div class="row"><span>${cart.session_id}</span><strong>${cart.items} item(s) / INR ${cart.total_inr}</strong></div>`)
        .join("") : `<div class="empty">No carts yet.</div>`;
      const orders = state.state.orders.length ? state.state.orders
        .map(order => `<div class="row"><span>${order.order_id}</span><strong>${order.status}</strong></div>`)
        .join("") : `<div class="empty">No orders yet.</div>`;
      document.getElementById("state-store").innerHTML = carts + orders;
    }

    async function poll() {
      const res = await fetch("/debug/flow/state");
      render(await res.json());
      statusEl.textContent = "live";
    }

    if ("EventSource" in window) {
      const events = new EventSource("/debug/flow/events");
      events.onmessage = event => {
        render(JSON.parse(event.data));
        statusEl.textContent = "live";
      };
      events.onerror = () => {
        statusEl.textContent = "reconnecting";
        setTimeout(poll, 1200);
      };
    } else {
      setInterval(poll, 1500);
      poll();
    }
  </script>
</body>
</html>
"""
