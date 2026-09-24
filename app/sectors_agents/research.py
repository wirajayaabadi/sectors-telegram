"""Research pipeline: screen for companies, research each, synthesize one Report.

Chain (manager pattern - control stays here):
    screener -> ValidTickers -> gather(research each ticker) -> synthesizer -> Report

Example: "outlook for companies affiliated with Prajogo Pangestu"
    screener finds [BRPT, TPIA, BREN, CUAN] -> research each -> one Report.

Note: Groq can't do tools + structured output in one request, so the screener and
researcher each split into a tool-using fetcher + a structured formatter.
"""

from __future__ import annotations

import asyncio
import json
import logging

from agents import Agent, ModelSettings, Runner

from app import tools as t
from app.provider import LARGE, SMALL
from app.schemas import Report, Section, ValidTickers
from app.sectors_agents.rules import BASE_RULES
from app.sectors_agents.specialists import run_specialist, run_structured

log = logging.getLogger(__name__)

MAX_TICKERS = 5

# --- 1. Screener: fetcher (tools) -> formatter (ValidTickers) ---
_screen_fetcher = Agent(
    name="Screener",
    model=SMALL,
    instructions=f"{BASE_RULES}\n"
    "Use screen_companies to find the tickers the user asked for (by theme, owner, sector, size). "
    "Prefer the `q` natural-language argument. List ONLY the matching tickers, nothing else.",
    tools=[t.screen_companies],
    model_settings=ModelSettings(tool_choice="required", temperature=0),
)

_ticker_formatter = Agent(
    name="Ticker Formatter",
    model=SMALL,
    instructions="Extract the tickers from the text into ValidTickers. Only tickers that appear in the text.",
    output_type=ValidTickers,
    model_settings=ModelSettings(max_tokens=512, temperature=0),
)

# --- 2. Researcher: fetcher (tools) -> Section (reuses the specialists' shared formatter via run_specialist) ---
_research_fetcher = Agent(
    name="Company Researcher",
    model=SMALL,
    instructions=f"{BASE_RULES}\n"
    "Research the ONE company you are given. Use get_company_report (financials, dividend, overview); "
    "use `change` for growth. Write a concise summary under 150 words with every number, the ticker, "
    "and the data date. The section is titled with the ticker.",
    tools=[t.get_company_report, t.get_daily, t.change],
    model_settings=ModelSettings(tool_choice="required", temperature=0),
)

# --- 3. Synthesizer: no tools -> output_type is fine on its own ---
synthesizer_agent = Agent(
    name="Research Synthesizer",
    model=LARGE,
    instructions=f"{BASE_RULES}\n"
    "You receive JSON Sections, one per company. Produce a Report: a headline capturing the "
    "group's outlook, keep the per-company sections, a watchlist of up to 5 of these tickers with "
    "factual reasons, and as_of = latest date. Do not invent numbers or tickers.",
    output_type=Report,
    model_settings=ModelSettings(max_tokens=4096, temperature=0),
)


async def _research_one(ticker: str) -> Section | None:
    try:
        return await run_specialist(_research_fetcher, f"Company ticker: {ticker}")
    except Exception:
        log.exception("research failed for %s", ticker)
        return None


async def research_companies(query: str) -> Report:
    """Screen -> research each -> synthesize. Returns a Report (renders like the morning report)."""
    # 1. screen (fetcher then formatter). run_structured retries flaky JSON.
    text = (await Runner.run(_screen_fetcher, query, max_turns=6)).final_output
    screen: ValidTickers = await run_structured(_ticker_formatter, f"Text:\n{text}")
    tickers = screen.tickers[:MAX_TICKERS]
    if not tickers:
        raise RuntimeError("no companies matched the query")

    # 2. research each in parallel (manager fan-out)
    sections = [
        s
        for s in await asyncio.gather(*(_research_one(tk) for tk in tickers))
        if s is not None
    ]
    if not sections:
        raise RuntimeError("all company research failed")

    # 3. synthesize
    payload = json.dumps([s.model_dump() for s in sections], ensure_ascii=False)
    return await run_structured(synthesizer_agent, f"Sections:\n{payload}")
