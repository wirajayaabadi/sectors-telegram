"""The four parallel morning-report analysts.

Groq cannot combine tool-calling and structured output (json mode) in ONE request.
So each analyst is TWO agents chained:
    fetcher (tools, free text)  ->  formatter (output_type=Section, no tools)
Neither request has both, so Groq is happy. Use run_specialist() to run the pair.
"""

from __future__ import annotations

import logging

from agents import Agent, ModelSettings, Runner

from app import tools as t
from app.provider import SMALL, LARGE
from app.schemas import Section
from app.sectors_agents.rules import BASE_RULES

log = logging.getLogger(__name__)

FORMAT_ATTEMPTS = 3  # the formatter occasionally emits garbage JSON; the same input usually
#                      succeeds on a retry, so we try a few times before giving up.


def _fetcher(name: str, focus: str, tools: list) -> Agent:
    """Tool-using agent that gathers data and writes a plain-text summary (no output_type)."""
    return Agent(
        name=name,
        model=SMALL,
        instructions=f"{BASE_RULES}\n\nYour job: {focus}\n"
        "Call your tools to get the data, then write a concise plain-text summary under 150 words "
        "that includes every number and ticker you found and the data date. Facts only, no filler.",
        tools=tools,
        model_settings=ModelSettings(
            tool_choice="required", temperature=0
        ),  # force a real tool call first, deterministic output
    )


# One shared formatter: turns any fetcher's text into a Section. No tools -> json mode is allowed.
# Keep the Section small: a compact Section can't truncate mid-JSON, and reads better in
# Telegram. max_tokens is headroom for the occasional richer section.
_formatter = Agent(
    name="Section Formatter",
    model=LARGE,
    instructions=f"{BASE_RULES}\n"
    "You are given an analyst's plain-text summary. Convert it into a Section. Keep it TIGHT: "
    "at most 4 bullets, at most 6 key_numbers (the most important figures), at most 8 tickers. "
    "Every number and ticker you include must be in the summary. Do not add facts not in it.",
    output_type=Section,
    model_settings=ModelSettings(max_tokens=4096),
)


index_fetcher = _fetcher(
    "Index Analyst",
    "Summarise how the IHSG index moved and total market cap. Call get_index for IHSG "
    "and get_idx_total; use the `change` tool on the last two closes to get the % move. "
    "The section is titled 'Indices'.",
    [t.get_index, t.get_idx_total, t.change],
)

movers_fetcher = _fetcher(
    "Movers Analyst",
    "Use screen_companies to find the biggest IDX companies by market cap and any notable "
    "movers (e.g. q='biggest gainers today' or a where-filter). Report the standout names. "
    "The section is titled 'Movers'.",
    [t.screen_companies, t.get_daily, t.change],
)

flow_fetcher = _fetcher(
    "Flow Analyst",
    "Screen for a few large-cap IDX stocks, then check foreign flow for up to 3 of them "
    "with get_foreign_flow. Describe which saw the strongest net foreign buying/selling. "
    "The section is titled 'Foreign Flow'.",
    [t.screen_companies, t.get_foreign_flow],
)

news_fetcher = _fetcher(
    "News Analyst",
    "Pick the 3-4 most market-relevant IDX news items (get_news) and note any significant "
    "filings (get_filings). Explain in one line each why they matter. Include tickers. "
    "The section is titled 'News'.",
    [t.get_news, t.get_filings],
)

SPECIALISTS = [index_fetcher, movers_fetcher, flow_fetcher, news_fetcher]


async def run_structured(agent: Agent, prompt: str):
    """Run a structured-output agent, retrying on flaky JSON.

    Groq/Qwen occasionally emit garbage instead of valid JSON (a 400 json_validate_failed).
    The same input usually works on a retry, so we try a few times before giving up. Reusable
    for any output_type agent (the Section formatter, the ticker extractor, the synthesizer).
    """
    last_error: Exception | None = None
    for attempt in range(1, FORMAT_ATTEMPTS + 1):
        try:
            return (await Runner.run(agent, prompt)).final_output
        except Exception as e:  # usually a 400 json_validate_failed: garbage JSON from the model
            last_error = e
            log.warning("%s attempt %d/%d failed: %s", agent.name, attempt, FORMAT_ATTEMPTS, e)
    raise last_error


async def run_specialist(fetcher: Agent, prompt: str) -> Section:
    """Run the fetcher (tools) then the formatter (structured output) -> a Section.

    Only the formatter is retried: the fetcher already succeeded (and hit the API), so on a
    flaky-JSON failure we just re-ask the formatter with the same summary.
    """
    summary = (await Runner.run(fetcher, prompt, max_turns=8)).final_output
    return await run_structured(_formatter, f"Analyst summary:\n{summary}")
