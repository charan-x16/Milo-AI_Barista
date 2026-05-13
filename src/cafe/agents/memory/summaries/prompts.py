"""Prompts for LLM-generated checkpoint memory summaries."""

MEMORY_SUMMARY_PROMPT = """You maintain cumulative memory for a cafe ordering assistant.

Update the prior summary with the provided visible user/assistant messages.
Preserve durable preferences, decisions, important facts, cart/order context,
and unresolved questions. Do not invent facts.

Return only JSON with this shape:
{
  "summary_text": "prompt-ready cumulative summary",
  "preferences": [],
  "decisions": [],
  "important_facts": [],
  "cart_order_context": [],
  "unresolved_questions": []
}
"""
