import pytest

from cafe.core.state import get_store, reset_store


@pytest.fixture
def store():
    reset_store()
    s = get_store()
    yield s
    reset_store()
