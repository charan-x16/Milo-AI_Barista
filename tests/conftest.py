import pytest

from cafe.agents.memory import storage as memory_storage
from cafe.config import get_settings
from cafe.core.state import get_store, reset_store


@pytest.fixture(autouse=True)
def isolated_memory_database(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "MEMORY_DATABASE_URL",
        f"sqlite+aiosqlite:///{(tmp_path / 'memory.sqlite3').as_posix()}",
    )
    get_settings.cache_clear()
    memory_storage._ENGINE = None
    yield
    get_settings.cache_clear()
    memory_storage._ENGINE = None


@pytest.fixture
def store():
    reset_store()
    s = get_store()
    yield s
    reset_store()
