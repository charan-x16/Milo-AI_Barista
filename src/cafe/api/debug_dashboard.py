"""HTML for the live architecture dashboard."""


DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Milo Architecture Console</title>
  <style>
    :root {
      --paper: #f4efe6;
      --ink: #181512;
      --muted: #6f675b;
      --panel: #fffaf0;
      --line: #d4c6b2;
      --coffee: #6f432d;
      --green: #1f7a5b;
      --blue: #245f78;
      --amber: #b26722;
      --red: #a33a31;
      --violet: #5b4b8a;
      --shadow: rgba(24, 21, 18, 0.10);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      background:
        linear-gradient(90deg, rgba(24, 21, 18, .035) 1px, transparent 1px),
        linear-gradient(rgba(24, 21, 18, .035) 1px, transparent 1px),
        var(--paper);
      background-size: 24px 24px;
      color: var(--ink);
      font-family: Cambria, Georgia, serif;
    }

    button, input, textarea {
      font: inherit;
    }

    .topbar {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 18px;
      align-items: center;
      padding: 18px 22px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 250, 240, .94);
      position: sticky;
      top: 0;
      z-index: 5;
    }

    h1 {
      margin: 0;
      font-size: clamp(25px, 3vw, 42px);
      letter-spacing: 0;
      line-height: 1;
    }

    .sub {
      margin-top: 6px;
      color: var(--muted);
      font-size: 14px;
    }

    .live {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      border: 1px solid var(--line);
      background: #fffdf8;
      padding: 9px 12px;
      white-space: nowrap;
      box-shadow: 0 8px 22px var(--shadow);
    }

    .live-dot {
      width: 10px;
      height: 10px;
      background: var(--green);
      border-radius: 50%;
      box-shadow: 0 0 0 6px rgba(31, 122, 91, .16);
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      min-height: calc(100vh - 82px);
    }

    .main {
      padding: 20px;
      min-width: 0;
    }

    .side {
      border-left: 1px solid var(--line);
      background: rgba(255, 250, 240, .78);
      padding: 20px;
      min-width: 0;
    }

    .title {
      margin: 0 0 10px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: .09em;
      text-transform: uppercase;
    }

    .board {
      border: 1px solid var(--line);
      background: rgba(255, 253, 248, .88);
      box-shadow: 0 16px 42px var(--shadow);
      padding: 18px;
    }

    .diagram {
      display: grid;
      gap: 0;
      max-width: 820px;
      margin: 0 auto;
    }

    .flow-node {
      position: relative;
      border: 1px solid var(--line);
      background: #fffdf8;
      padding: 14px;
      box-shadow: 0 8px 22px var(--shadow);
      transition: transform .18s ease, border-color .18s ease, background .18s ease;
      z-index: 2;
    }

    .flow-node.active {
      transform: translateY(-3px);
      border-color: var(--coffee);
      background: #fff1dc;
      outline: 3px solid rgba(111, 67, 45, .12);
    }

    .flow-node.done {
      border-color: rgba(31, 122, 91, .45);
    }

    .node-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: start;
      margin-bottom: 10px;
    }

    .node-name {
      font-size: 18px;
      font-weight: 700;
      line-height: 1.05;
      overflow-wrap: anywhere;
    }

    .chip {
      font-family: "Cascadia Mono", Consolas, monospace;
      color: var(--muted);
      font-size: 11px;
      border: 1px solid var(--line);
      padding: 3px 6px;
      background: var(--paper);
      white-space: nowrap;
    }

    .node-text {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.35;
    }

    .flow-arrow {
      height: 30px;
      display: grid;
      place-items: center;
      color: var(--coffee);
      font-size: 24px;
      line-height: 1;
    }

    .flow-branch {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
      position: relative;
    }

    .flow-branch::before {
      content: "";
      position: absolute;
      top: -15px;
      left: 25%;
      right: 25%;
      border-top: 1px solid rgba(111, 67, 45, .45);
    }

    .flow-branch-item {
      display: grid;
      gap: 0;
    }

    .flow-note {
      margin: 10px 0 0;
      color: var(--muted);
      font-size: 13px;
      text-align: center;
    }

    .workbench {
      display: grid;
      grid-template-columns: minmax(280px, .8fr) minmax(0, 1.2fr);
      gap: 16px;
      margin-top: 16px;
    }

    .panel {
      border: 1px solid var(--line);
      background: rgba(255, 253, 248, .92);
      box-shadow: 0 10px 26px var(--shadow);
      padding: 14px;
      min-width: 0;
    }

    .chat-log {
      height: 240px;
      overflow: auto;
      border: 1px solid var(--line);
      background: #fffdf8;
      padding: 10px;
      display: grid;
      gap: 8px;
      align-content: start;
      margin-bottom: 10px;
    }

    .bubble {
      padding: 9px 10px;
      max-width: 92%;
      border: 1px solid var(--line);
      background: var(--paper);
      font-size: 14px;
      line-height: 1.35;
      white-space: pre-wrap;
    }

    .bubble.user {
      justify-self: end;
      background: #e9f3ef;
      border-color: rgba(31, 122, 91, .35);
    }

    .bubble.agent {
      justify-self: start;
      background: #fff2df;
      border-color: rgba(178, 103, 34, .35);
    }

    .chat-form {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
    }

    .chat-form textarea {
      min-height: 46px;
      resize: vertical;
      border: 1px solid var(--line);
      background: #fffdf8;
      padding: 10px;
      color: var(--ink);
    }

    .chat-form button, .small-button {
      border: 1px solid var(--coffee);
      background: var(--coffee);
      color: white;
      padding: 10px 14px;
      cursor: pointer;
    }

    .chat-form button:disabled {
      opacity: .55;
      cursor: wait;
    }

    .timeline {
      max-height: 330px;
      overflow: auto;
      display: grid;
      gap: 8px;
    }

    .event {
      display: grid;
      grid-template-columns: 120px 1fr;
      gap: 10px;
      border-bottom: 1px solid var(--line);
      padding: 8px 0;
      font-size: 13px;
    }

    .event code, .mono {
      font-family: "Cascadia Mono", Consolas, monospace;
      font-size: 12px;
    }

    .complete { color: var(--green); }
    .running { color: var(--blue); }
    .error { color: var(--red); }
    .skipped { color: var(--amber); }

    .kv {
      display: grid;
      gap: 9px;
      margin-bottom: 22px;
    }

    .row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--line);
      font-size: 14px;
    }

    .row strong {
      text-align: right;
      overflow-wrap: anywhere;
    }

    .empty {
      color: var(--muted);
      font-size: 14px;
      padding: 10px 0;
    }

    .caption {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.4;
      margin: 0 0 12px;
    }

    @media (max-width: 1120px) {
      .layout { grid-template-columns: 1fr; }
      .side { border-left: 0; border-top: 1px solid var(--line); }
      .workbench { grid-template-columns: 1fr; }
    }

    @media (max-width: 640px) {
      .topbar { grid-template-columns: 1fr; }
      .chat-form { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div>
      <h1>Milo Architecture Console</h1>
      <div class="sub">Live flowchart, chat runner, trace timeline, memory limits, and state snapshots.</div>
    </div>
    <div class="live"><span class="live-dot"></span><span id="live-status">connecting</span></div>
  </header>

  <div class="layout">
    <main class="main">
      <h2 class="title">Architecture Flow</h2>
      <section class="board">
        <div id="flow"></div>
      </section>

      <section class="workbench">
        <div class="panel">
          <h2 class="title">Chat Runner</h2>
          <p class="caption">Send a message here and watch each architecture component light up as the backend handles the turn.</p>
          <div id="chat-log" class="chat-log"></div>
          <form id="chat-form" class="chat-form">
            <textarea id="message" placeholder="Try: Add one cappuccino to my cart"></textarea>
            <button id="send" type="submit">Send</button>
          </form>
        </div>

        <div class="panel">
          <h2 class="title">Live Trace</h2>
          <div id="timeline" class="timeline"></div>
        </div>
      </section>
    </main>

    <aside class="side">
      <h2 class="title">Runtime</h2>
      <div id="runtime" class="kv"></div>
      <h2 class="title">Components</h2>
      <div id="components" class="kv"></div>
      <h2 class="title">StateStore</h2>
      <div id="state-store" class="kv"></div>
      <button id="reset" class="small-button" type="button">Reset Trace + State</button>
    </aside>
  </div>

  <script>
    const statusEl = document.getElementById("live-status");
    const flowEl = document.getElementById("flow");
    const chatLogEl = document.getElementById("chat-log");
    const formEl = document.getElementById("chat-form");
    const messageEl = document.getElementById("message");
    const sendEl = document.getElementById("send");
    const resetEl = document.getElementById("reset");
    let currentSessionId = localStorage.getItem("milo_debug_session_id") || "";
    let latestState = null;

    const descriptions = {
      client: "Browser, API client, or test sends a chat request.",
      api: "FastAPI validates request and calls the turn runtime.",
      turn_runtime: "Runs routing, specialist execution, tracing, and final validation.",
      context: "Adds session id, cart snapshot, and recent orders.",
      session_manager: "Reuses the per-session Orchestrator.",
      orchestrator: "Routes the request to the owning specialist.",
      specialists: "Product, cart, order, or support agent returns the answer.",
      tools: "Typed functions that mutate or read domain state.",
      state_store: "In-memory carts, orders, and menu.",
      critic: "Optional validation hook after mutating actions.",
      response: "Final payload returned to the client."
    };

    const mainFlow = ["client", "api", "turn_runtime", "orchestrator", "specialists", "tools", "state_store", "critic", "response"];
    const branchFlow = ["context", "session_manager"];

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
      }[char]));
    }

    function latestTurn(state) {
      return state.turns && state.turns.length ? state.turns[0] : null;
    }

    function latestStep(state) {
      const turn = latestTurn(state);
      if (!turn || !turn.events.length) return "";
      return turn.events[turn.events.length - 1].step;
    }

    function completedSteps(state) {
      const turn = latestTurn(state);
      return new Set((turn?.events || []).map(event => event.step));
    }

    function renderFlow(state) {
      const active = latestStep(state);
      const done = completedSteps(state);
      const nodeById = new Map(state.flow.map(node => [node.id, node]));

      flowEl.className = "diagram";
      flowEl.innerHTML = mainFlow.map((id, index) => {
        const nodeHtml = renderNode(id, nodeById, active, done);
        const branchHtml = id === "turn_runtime" ? renderBranch(nodeById, active, done) : "";
        const noteHtml = id === "state_store" ? `<div class="flow-note">State changes are read back by future turns through the context snapshot.</div>` : "";
        const arrowHtml = index < mainFlow.length - 1 ? `<div class="flow-arrow">↓</div>` : "";
        return `${nodeHtml}${branchHtml}${noteHtml}${arrowHtml}`;
      }).join("");
    }

    function renderBranch(nodeById, active, done) {
      return `
        <div class="flow-arrow">↓</div>
        <div class="flow-branch">
          ${branchFlow.map(id => `
            <div class="flow-branch-item">
              ${renderNode(id, nodeById, active, done)}
            </div>
          `).join("")}
        </div>
      `;
    }

    function renderNode(id, nodeById, active, done) {
      const node = nodeById.get(id);
      return `
        <div class="flow-node ${id === active ? "active" : ""} ${done.has(id) ? "done" : ""}">
          <div class="node-head">
            <div class="node-name">${escapeHtml(node.label)}</div>
            <span class="chip">${escapeHtml(node.kind)}</span>
          </div>
          <div class="node-text">${escapeHtml(descriptions[id] || "")}</div>
        </div>
      `;
    }

    function renderTrace(state) {
      const turn = latestTurn(state);
      if (!turn) {
        document.getElementById("timeline").innerHTML = `<div class="empty">No chat turns yet.</div>`;
        return;
      }
      document.getElementById("timeline").innerHTML = `
        <div class="row"><span>turn</span><strong>#${turn.turn_id} / ${escapeHtml(turn.status)}</strong></div>
        <div class="row"><span>session</span><strong class="mono">${escapeHtml(turn.session_id)}</strong></div>
        <div class="row"><span>context</span><strong class="mono">${escapeHtml(turn.context || "none")}</strong></div>
        ${turn.events.map(event => `
          <div class="event">
            <code class="${event.status}">${escapeHtml(event.step)}</code>
            <div>${escapeHtml(event.detail)}</div>
          </div>
        `).join("")}
      `;
    }

    function renderSide(state) {
      document.getElementById("runtime").innerHTML = [
        ["model", state.runtime.model],
        ["active sessions", state.runtime.active_sessions.length],
        ["prompt cap", state.runtime.memory_max_prompt_tokens],
        ["compression", state.runtime.memory_compression_trigger_tokens],
        ["exact recent", state.runtime.memory_keep_recent_messages]
      ].map(([key, value]) => `<div class="row"><span>${key}</span><strong>${escapeHtml(value)}</strong></div>`).join("");

      document.getElementById("components").innerHTML = state.components
        .map(item => `<div class="row"><span>${escapeHtml(item.name)}</span><strong>${escapeHtml(item.status)}</strong></div>`)
        .join("");

      const carts = state.state.carts.length
        ? state.state.carts.map(cart => `<div class="row"><span class="mono">${escapeHtml(cart.session_id)}</span><strong>${cart.items} item(s), INR ${cart.total_inr}</strong></div>`).join("")
        : `<div class="empty">No carts yet.</div>`;
      const orders = state.state.orders.length
        ? state.state.orders.map(order => `<div class="row"><span class="mono">${escapeHtml(order.order_id)}</span><strong>${escapeHtml(order.status)}</strong></div>`).join("")
        : `<div class="empty">No orders yet.</div>`;
      document.getElementById("state-store").innerHTML = carts + orders;
    }

    function render(state) {
      latestState = state;
      renderFlow(state);
      renderTrace(state);
      renderSide(state);
      statusEl.textContent = "live";
    }

    function addBubble(role, text) {
      const div = document.createElement("div");
      div.className = `bubble ${role}`;
      div.textContent = text;
      chatLogEl.appendChild(div);
      chatLogEl.scrollTop = chatLogEl.scrollHeight;
    }

    async function ensureSession() {
      if (currentSessionId) return currentSessionId;
      const res = await fetch("/sessions", { method: "POST" });
      const data = await res.json();
      currentSessionId = data.session_id;
      localStorage.setItem("milo_debug_session_id", currentSessionId);
      return currentSessionId;
    }

    formEl.addEventListener("submit", async event => {
      event.preventDefault();
      const message = messageEl.value.trim();
      if (!message) return;
      sendEl.disabled = true;
      addBubble("user", message);
      messageEl.value = "";
      try {
        const sessionId = await ensureSession();
        const res = await fetch("/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId, message })
        });
        const data = await res.json();
        addBubble("agent", data.reply || JSON.stringify(data));
      } catch (error) {
        addBubble("agent", `Request failed: ${error}`);
      } finally {
        sendEl.disabled = false;
      }
    });

    resetEl.addEventListener("click", async () => {
      await fetch("/admin/reset", { method: "POST" });
      currentSessionId = "";
      localStorage.removeItem("milo_debug_session_id");
      chatLogEl.innerHTML = "";
      await poll();
    });

    async function poll() {
      const res = await fetch("/debug/flow/state");
      render(await res.json());
    }

    if ("EventSource" in window) {
      const source = new EventSource("/debug/flow/events");
      source.onmessage = event => render(JSON.parse(event.data));
      source.onerror = () => {
        statusEl.textContent = "reconnecting";
        setTimeout(poll, 1200);
      };
    } else {
      setInterval(poll, 1200);
      poll();
    }

    window.addEventListener("resize", () => latestState && renderFlow(latestState));
    poll();
  </script>
</body>
</html>
"""
