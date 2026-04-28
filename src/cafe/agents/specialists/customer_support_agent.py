"""Customer Support specialist - handles cafe support RAG questions."""

from pathlib import Path

from agentscope.agent import ReActAgent
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit, view_text_file

from cafe.agents.llm import make_chat_model
from cafe.agents.memory import make_multi_agent_formatter
from cafe.agents.prompts import CUSTOMER_SUPPORT_PROMPT
from cafe.config import get_settings
from cafe.tools.support_tools import search_support_knowledge


_SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "support_playbook"


def _make_toolkit() -> Toolkit:
    tk = Toolkit()
    tk.register_tool_function(search_support_knowledge)
    tk.register_tool_function(view_text_file)
    tk.register_agent_skill(str(_SKILL_DIR))
    return tk


def make_customer_support_agent() -> ReActAgent:
    s = get_settings()
    return ReActAgent(
        name="CustomerSupportAgent",
        sys_prompt=CUSTOMER_SUPPORT_PROMPT,
        model=make_chat_model(s),
        formatter=make_multi_agent_formatter(s),
        toolkit=_make_toolkit(),
        memory=InMemoryMemory(),
        max_iters=6,
    )
