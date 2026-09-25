"""The morning-report MANAGER.

Manager pattern (control stays here): gather Sections from the analysts, hand them
to the editor, then run a critic revise loop:

    analysts -> editor -> critic -> pass? done : editor(feedback) -> ...

Two runners, switched by settings.is_paid:
  - partial (free Groq tier): 2 specialists, run ONE AT A TIME with pacing, so we
    never burst past the input-tokens-per-minute limit.
  - full (paid Dev tier): all 4 specialists in parallel.

Contrast with triage.py's HANDOFF, where control transfers to a specialist. Here
the manager never gives up control - it collects and assembles.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from agents import Runner

from app.config import settings
from app.schemas import EvaluationFeedback, Report, Section
from app.sectors_agents import critic, editor
from app.sectors_agents.specialists import (
    SPECIALISTS,
    index_fetcher,
    news_fetcher,
    run_specialist,
    run_structured,
)

log = logging.getLogger(__name__)

MAX_REVISIONS = 2  # editor <-> critic rounds before we send what we have
PARTIAL_SPECIALISTS = [index_fetcher, news_fetcher]  # cheapest two for the free tier
PARTIAL_DELAY = 3.0  # seconds between sequential calls, to respect per-minute limits


def _prompt() -> str:
    today = datetime.now(settings.tz).date()
    return f"Prepare your section for the {today:%A, %d %B %Y} IDX morning outlook."


async def _safe_specialist(fetcher, prompt: str) -> Section | None:
    try:
        return await run_specialist(fetcher, prompt)
    except Exception:  # one failed analyst must not kill the report
        log.exception("specialist %s failed", fetcher.name)
        return None


async def _gather_full(prompt: str) -> list[Section]:
    """Paid tier: all specialists in parallel."""
    results = await asyncio.gather(*(_safe_specialist(f, prompt) for f in SPECIALISTS))
    return [s for s in results if s]


async def _gather_partial(prompt: str) -> list[Section]:
    """Free tier: fewer specialists, one at a time with pacing (stay under ITPM)."""
    sections: list[Section] = []
    for fetcher in PARTIAL_SPECIALISTS:
        s = await _safe_specialist(fetcher, prompt)
        if s:
            sections.append(s)
        await asyncio.sleep(PARTIAL_DELAY)
    return sections


async def _edit_and_review(sections: list[Section]) -> Report:
    """Shared tail: editor assembles, critic reviews in a bounded revise loop.

    editor and critic are structured-output agents, so they go through run_structured
    (retries the occasional flaky JSON) rather than a bare Runner.run.
    """
    sources = json.dumps([s.model_dump() for s in sections], ensure_ascii=False)
    report: Report = await run_structured(editor, f"Sections:\n{sources}")
    for rnd in range(1, MAX_REVISIONS + 1):
        verdict: EvaluationFeedback = await run_structured(
            critic,
            f"Iteration {rnd} of {MAX_REVISIONS}\n\nSources:\n{sources}\n\nReport:\n{report.model_dump_json()}",
        )
        if verdict.score == "pass":
            break
        log.info("critic round %d: %s - %s", rnd, verdict.score, verdict.feedbacks)
        feedback = "EVALUATOR FEEDBACK - fix these, keep everything else:\n- " + "\n- ".join(verdict.feedbacks)
        report = await run_structured(
            editor, f"Sections:\n{sources}\n\n{feedback}\n\nPrevious:\n{report.model_dump_json()}"
        )
    return report


async def build_report() -> Report:
    prompt = _prompt()
    sections = await (_gather_full(prompt) if settings.is_paid else _gather_partial(prompt))
    if not sections:
        raise RuntimeError("all analysts failed")
    return await _edit_and_review(sections)
