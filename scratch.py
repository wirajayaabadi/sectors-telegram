"""Quick scratch pad: run ONE agent in isolation. Edit freely, this file is throwaway.

    uv run python scratch.py

Swap the agent/prompt below to poke whatever you're debugging. Turn logging on to see the
tool calls and HTTP status codes as it runs.
"""

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

from agents import Runner

from app.provider import configure_llm
from app.sectors_agents.chat import market_agent


async def main():
    configure_llm()  # register the Groq client first, or the agent can't run
    r = await Runner.run(market_agent, "how is IHSG this week?")
    print("\n----- RESULT -----")
    print(r.final_output)


if __name__ == "__main__":
    asyncio.run(main())
