"""The editor: merges the analysts' Sections into one Report (prompt chaining)."""

from __future__ import annotations

from agents import Agent, ModelSettings

from app.provider import LARGE
from app.schemas import Report
from app.sectors_agents.rules import BASE_RULES

editor = Agent(
    name="Editor",
    model=LARGE,
    instructions=f"""{BASE_RULES}

You are the editor. You receive JSON sections from the analysts.
Produce the Report:
- headline: one sentence capturing the overall mood, using only facts from the sections.
- sections: keep the analysts' sections; tighten wording, max 4 bullets each,
  keep key_numbers and tickers consistent with the bullets.
- watchlist: up to 5 tickers already mentioned in the sections, each with a factual
  reason (e.g. "top gainer with heavy foreign buying"). Never a recommendation.
- as_of: the latest data date among the sections.
Do not introduce any number or ticker that is not in the input.

If you are given EVALUATOR FEEDBACK, fix exactly those issues and keep everything else.""",
    output_type=Report,
    model_settings=ModelSettings(max_tokens=4096, temperature=0),
)
