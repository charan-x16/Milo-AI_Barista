"""Small helpers for agent working memory.

The model API is stateless, so AgentScope resends agent memory on each call.
These helpers keep that prompt bounded and summarize older turns.
"""

from pydantic import BaseModel, Field

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter, OpenAIMultiAgentFormatter
from agentscope.token import OpenAITokenCounter

from cafe.config import Settings


class CafeConversationSummary(BaseModel):
    """What older chat turns should preserve after compression."""

    user_request: str = Field(
        max_length=300,
        description="What the customer is trying to do or asked about.",
    )
    current_state: str = Field(
        max_length=300,
        description="Known cart/order/support state that matters for later turns.",
    )
    preferences: str = Field(
        max_length=300,
        description="Customer preferences, constraints, allergies, names, or habits.",
    )
    unresolved_items: str = Field(
        max_length=250,
        description="Open questions, confirmations, or next actions.",
    )


SUMMARY_TEMPLATE = (
    "<conversation_summary>"
    "User request: {user_request}\n"
    "Current state: {current_state}\n"
    "Preferences: {preferences}\n"
    "Unresolved items: {unresolved_items}"
    "</conversation_summary>"
)

COMPRESSION_PROMPT = (
    "<system-hint>Summarize the older cafe conversation so the assistant can "
    "continue naturally. Preserve user preferences, names, allergies, cart or "
    "order details, unresolved questions, and promises made. Be concise."
    "</system-hint>"
)


def make_token_counter(settings: Settings) -> OpenAITokenCounter:
    return OpenAITokenCounter(settings.openai_model)


def make_chat_formatter(settings: Settings) -> OpenAIChatFormatter:
    return OpenAIChatFormatter(
        token_counter=make_token_counter(settings),
        max_tokens=settings.memory_max_prompt_tokens,
    )


def make_multi_agent_formatter(settings: Settings) -> OpenAIMultiAgentFormatter:
    return OpenAIMultiAgentFormatter(
        token_counter=make_token_counter(settings),
        max_tokens=settings.memory_max_prompt_tokens,
    )


def make_compression_config(settings: Settings) -> ReActAgent.CompressionConfig:
    return ReActAgent.CompressionConfig(
        enable=True,
        agent_token_counter=make_token_counter(settings),
        trigger_threshold=settings.memory_compression_trigger_tokens,
        keep_recent=settings.memory_keep_recent_messages,
        compression_prompt=COMPRESSION_PROMPT,
        summary_template=SUMMARY_TEMPLATE,
        summary_schema=CafeConversationSummary,
    )
