"""Run one agent (or one sub-pipeline) in isolation, without Telegram.

Usage (from the repo root):
    uv run python scripts/try_agent.py <name> [prompt...]
    uv run python scripts/try_agent.py                 # lists the available names

Examples:
    uv run python scripts/try_agent.py market "how is IHSG this week?"
    uv run python scripts/try_agent.py index           # the Index specialist (fetcher+formatter)
    uv run python scripts/try_agent.py report          # the whole morning-report pipeline
    uv run python scripts/try_agent.py research "companies affiliated with Prajogo Pangestu"
    uv run python scripts/try_agent.py ask "what should I buy?"   # goes through triage + guardrail
    uv run python scripts/try_agent.py sector banks    # /sector, plain code (5 credits, no LLM)
    uv run python scripts/try_agent.py stock BBCA      # /stock card, plain code (5 credits, no LLM)
    uv run python scripts/try_agent.py movers 7d       # /movers, plain code (1 credit per half)
    uv run python scripts/try_agent.py active 10       # /active, plain code (2 credits)
"""

import asyncio
import logging
import sys
from pathlib import Path

# This script lives in scripts/, so the repo root isn't on the import path by default.
# Add it so `import app...` works when you run the file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# INFO logging shows the tool calls and HTTP status codes as the agent runs. A bare script
# doesn't inherit main.py's logging setup, so we turn it on here.
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)

from agents import Runner  # noqa: E402

from app.provider import configure_llm  # noqa: E402


# Each entry is an async function taking the prompt and returning the result to print.
# We define them lazily (imports inside) so a broken agent doesn't stop the whole script loading.
async def _run_plain(agent, prompt):
    return (await Runner.run(agent, prompt)).final_output


def _plain(import_path):
    """A plain agent: run it directly, print .final_output."""

    async def go(prompt):
        module, name = import_path.rsplit(".", 1)
        agent = getattr(__import__(module, fromlist=[name]), name)
        return await _run_plain(agent, prompt)

    return go


def _specialist(fetcher_name):
    """A report specialist: run the fetcher+formatter pair via run_specialist -> a Section."""

    async def go(prompt):
        from app.sectors_agents import specialists

        fetcher = getattr(specialists, fetcher_name)
        return await specialists.run_specialist(
            fetcher, prompt or "Prepare your section for today."
        )

    return go


async def _report(prompt):
    from app.report import build_report

    return await build_report()


async def _research(prompt):
    from app.sectors_agents.research import research_companies

    return await research_companies(prompt or "the biggest IDX companies by market cap")


async def _compare(prompt):
    from app.handlers import _parse_symbols
    from app.sectors_agents.compare import compare_companies

    symbols = _parse_symbols([prompt]) if prompt else ["BBCA", "BBRI"]
    return await compare_companies(symbols)


async def _ask(prompt):
    from app.sectors_agents import answer

    return await answer(chat_id=0, text=prompt or "how is IHSG?")


async def _sector(prompt):
    """/sector without Telegram: match -> fetch -> render every tab. No LLM involved."""
    from app import subsector
    from app.render import render_subsector

    subs = (await subsector.get_subsectors()).value
    found = subsector.match(prompt or "banks", subs)
    if found.slug is None:
        return f"No exact match. Candidates: {', '.join(found.candidates) or 'none'}"
    fetched = await subsector.get_report(found.slug)
    return "\n\n".join(
        f"===== {name} =====\n" + "\n".join(render_subsector(fetched, view))
        for view, name in subsector.VIEWS.items()
    )


async def _stock(prompt):
    """/stock without Telegram: the card plus every tab that needs no extra request."""
    from app import stock
    from app.render import render_stock

    symbol = stock.normalize_ticker(prompt or "BBCA")
    if symbol is None:
        return f"Not a ticker: {prompt!r}"
    card = await stock.load_card(symbol)
    return "\n\n".join(
        f"===== {stock.VIEWS[view]} =====\n" + "\n".join(render_stock(card, view))
        for view in card.tabs()
        if view != "va"  # valuation costs one more credit; open it in Telegram
    )


async def _movers(prompt):
    from app import rankings
    from app.render import render_movers

    q = rankings.parse_movers(prompt.split())
    if q is None:
        return "Invalid /movers arguments"
    return "\n".join(render_movers(await rankings.load_movers(q)))


async def _active(prompt):
    from datetime import datetime

    from app import rankings
    from app.config import settings
    from app.render import render_active, render_active_help

    today = datetime.now(settings.tz).date()
    q = rankings.parse_active(prompt.split(), today)
    if isinstance(q, rankings.ActiveError):
        return render_active_help(q)
    fetched = await rankings.get_active_days(q.day, today)
    return "\n".join(render_active(fetched, rankings.pick_day(fetched.value, q.day), q))


# name -> runner. Grouped by the three test shapes we care about.
AGENTS = {
    # plain agents (free-text output)
    "market": _plain("app.sectors_agents.chat.market_agent"),
    "idx_company": _plain("app.sectors_agents.chat.idx_company_agent"),
    "triage": _plain("app.sectors_agents.triage.triage_agent"),
    # specialists (fetcher + formatter -> a Section)
    "index": _specialist("index_fetcher"),
    "movers": _specialist("movers_fetcher"),
    "flow": _specialist("flow_fetcher"),
    "news": _specialist("news_fetcher"),
    # whole sub-pipelines
    "report": _report,  # full morning report (manager + critic)
    "research": _research,  # screen -> research each -> synthesize
    "compare": _compare,  # research each in parallel -> comparator
    "ask": _ask,  # triage + guardrail + handoff, like a real /ask
    "sector": _sector,  # /sector: plain code, no agent
    "stock": _stock,  # /stock: plain code, no agent
    "movers": _movers,  # /movers: plain code, no agent
    "active": _active,  # /active: plain code, no agent
}


def _show(result):
    """Print the result, handling Pydantic objects and the Windows-console emoji gotcha."""
    text = (
        result.model_dump_json(indent=2)
        if hasattr(result, "model_dump_json")
        else str(result)
    )
    try:
        print(text)
    except (
        UnicodeEncodeError
    ):  # Windows console can't print emoji; write to a file instead
        with open("_try_agent_out.txt", "w", encoding="utf-8") as f:
            f.write(text)
        print(
            "(output had characters the console can't show; wrote it to _try_agent_out.txt)"
        )


async def main():
    if len(sys.argv) < 2 or sys.argv[1] not in AGENTS:
        print("Available names:", ", ".join(AGENTS))
        print('Usage: uv run python scripts/try_agent.py <name> "your prompt"')
        return
    name = sys.argv[1]
    prompt = " ".join(sys.argv[2:])
    configure_llm()  # register the Groq client BEFORE running any agent
    result = await AGENTS[name](prompt)
    print("\n----- RESULT -----")
    _show(result)


if __name__ == "__main__":
    asyncio.run(main())
