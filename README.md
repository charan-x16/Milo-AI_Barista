# Milo Barista Backend

## What This Is

Milo Barista is a 5-agent cafe ordering system built with AgentScope,
FastAPI, Pydantic v2, Qdrant, and SQL-backed short-term memory.

It uses one Orchestrator agent as the supervisor, with four specialist
ReActAgents exposed to it as tools:

- Product Search Agent
- Cart Management Agent
- Order Management Agent
- Customer Support Agent

## Architecture

```text
Client / UI
    |
    v
FastAPI API
src/cafe/api/main.py
    |
    v
Per-session Orchestrator
src/cafe/agents/orchestrator.py
src/cafe/agents/session_manager.py
    |
    v
Specialist agents as tools
src/cafe/agents/specialist_tools.py
    |
    +--> Product Search Agent
    |    prompt: src/cafe/agents/agent_md/product_search.md
    |    skill:  src/cafe/skills/menu_navigation/SKILL.md
    |    tools:  src/cafe/tools/product_tools.py
    |
    +--> Cart Management Agent
    |    prompt: src/cafe/agents/agent_md/cart_management.md
    |    skill:  src/cafe/skills/cart_etiquette/SKILL.md
    |    tools:  src/cafe/tools/cart_tools.py
    |
    +--> Order Management Agent
    |    prompt: src/cafe/agents/agent_md/order_management.md
    |    skill:  src/cafe/skills/order_lifecycle/SKILL.md
    |    tools:  src/cafe/tools/order_tools.py
    |
    +--> Customer Support Agent
         prompt: src/cafe/agents/agent_md/customer_support.md
         skill:  src/cafe/skills/support_playbook/SKILL.md
         tools:  src/cafe/tools/support_tools.py

Domain tools
    |
    v
Synchronous services
src/cafe/services/
    |
    v
Pydantic models + hybrid state
src/cafe/models/
src/cafe/core/state.py
    |
    +--> SQL persistence
         src/cafe/agents/memory/
```

## Quickstart

```bash
cp .env.example .env
# Set LLM_PROVIDER, LLM_MODEL, and LLM_API_KEY in .env

uv sync
uv run alembic upgrade head
uv run uvicorn cafe.api.main:app --reload
```

The API will be available at `http://127.0.0.1:8000`.

Interactive docs are available at `http://127.0.0.1:8000/docs`.

## Database Memory

Conversation memory and frontend history data are stored in SQL using:

- `users`
- `conversations`
- `conversation_messages`
- `conversation_summaries`
- `memory_summaries`
- `menu_items`
- `carts`
- `cart_items`
- `orders`
- `order_items`

Configure the database with `MEMORY_DATABASE_URL` in `.env`. Local SQLite
works by default; Neon/Postgres should run migrations before serving traffic:

```bash
uv run alembic upgrade head
```

To test migrations against a temporary database without using `.env`:

```bash
uv run alembic -x database_url=sqlite+aiosqlite:///./tmp/alembic-test.sqlite3 upgrade head
```

Recent visible chat messages stay exact. Cumulative checkpoint summaries are
stored in `memory_summaries` every `MEMORY_SUMMARY_INTERVAL_MESSAGES` visible
messages and are passed back into the Orchestrator on future turns alongside
the `MEMORY_RECENT_MESSAGES` visible-message window. The older
`conversation_summaries` table is retained for compatibility.

## Frontend APIs

Base URL in local development:

```text
http://127.0.0.1:8000
```

Primary frontend endpoints:

- `POST /sessions`
  Creates a new chat session.
- `POST /chat`
  Sends one user message and returns the assistant reply.
- `GET /sessions/{session_id}/cart`
  Returns the current cart for the session.
- `GET /sessions/{session_id}/orders`
  Returns orders created in the session.
- `GET /users/{user_id}/conversations`
  Returns recent conversations for sidebar/history UI.
- `GET /sessions/{session_id}/messages?user_id=anonymous`
  Returns visible chat messages for one conversation.
- `POST /sessions/{session_id}/reset`
  Dev helper that clears SQL memory and in-process state for one session.

Example chat request:

```json
{
  "user_id": "anonymous",
  "session_id": "abc123",
  "message": "show me the menu"
}
```

Example frontend flow:

1. `POST /sessions`
2. Save returned `session_id`
3. `POST /chat` with that `session_id`
4. Use `GET /users/{user_id}/conversations` for recent chats
5. Use `GET /sessions/{session_id}/messages` when reopening an old chat
6. Use cart and order endpoints to drive side panels

## Sharing Locally

Run the API locally:

```bash
uv run uvicorn cafe.api.main:app --host 127.0.0.1 --port 8000
```

Expose it with ngrok:

```powershell
& "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ngrok.exe" http 8000
```

Use the generated `https://...ngrok-free.app` URL as the frontend base URL.
The OpenAPI schema is available at `/openapi.json`, and the interactive docs
are available at `/docs`.

## RAG Indexing

The Product Search Agent retrieves core menu facts from
`src/cafe/Docs/BTB_Menu_Enhanced.md` and taste/ingredient/allergen attributes
from `src/cafe/Docs/BTB_Menu_Attributes.md`.
The Customer Support Agent retrieves from `src/cafe/Docs/BTB_Company_Policies.md`.
Each document is indexed into its own Qdrant collection. Embeddings use
FastEmbed with `BAAI/bge-small-en-v1.5` by default.

```bash
# Start Qdrant separately, then create the two collections:
uv run python scripts/setup_qdrant.py

# Index both documents:
uv run python scripts/index_rag.py
```

Configure Qdrant and embeddings through `.env`:

```bash
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=

QDRANT_URL=http://localhost:6333
QDRANT_PRODUCT_COLLECTION=btb_product_menu
QDRANT_MENU_ATTRIBUTES_COLLECTION=btb_menu_attributes
QDRANT_SUPPORT_COLLECTION=btb_company_policies
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
EMBEDDING_DIMENSIONS=384
```

## Current Chat Flow

1. `POST /chat` validates the request in `src/cafe/api/main.py`.
2. The API loads the SQL-backed per-session Orchestrator through `SessionManager`.
3. The API sends the user message to the Orchestrator with the active `session_id`.
4. The Orchestrator delegates menu, cart, order, or support work to specialist tools in `src/cafe/agents/specialist_tools.py`.
5. Specialists call grouped domain tools in `src/cafe/tools/`.
6. Services update `StateStore`; SQL memory persists messages, cart snapshots, and order snapshots.
7. The API returns the Orchestrator's final reply.

## Folder Layout

```text
backend/
|- scripts/
|  |- setup_qdrant.py
|  `- index_rag.py
|- src/
|  `- cafe/
|     |- agents/
|     |  |- agent_md/
|     |  |- memory/
|     |  |- specialists/
|     |  |- orchestrator.py
|     |  |- prompts.py
|     |  |- session_manager.py
|     |  `- specialist_tools.py
|     |- api/
|     |  |- debug.py
|     |  |- debug_dashboard.py
|     |  |- main.py
|     |  `- schemas.py
|     |- core/
|     |  |- debug_trace.py
|     |  |- state.py
|     |  `- validator.py
|     |- models/
|     |- services/
|     |- skills/
|     `- tools/
|- migrations/
|- tests/
|- .env.example
|- alembic.ini
|- pyproject.toml
`- README.md
```

## Testing

Offline tests:

```bash
uv run pytest -v -k "not end_to_end"
```

All tests, including the live LLM end-to-end test:

```bash
uv run pytest -v
```

The full suite needs `LLM_API_KEY` for `tests/test_api.py::test_chat_end_to_end`.

## Phase 2 (TODO)

Redesign the chat flow and reintroduce validation/critic behavior behind a
clearer component boundary.

## Not In This Prototype

- Auth
- Streaming
- Payments
- Real MCP
