import pytest

from cafe.agents.agent_cache import SpecialistAgentCache


class FakeMemory:
    def __init__(self) -> None:
        self.content = []

    async def clear(self) -> None:
        self.content.clear()

    async def add(self, item) -> None:
        self.content.append(item)

    async def size(self) -> int:
        return len(self.content)


class FakeAgent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.memory = FakeMemory()


def _fake_cache(pool_size: int = 1) -> tuple[SpecialistAgentCache, dict[str, int]]:
    calls: dict[str, int] = {}

    def factory(name: str):
        def build() -> FakeAgent:
            calls[name] = calls.get(name, 0) + 1
            return FakeAgent(name)

        return build

    cache = SpecialistAgentCache(
        {
            "product": factory("ProductSearchAgent"),
            "cart": factory("CartManagementAgent"),
            "order": factory("OrderManagementAgent"),
            "support": factory("CustomerSupportAgent"),
        },
        pool_size=pool_size,
    )
    return cache, calls


@pytest.mark.asyncio
async def test_cache_initialization_returns_same_instances():
    cache, calls = _fake_cache()

    cache.initialize()
    product_agent = await cache.get_agent("product")
    product_agent_2 = await cache.get_agent("product")

    assert product_agent is product_agent_2
    assert product_agent.name == "ProductSearchAgent"
    assert calls["ProductSearchAgent"] == 1
    assert (await cache.get_agent("cart")).name == "CartManagementAgent"
    assert (await cache.get_agent("order")).name == "OrderManagementAgent"
    assert (await cache.get_agent("support")).name == "CustomerSupportAgent"


@pytest.mark.asyncio
async def test_cache_requires_initialization():
    cache, _ = _fake_cache()

    with pytest.raises(RuntimeError, match="not initialized"):
        await cache.get_agent("product")


@pytest.mark.asyncio
async def test_unknown_agent_type():
    cache, _ = _fake_cache()
    cache.initialize()

    with pytest.raises(ValueError, match="Unknown agent type"):
        await cache.get_agent("unknown_agent")


@pytest.mark.asyncio
async def test_agent_memory_cleared_between_requests():
    cache, _ = _fake_cache()
    cache.initialize()

    agent = await cache.get_agent("product")
    await agent.memory.add("test message 1")
    assert await agent.memory.size() == 1

    agent_again = await cache.get_agent("product")

    assert agent_again is agent
    assert await agent_again.memory.size() == 0


@pytest.mark.asyncio
async def test_agent_memory_cleared_after_lease():
    cache, _ = _fake_cache()
    cache.initialize()

    async with cache.acquire_agent("product") as agent:
        await agent.memory.add("request-local scratchpad")
        assert await agent.memory.size() == 1

    agent_again = await cache.get_agent("product")

    assert agent_again is agent
    assert await agent_again.memory.size() == 0


@pytest.mark.asyncio
async def test_agent_pool_allows_two_concurrent_leases():
    cache, calls = _fake_cache(pool_size=2)
    cache.initialize()

    async with cache.acquire_agent("support") as first:
        async with cache.acquire_agent("support") as second:
            assert first is not second

    assert calls["CustomerSupportAgent"] == 2
