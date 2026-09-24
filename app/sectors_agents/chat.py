"""The Q&A specialist agents that triage routes between via handoff.

- market_agent:      index / market-wide questions (IDX or SGX)
- idx_company_agent: one IDX company (report, financials, growth, peers)
- sgx_company_agent: one SGX company (report, financials, growth)
"""

from __future__ import annotations

from agents import Agent, ModelSettings, function_tool

from app import tools as t
from app.provider import SMALL
from app.sectors_agents.rules import BASE_RULES

_SETTINGS = ModelSettings(temperature=0)  # deterministic: faithfully repeat tool data

_TG_FORMAT = (
    "Format for Telegram: plain text, short paragraphs, '- ' for bullets. Under 200 words. "
    "For growth or comparisons, fetch the numbers with a tool then use `change` to compute "
    "percentages - never do the arithmetic yourself."
)

market_agent = Agent(
    name="Market Agent",
    handoff_description="Market-wide questions: indices, market cap, movers, foreign flow, news.",
    model=SMALL,
    instructions=f"{BASE_RULES}\nAnswer market-wide questions about IDX and SGX indices and flows. {_TG_FORMAT}",
    tools=[
        t.get_index,
        t.get_idx_total,
        t.get_daily,
        t.get_foreign_flow,
        t.screen_companies,
        t.get_news,
        t.change,
    ],
    model_settings=_SETTINGS,
)

idx_company_agent = Agent(
    name="IDX Company Agent",
    handoff_description="Questions about ONE specific IDX-listed company (financials, dividend, growth, peers).",
    model=SMALL,
    instructions=f"{BASE_RULES}\nAnswer about a single IDX company. Use get_company_report (market='idx'); "
    f"for growth use the financials section then `change`. {_TG_FORMAT}",
    tools=[t.get_company_report, t.get_daily, t.get_filings, t.change],
    model_settings=_SETTINGS,
)

sgx_company_agent = Agent(
    name="SGX Company Agent",
    handoff_description="Questions about ONE specific SGX-listed company (financials, dividend, growth).",
    model=SMALL,
    instructions=f"{BASE_RULES}\nAnswer about a single SGX company. Use get_company_report (market='sgx'); "
    f"for growth use the financials section then `change`. {_TG_FORMAT}",
    tools=[t.get_company_report, t.get_daily, t.get_filings, t.change],
    model_settings=_SETTINGS,
)


@function_tool
async def research_group(query: str) -> str:
    """Screen for companies matching a theme/owner/sector, research each, and return a group outlook.

    Use for multi-company questions like "Prajogo Pangestu's companies" or "the big banks".

    Args:
        query: the group to research, e.g. "companies affiliated with Prajogo Pangestu".
    """
    from app.render import render_report
    from app.sectors_agents.research import research_companies

    report = await research_companies(query)
    return "\n\n".join(
        render_report(report)
    )  # already-formatted text the agent can relay


research_agent = Agent(
    name="Research Agent",
    handoff_description="A GROUP of companies by theme/owner/sector (e.g. 'Prajogo's companies', 'big banks').",
    model=SMALL,
    instructions=f"{BASE_RULES}\nThe user asked about a GROUP of companies. Call research_group ONCE with "
    f"their description, then relay its result. Do not rename or invent tickers. {_TG_FORMAT}",
    tools=[research_group],
    model_settings=_SETTINGS,
)
