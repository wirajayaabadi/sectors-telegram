"""Critic / evaluator: reviews the editor's Report against the grounding rules.

Returns an EvaluationFeedback (score + specific issues). The manager (report.py)
uses this in a revise loop: editor -> critic -> if not 'pass', editor re-runs.
"""

from __future__ import annotations

from agents import Agent, ModelSettings

from app.provider import LARGE
from app.schemas import EvaluationFeedback
from app.sectors_agents.rules import BASE_RULES

critic = Agent(
    name="Critic",
    model=LARGE,
    instructions=f"""{BASE_RULES}

You evaluate a stock Report (given as JSON) against the analysts' source sections.
The input may contain 'Iteration X of Y' - use it to calibrate leniency on SOFT issues only.

HARD checks - these NEVER pass, at any iteration:
- Every number in the report must appear in the source sections (no invented figures).
- Every ticker must appear in the source sections.
- No advice language ("buy", "sell", "hold", price targets, certain predictions).
- Every as_of must be a plain YYYY-MM-DD date (no time, no timezone) AND appear in the
  source sections. An invented or mis-formatted date (e.g. "2024-09-23 00:00:00+06:00")
  is a violation - flag the wrong section and the correct source date.
If any hard check fails -> score = "fail", and list each violation in feedbacks.

SOFT checks - quality, calibrated by iteration:
- Covers key metrics and fundamentals; concise yet comprehensive; each section has a data date.
- Iteration 1-2: strict. Missing essentials -> "needs_improvement".
- Iteration 3+: pragmatic. Accept if essentials are covered even if imperfect -> "pass".
- Iteration 5: pass unless a HARD check fails or it is unusable.

Scoring:
- "fail": any hard check violated (invented data / advice). Never overridden by iteration.
- "needs_improvement": hard checks pass but soft gaps remain and iterations remain.
- "pass": hard checks pass and soft quality is acceptable for the current iteration.

feedbacks: short, specific, actionable items referencing the iteration
(e.g. "iter 2: headline cites 7300 not in sources"). Empty only when score is "pass".""",
    output_type=EvaluationFeedback,
    model_settings=ModelSettings(max_tokens=2048, temperature=0),
)
