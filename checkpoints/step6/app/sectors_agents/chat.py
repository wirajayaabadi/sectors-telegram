"""Step 6: ONE Q&A agent.

An agent = instructions + tools + model settings. Nothing more mysterious than that.
`market_agent` can answer market-wide questions by calling the Sectors tools. We run it
directly here. In step 7 we add a team of report analysts; in step 8 a triage agent routes
between several specialists via handoff. For now, one agent answers everything.
"""

from __future__ import annotations

import logging

from agents import Agent, ModelSettings, Runner, SQLiteSession

from app import tools as t
from app.config import settings
from app.provider import SMALL
from app.sectors_agents.rules import BASE_RULES

log = logging.getLogger(__name__)

_TG_FORMAT = (
    "Format for Telegram: plain text, short paragraphs, '- ' for bullets. Under 200 words. "
    "For growth or comparisons, fetch the numbers with a tool then use `change` to compute "
    "percentages - never do the arithmetic yourself."
)

market_agent = Agent(
    name="Market Agent",
    model=SMALL,
    instructions=f"{BASE_RULES}\nAnswer questions about IDX and SGX markets, indices, "
    f"companies, movers, foreign flow, and news. {_TG_FORMAT}",
    tools=[
        t.get_index,
        t.get_idx_total,
        t.get_daily,
        t.get_foreign_flow,
        t.screen_companies,
        t.get_company_report,
        t.get_news,
        t.change,
    ],
    model_settings=ModelSettings(temperature=0),  # deterministic: faithfully repeat tool data
)


async def answer(chat_id: int, text: str) -> str:
    # A per-chat SQLite session gives the agent conversation memory (what /reset clears).
    session = SQLiteSession(f"chat-{chat_id}", settings.db_path)
    try:
        result = await Runner.run(market_agent, text, session=session, max_turns=8)
        return str(result.final_output)
    except Exception:
        log.exception("chat failed for %s", chat_id)
        return "Something went wrong while processing your question. Please try again shortly."


async def reset_memory(chat_id: int) -> None:
    await SQLiteSession(f"chat-{chat_id}", settings.db_path).clear_session()
