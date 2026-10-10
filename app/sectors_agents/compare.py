"""Head-to-head comparison pipeline: research each company, then compare them.

PARALLELIZATION inside a MANAGER (control stays here):

    analyst(A) ┐
    analyst(B) ├─ gather ─> [Section]  ->  comparator  ->  Comparison
    analyst(C) ┘

The analysts never talk to each other and never see the other companies - each one only
researches the ticker it was handed. The comparator is the only agent that sees all of
them, and it has NO tools, so every figure it prints has to come from a Section.

Pacing: with a free Groq tier (settings.is_paid False) the per-minute token limit is the
binding constraint, so we run the analysts one at a time with a delay instead of in
parallel. Same agents, same result, just slower - mirrors report.py's two runners.
"""

from __future__ import annotations

import asyncio
import json
import logging

from agents import Agent, ModelSettings

from app import tools as t
from app.config import settings
from app.provider import LARGE, SMALL
from app.schemas import Comparison, Section
from app.sectors import SectorsError, get_client
from app.sectors_agents.rules import BASE_RULES
from app.sectors_agents.specialists import run_specialist, run_structured

log = logging.getLogger(__name__)

MIN_COMPANIES = 2
MAX_COMPANIES = 4
SEQUENTIAL_DELAY = 3.0  # seconds between analysts when running un-paid (stay under ITPM)


async def ensure_listed(symbol: str) -> None:
    """Reject a ticker IDX does not know. Plain code, never the LLM.

    Checked up front so a typo fails with a clear message instead of surfacing as an
    analyst that quietly returns nothing halfway through the comparison.
    """
    try:
        await get_client().get("daily", path_params={"symbol": symbol})
    except SectorsError as e:
        raise ValueError(f"{symbol} is not listed on IDX") from e


# One analyst, reused per company. Tool-using and free-text, so run_specialist can pair it
# with the shared Section formatter (Groq refuses tools + structured output in one request).
analyst = Agent(
    name="Comparison Analyst",
    model=SMALL,
    instructions=f"{BASE_RULES}\n"
    "You research ONE company so it can be compared against its peers. Call "
    "get_company_snapshot - it already holds the headline figures, so one call is enough. "
    "Call get_daily only if the snapshot gave you no dated close.\n"
    "Write a plain-text summary under 150 words covering, where available: market cap, "
    "earnings per share, revenue and earnings growth, dividend yield, a valuation multiple, "
    "and the latest close with its date. State the unit for every figure. The section is "
    "titled with the ticker.",
    tools=[t.get_company_snapshot, t.get_daily, t.change],
    model_settings=ModelSettings(tool_choice="required", temperature=0),
)

# No tools: this agent only reorganises what the analysts found. That is what keeps the
# comparison grounded - it has no way to fetch a number nobody reported.
comparator = Agent(
    name="Comparator",
    model=LARGE,
    instructions=f"{BASE_RULES}\n"
    "You receive one JSON Section per company. Produce a Comparison:\n"
    "- rows: 3-6 metrics the sources actually cover (e.g. market cap, revenue, earnings "
    "growth, dividend yield, valuation, latest close). For each row give every company's "
    "value with its unit, and a note saying who leads and by how much.\n"
    "- If a company's Section is missing a metric, write 'not reported' for it rather than "
    "dropping the company or guessing.\n"
    "- headline: one sentence on the main factual difference.\n"
    "- takeaways: 2-4 factual observations. Describe differences, never advise.\n"
    "- as_of: the latest data date among the Sections.\n"
    "Use only figures that appear in the Sections. Do not compute new percentages.",
    output_type=Comparison,
    model_settings=ModelSettings(max_tokens=4096, temperature=0),
)


async def _research_one(symbol: str) -> Section | None:
    """Research one company. Returns None on failure so the others still get compared."""
    try:
        await ensure_listed(symbol)
        return await run_specialist(analyst, f"Company ticker: {symbol}")
    except Exception:
        log.exception("comparison analyst failed for %s", symbol)
        return None


async def _gather_parallel(symbols: list[str]) -> list[Section]:
    results = await asyncio.gather(*(_research_one(s) for s in symbols))
    return [s for s in results if s]


async def _gather_sequential(symbols: list[str]) -> list[Section]:
    sections: list[Section] = []
    for symbol in symbols:
        section = await _research_one(symbol)
        if section:
            sections.append(section)
        await asyncio.sleep(SEQUENTIAL_DELAY)
    return sections


async def compare_companies(symbols: list[str]) -> Comparison:
    """Research every symbol, then have the comparator put them head to head."""
    gather = _gather_parallel if settings.is_paid else _gather_sequential
    sections = await gather(symbols)
    if len(sections) < MIN_COMPANIES:
        raise RuntimeError(
            f"only {len(sections)} of {len(symbols)} companies could be researched"
        )
    payload = json.dumps([s.model_dump() for s in sections], ensure_ascii=False)
    return await run_structured(
        comparator,
        f"Compare these companies: {', '.join(symbols)}\n\nSections:\n{payload}",
    )
