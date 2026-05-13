"""Tests conftest module."""

import pytest
import pytest_asyncio

from cafe.agents.memory import storage as memory_storage
from cafe.config import get_settings
from cafe.core.background_tasks import drain_background_tasks
from cafe.core.state import get_store, reset_store


@pytest.fixture(autouse=True)
def isolated_memory_database(tmp_path, monkeypatch):
    """Verify isolated memory database.

    Args:
        - tmp_path: Any - The tmp path value.
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - The return value.
    """
    monkeypatch.setenv(
        "MEMORY_DATABASE_URL",
        f"sqlite+aiosqlite:///{(tmp_path / 'memory.sqlite3').as_posix()}",
    )
    get_settings.cache_clear()
    memory_storage._reset_storage_runtime_cache()
    yield
    get_settings.cache_clear()
    memory_storage._reset_storage_runtime_cache()


@pytest_asyncio.fixture(autouse=True)
async def drain_background_work():
    """Verify drain background work.

    Returns:
        - return None - The return value.
    """
    yield
    await drain_background_tasks(timeout=2.0)


@pytest.fixture
def store():
    """Verify store.

    Returns:
        - return None - The return value.
    """
    reset_store()
    s = get_store()
    yield s
    reset_store()
