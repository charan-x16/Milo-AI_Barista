"""Global cache for specialist agents to avoid recreation overhead."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from inspect import isawaitable
from threading import Lock
from typing import Callable

from agentscope.agent import ReActAgent

from cafe.agents.specialists.cart_management_agent import make_cart_management_agent
from cafe.agents.specialists.customer_support_agent import (
    make_customer_support_agent,
)
from cafe.agents.specialists.order_management_agent import make_order_management_agent
from cafe.agents.specialists.product_search_agent import make_product_search_agent


logger = logging.getLogger(__name__)

AgentFactory = Callable[[], ReActAgent]
DEFAULT_POOL_SIZE = 2


class SpecialistAgentCache:
    """Create specialist pools, then lease agents safely across requests."""

    def __init__(
        self,
        factories: dict[str, AgentFactory] | None = None,
        *,
        pool_size: int = DEFAULT_POOL_SIZE,
    ) -> None:
        self._factories = factories or {
            "product": make_product_search_agent,
            "cart": make_cart_management_agent,
            "order": make_order_management_agent,
            "support": make_customer_support_agent,
        }
        self._pool_size = max(pool_size, 1)
        self._agents: dict[str, list[ReActAgent]] = {}
        self._available: dict[str, asyncio.Queue[ReActAgent]] = {}
        self._initialized = False
        self._init_lock = Lock()

    def initialize(self) -> None:
        """Create specialist agent pools and load their skills once."""
        with self._init_lock:
            if self._initialized:
                logger.debug("SpecialistAgentCache already initialized")
                return

            logger.info("Initializing specialist agent cache...")
            self._agents = {
                agent_type: [factory() for _ in range(self._pool_size)]
                for agent_type, factory in self._factories.items()
            }
            self._available = {}
            for agent_type, agents in self._agents.items():
                queue: asyncio.Queue[ReActAgent] = asyncio.Queue()
                for agent in agents:
                    queue.put_nowait(agent)
                self._available[agent_type] = queue
            self._initialized = True
            logger.info(
                "Specialist agent cache initialized with %d types, %d total agents: %s",
                len(self._agents),
                sum(len(agents) for agents in self._agents.values()),
                list(self._agents.keys()),
            )

    async def get_agent(self, agent_type: str) -> ReActAgent:
        """Return a cached agent after clearing its per-request memory."""
        self._require_initialized()
        agent = self._lookup(agent_type)[0]
        await _clear_agent_memory(agent)
        return agent

    @asynccontextmanager
    async def acquire_agent(self, agent_type: str):
        """Lease one cached agent so concurrent requests cannot share memory."""
        self._require_initialized()
        self._lookup(agent_type)
        queue = self._available[agent_type]

        agent = await queue.get()
        try:
            await _clear_agent_memory(agent)
            yield agent
        finally:
            await _clear_agent_memory(agent)
            queue.put_nowait(agent)

    def clear_memories(self) -> None:
        """Best-effort sync memory clear for admin resets and tests."""
        for agents in self._agents.values():
            for agent in agents:
                memory = getattr(agent, "memory", None)
                content = getattr(memory, "content", None)
                if hasattr(content, "clear"):
                    content.clear()

    def reset(self) -> None:
        """Drop cached agents. Intended for tests only."""
        with self._init_lock:
            self._agents.clear()
            self._available.clear()
            self._initialized = False

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError(
                "SpecialistAgentCache not initialized. "
                "Call initialize() at application startup.",
            )

    def _lookup(self, agent_type: str) -> list[ReActAgent]:
        if agent_type not in self._agents:
            raise ValueError(
                f"Unknown agent type: {agent_type}. "
                f"Must be one of {list(self._agents.keys())}",
            )
        return self._agents[agent_type]


async def _clear_agent_memory(agent: ReActAgent) -> None:
    memory = getattr(agent, "memory", None)
    if memory is None:
        return

    clear_result = memory.clear()
    if isawaitable(clear_result):
        await clear_result


_cache = SpecialistAgentCache()


def initialize_agent_cache() -> None:
    """Initialize the global agent cache. Call once at app startup."""
    _cache.initialize()


async def get_cached_agent(agent_type: str) -> ReActAgent:
    """Get a cached specialist agent by type."""
    return await _cache.get_agent(agent_type)


@asynccontextmanager
async def acquire_cached_agent(agent_type: str):
    """Lease a cached specialist agent by type."""
    async with _cache.acquire_agent(agent_type) as agent:
        yield agent


def clear_agent_cache_memories() -> None:
    """Clear cached specialist memories without rebuilding agents."""
    _cache.clear_memories()


def reset_agent_cache() -> None:
    """Drop cached specialists. Intended for tests only."""
    _cache.reset()


def is_agent_cache_ready() -> bool:
    """Check if the global cache has initialized successfully."""
    return _cache.is_initialized
