from __future__ import annotations

from dataclasses import dataclass

from cafe.core.state import get_store
from cafe.services.menu_index_service import (
    browse_menu_query,
    extract_price_limit,
    filter_price_items,
    format_price_filter_query,
    format_price_list_query,
    is_context_dependent_price_request,
    is_price_list_request,
    price_items_for_query,
    requested_section_from_query,
)


@dataclass(frozen=True)
class DeterministicMenuReply:
    reply: str
    route: str
    tool_calls: list[dict]


def _query_with_last_scope(session_id: str, user_text: str) -> str:
    if (
        is_context_dependent_price_request(user_text)
        and (last_scope := get_store().last_menu_scope.get(session_id))
    ):
        return f"{user_text} for {last_scope}"
    return user_text


def _remember_scope(session_id: str, user_text: str) -> None:
    section = requested_section_from_query(user_text)
    if section:
        get_store().last_menu_scope[session_id] = section


def deterministic_menu_reply(
    session_id: str,
    user_text: str,
) -> DeterministicMenuReply | None:
    """Return exact menu answers that do not need LLM reasoning.

    This handles structured menu browsing and price lookup, where the canonical
    menu index and price tables are more reliable than asking an LLM to choose,
    preserve, and rephrase tool results.
    """
    scoped_query = _query_with_last_scope(session_id, user_text)
    max_price = extract_price_limit(scoped_query)
    if max_price is not None:
        items = filter_price_items(max_price=max_price, query=scoped_query)
        return DeterministicMenuReply(
            reply=format_price_filter_query(scoped_query, max_price=max_price),
            route="deterministic_price_filter",
            tool_calls=[
                {
                    "name": "filter_current_menu_by_price",
                    "input": {"query": scoped_query, "max_price": max_price},
                    "count": len(items),
                }
            ],
        )

    if is_price_list_request(scoped_query):
        items = price_items_for_query(scoped_query)
        return DeterministicMenuReply(
            reply=format_price_list_query(scoped_query),
            route="deterministic_price_list",
            tool_calls=[
                {
                    "name": "list_current_menu_prices",
                    "input": {"query": scoped_query},
                    "count": len(items),
                }
            ],
        )

    browse_result = browse_menu_query(user_text)
    if not browse_result.passthrough:
        return None

    _remember_scope(session_id, user_text)
    return DeterministicMenuReply(
        reply=browse_result.display_text,
        route=f"deterministic_{browse_result.response_kind}",
        tool_calls=[
            {
                "name": "browse_current_menu_request",
                "input": {
                    "query": user_text,
                    "response_kind": browse_result.response_kind,
                    "requested_section": browse_result.requested_section,
                },
            }
        ],
    )
