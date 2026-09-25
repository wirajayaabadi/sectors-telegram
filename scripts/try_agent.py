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


async def _ask(prompt):
    from app.sectors_agents import answer

    return await answer(chat_id=0, text=prompt or "how is IHSG?")


# name -> runner. Grouped by the three test shapes we care about.
AGENTS = {
    # plain agents (free-text output)
    "market": _plain("app.sectors_agents.chat.market_agent"),
    "idx_company": _plain("app.sectors_agents.chat.idx_company_agent"),
    "sgx_company": _plain("app.sectors_agents.chat.sgx_company_agent"),
    "triage": _plain("app.sectors_agents.triage.triage_agent"),
    # specialists (fetcher + formatter -> a Section)
    "index": _specialist("index_fetcher"),
    "movers": _specialist("movers_fetcher"),
    "flow": _specialist("flow_fetcher"),
    "news": _specialist("news_fetcher"),
    # whole sub-pipelines
    "report": _report,  # full morning report (manager + critic)
    "research": _research,  # screen -> research each -> synthesize
    "ask": _ask,  # triage + guardrail + handoff, like a real /ask
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
