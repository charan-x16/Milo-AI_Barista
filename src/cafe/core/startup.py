"""Startup warmup for resources that must not be built on the chat hot path."""

from __future__ import annotations

import logging

from cafe.agents.memory import ensure_storage_ready
from cafe.agents.orchestrator import _make_toolkit as make_orchestrator_toolkit
from cafe.agents.specialists.cart_management_agent import (
    _make_toolkit as make_cart_toolkit,
)
from cafe.agents.specialists.customer_support_agent import (
    _make_toolkit as make_support_toolkit,
)
from cafe.agents.specialists.order_management_agent import (
    _make_toolkit as make_order_toolkit,
)
from cafe.agents.specialists.product_search_agent import (
    _make_toolkit as make_product_toolkit,
)
from cafe.config import Settings
from cafe.services.menu_index_service import (
    build_menu_index,
    build_menu_item_match_index,
    build_menu_match_aliases,
    get_menu_categories,
)
from cafe.services.rag_service import warm_rag_service

log = logging.getLogger(__name__)


def initialize_runtime_resources(settings: Settings) -> None:
    """Preload shared resources once at process startup.

    Args:
        - settings: Settings - The settings value.

    Returns:
        - return None - The return value.
    """
    _warm_menu_caches()
    _warm_tool_registry()
    _warm_rag(settings)


async def initialize_persistent_storage(settings: Settings) -> None:
    """Create SQL schema outside the first chat request.

    Args:
        - settings: Settings - The settings value.

    Returns:
        - return None - The return value.
    """
    await ensure_storage_ready(settings)


def _warm_menu_caches() -> None:
    """Handle warm menu caches.

    Returns:
        - return None - The return value.
    """
    build_menu_index()
    build_menu_match_aliases()
    build_menu_item_match_index()
    get_menu_categories(include_items=True)
    get_menu_categories(include_items=False)


def _warm_tool_registry() -> None:
    # Warm the cached AgentScope toolkits so skill registration does not happen
    # inside the first customer chat turn.
    """Handle warm tool registry.

    Returns:
        - return None - The return value.
    """
    make_orchestrator_toolkit()
    make_product_toolkit()
    make_cart_toolkit()
    make_order_toolkit()
    make_support_toolkit()


def _warm_rag(settings: Settings) -> None:
    """Handle warm rag.

    Args:
        - settings: Settings - The settings value.

    Returns:
        - return None - The return value.
    """
    try:
        warm_rag_service(settings)
    except Exception as exc:
        log.warning("RAG warmup skipped: %s", exc)
