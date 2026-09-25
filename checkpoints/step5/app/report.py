"""Data-only morning report (NO AI yet).

This is the shape of the finished product, built entirely in code so you can see the
structure before the agents fill it in. In step 7 this same `build_report()` signature
is replaced by a team of analysts + an editor + a critic - but the Report it returns,
and the way handlers.py / render.py consume it, stay identical.

We fetch a few things in parallel, do all arithmetic in Python (the `change` helper),
and hand back a Report. Compare this to app/report.py in later steps: same output type,
very different insides.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app.config import settings
from app.schemas import KeyNumber, Report, Section, WatchlistItem
from app.sectors import SectorsError, get_client

log = logging.getLogger(__name__)


def _rows(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("results") or data.get("data") or []
    return []


def _pct_change(series: list[float]) -> tuple[float, float]:
    """Latest change and percent change from a list of numbers, oldest to newest."""
    if len(series) < 2:
        return 0.0, 0.0
    last, prev = series[-1], series[-2]
    change = last - prev
    return change, (change / prev * 100 if prev else 0.0)


async def _index_section() -> Section | None:
    try:
        data = await get_client().get(
            "idx", "index_daily", path_params={"index_code": "ihsg"}, params={}
        )
    except SectorsError:
        log.exception("index fetch failed")
        return None
    rows = _rows(data)
    if not rows:
        return None
    closes = [float(r.get("price", 0)) for r in rows]
    change, pct = _pct_change(closes)
    last = rows[-1]
    return Section(
        title="Indices",
        bullets=[f"IHSG closed at {closes[-1]:.2f} ({change:+.2f}, {pct:+.2f}%)."],
        key_numbers=[
            KeyNumber(label="IHSG close", value=round(closes[-1], 2)),
            KeyNumber(label="Change %", value=round(pct, 2)),
        ],
        tickers=[],
        sentiment="bullish" if pct > 0 else "bearish" if pct < 0 else "neutral",
        as_of=str(last.get("date", "")),
    )


async def _movers_section() -> Section | None:
    try:
        data = await get_client().get(
            "idx", "companies", params={"order_by": "-market_cap", "limit": 5}
        )
    except SectorsError:
        log.exception("movers fetch failed")
        return None
    rows = _rows(data)
    if not rows:
        return None
    names = [
        f"{r.get('symbol')} ({r.get('company_name')})"
        for r in rows[:5]
        if r.get("symbol")
    ]
    tickers = [r.get("symbol") for r in rows[:5] if r.get("symbol")]
    return Section(
        title="Largest by market cap",
        bullets=[f"Biggest IDX names today: {', '.join(names)}."] if names else ["No data."],
        key_numbers=[],
        tickers=tickers,
        sentiment="neutral",
        as_of=datetime.now(settings.tz).date().isoformat(),
    )


async def build_report() -> Report:
    """Fetch sections in parallel, assemble a Report. Pure code, no LLM."""
    results = await asyncio.gather(_index_section(), _movers_section())
    sections = [s for s in results if s is not None]
    if not sections:
        raise RuntimeError("no sections could be built")
    as_of = max((s.as_of for s in sections if s.as_of), default="")
    watchlist = [
        WatchlistItem(symbol=t, reason="among the largest IDX names by market cap")
        for s in sections
        for t in s.tickers[:3]
    ][:5]
    return Report(
        headline="IDX morning snapshot",
        sections=sections,
        watchlist=watchlist,
        as_of=as_of,
    )
