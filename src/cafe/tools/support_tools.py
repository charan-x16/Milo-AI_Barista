from agentscope.tool import ToolResponse

from cafe.core.observability import observe_tool
from cafe.core.validator import ValidationError
from cafe.models.tool_io import ToolResult
from cafe.services import faq_service
from cafe.services.rag_service import build_rag_service, rag_sources
from cafe.tools._wrap import wrap


@observe_tool("faq_lookup")
async def faq_lookup(question: str) -> ToolResponse:
    """Returns (topic, answer) wrapped in ToolResult.

    Args:
        question: Customer support question to match against cafe FAQs.

    Returns:
        ToolResult.ok(topic=..., answer=...) or ToolResult.fail(error=...).

    Example:
        faq_lookup(question="What time do you open?")
    """
    try:
        topic, answer = faq_service.lookup_faq(question)
        return wrap(ToolResult.ok(topic=topic, answer=answer))
    except ValidationError as e:
        return wrap(ToolResult.fail(str(e)))
    except Exception as e:
        return wrap(ToolResult.fail(f"Unexpected error: {e}"))


@observe_tool("search_support_knowledge")
async def search_support_knowledge(query: str, max_results: int = 5) -> ToolResponse:
    """Retrieve policy/support knowledge from the support Qdrant collection.

    Args:
        query: Natural-language customer support question.
        max_results: Maximum number of chunks to return.

    Returns:
        ToolResult.ok(results=..., count=...) or ToolResult.fail(error=...).

    Example:
        search_support_knowledge(query="refund policy for wrong item", max_results=3)
    """
    try:
        source = rag_sources()["support"]
        hits = build_rag_service().retrieve(source.collection_name, query, limit=max_results)
        return wrap(
            ToolResult.ok(
                results=[
                    {
                        "text": hit.text,
                        "score": hit.score,
                        "source": hit.source,
                        "chunk_index": hit.chunk_index,
                    }
                    for hit in hits
                ],
                count=len(hits),
            )
        )
    except Exception as e:
        return wrap(ToolResult.fail(f"RAG retrieval error: {e}"))
