"""Triage: route a /ask question to the right specialist via HANDOFF.

Handoff vs manager: triage hands OFF control to one specialist, which then answers
the user directly. Contrast with report.py / research.py managers, which call
sub-agents, get results BACK, and assemble the answer themselves.

An input guardrail (topic_guardrail) runs first and blocks off-topic / advice questions.
"""

from __future__ import annotations

import logging

from agents import (
    Agent,
    InputGuardrailTripwireTriggered,
    ModelSettings,
    Runner,
    SQLiteSession,
)

from app.config import settings
from app.provider import SMALL
from app.sectors_agents.chat import (
    idx_company_agent,
    market_agent,
    research_agent,
    sgx_company_agent,
)
from app.sectors_agents.guardrail import topic_guardrail
from app.sectors_agents.rules import BASE_RULES

log = logging.getLogger(__name__)

triage_agent = Agent(
    name="Triage",
    model=SMALL,
    instructions=(
        f"{BASE_RULES}\n"
        "Route the user's question by handing off to exactly one agent:\n"
        "- Research Agent: a GROUP of companies by theme/owner/sector (e.g. 'Prajogo Pangestu's "
        "companies', 'the big banks', 'nickel miners'). Use this whenever the answer needs "
        "several companies screened and researched together.\n"
        "- Market Agent: indices (IHSG, STI), market cap, movers, foreign flow, market-wide news.\n"
        "- IDX Company Agent: ONE specific Indonesian company (4-letter ticker like BBCA).\n"
        "- SGX Company Agent: ONE specific Singapore company (e.g. D05, U11).\n"
        "Hand off immediately; do not answer yourself. If unsure, use the Market Agent."
    ),
    handoffs=[research_agent, market_agent, idx_company_agent, sgx_company_agent],
    input_guardrails=[topic_guardrail],
    model_settings=ModelSettings(temperature=0),
)


async def answer(chat_id: int, text: str) -> str:
    session = SQLiteSession(f"chat-{chat_id}", settings.db_path)
    try:
        result = await Runner.run(triage_agent, text, session=session, max_turns=8)
        return str(result.final_output)
    except InputGuardrailTripwireTriggered:
        return (
            "I can only help with market and company data (IDX/SGX). "
            "I can't tell you what to personally buy, sell, or hold."
        )
    except Exception:
        log.exception("chat failed for %s", chat_id)
        return "Something went wrong while processing your question. Please try again shortly."


async def reset_memory(chat_id: int) -> None:
    await SQLiteSession(f"chat-{chat_id}", settings.db_path).clear_session()
