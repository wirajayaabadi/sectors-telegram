"""/movers and /active: market rankings, in plain code (no LLM).

    args -> parse -> cache/API (only what was asked) -> normalise -> render (render.py)

API facts, checked against docs.sectors.app AND live responses (Oct 2026):

GET /v2/companies/top-changes/  (Top Company Movers)
- params: classifications (top_gainers, top_losers), periods (1d, 7d, 14d, 30d, 365d),
  n_stock (1-10, default 5), min_mcap_billion (default 5000 = Rp 5 T), sub_sector.
- 1 credit PER classification x period. We request one classification per call, so a
  failure in one half doesn't lose the other and each half is cached on its own.
- {"top_gainers": {"7d": [rows]}, ...}; row: name, symbol ("X.JK"), price_change
  (a FRACTION, documented), last_close_price (IDR), latest_close_date.
- Live: gainers come sorted high->low, losers low->high. "7d" is 7 CALENDAR days: FORU's
  -35.71% to 144 on 2026-10-07 matches the 224 close of 2026-09-30, five sessions back.
  "1d" is against the previous session's close.

GET /v2/most-traded/  (Most Traded Stocks)
- params: start, end (YYYY-MM-DD; default end = today, start = end - 30 days; future end
  is a 400), n_stock (1-10, default 5), adjusted (false = rank by raw volume), sub_sector.
- 2 credits per call, whatever the window or n_stock. So we always ask for 10 and slice:
  switching Top 5 <-> Top 10 never costs another call.
- {"YYYY-MM-DD": [{symbol, company_name, volume, price}], ...}. Live: volume equals the
  daily endpoint's `volume`, which is documented as shares; `price` equals that date's
  close. A date with no trading returns 200 {"message": "No traded stocks found ..."}.
- "Latest" = the newest date in the default 30-day window: one call, no backward search.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.cache import EmptyData, Fetched, TTLCache, describe_api_error
from app.config import settings
from app.sectors import get_client
from app.subsector import num

PERIODS = ("1d", "7d", "14d", "30d", "365d")
CLASSES = {"gainers": "top_gainers", "losers": "top_losers"}
WHICH = ("both", "gainers", "losers")
MOVERS_N = 5

ACTIVE_SIZES = (5, 10)
ACTIVE_MAX = 10  # the API's n_stock ceiling; the app can't promise more than it can fetch
ACTIVE_FETCH_N = 10  # same 2 credits as asking for 5

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATEISH_RE = re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}$")
_WORDS = {"gainers": "gainers", "gainer": "gainers", "losers": "losers", "loser": "losers"}


def describe_error(exc: Exception) -> str:
    return describe_api_error(exc, not_found="Data peringkat tidak ditemukan (404).")


# ----------------------------------------------------------------------------- parsing


@dataclass(frozen=True)
class MoversQuery:
    which: str = "both"  # both | gainers | losers
    period: str = "1d"

    @property
    def classes(self) -> tuple[str, ...]:
        return ("gainers", "losers") if self.which == "both" else (self.which,)


def parse_movers(args: list[str]) -> MoversQuery | None:
    """'' | '7d' | 'gainers 30d' | 'LOSERS 1D' | '30d gainers'. None = show help."""
    which = period = None
    tokens = [t.lower() for t in args]
    if len(tokens) > 2:
        return None
    for t in tokens:
        if t in PERIODS and period is None:
            period = t
        elif t in _WORDS and which is None:
            which = _WORDS[t]
        else:  # unknown word, or the same kind twice ("7d 30d")
            return None
    return MoversQuery(which or "both", period or "1d")


@dataclass(frozen=True)
class ActiveQuery:
    day: date | None = None  # None = latest available
    n: int = 5


@dataclass(frozen=True)
class ActiveError:
    reason: str  # usage | bad_date | future | count


def parse_active(args: list[str], today: date) -> ActiveQuery | ActiveError:
    """'' | 'N' | 'YYYY-MM-DD' | 'YYYY-MM-DD N'. `today` is the Asia/Jakarta date."""
    if len(args) > 2:
        return ActiveError("usage")
    day: date | None = None
    n = 5
    rest = list(args)
    if rest and _DATE_RE.match(rest[0]):
        try:
            day = date.fromisoformat(rest.pop(0))
        except ValueError:
            return ActiveError("bad_date")
        if day > today:
            return ActiveError("future")
    elif rest and _DATEISH_RE.match(rest[0]):
        return ActiveError("bad_date")  # looks like a date (07-10-2026, 2026/10/07), isn't ISO
    if rest:
        token = rest.pop(0)
        if not token.isdigit():
            return ActiveError("usage")
        n = int(token)
        if not 1 <= n <= ACTIVE_MAX:
            return ActiveError("count")
    if rest:
        return ActiveError("usage")
    return ActiveQuery(day, n)


# ----------------------------------------------------------------------------- data shapes


@dataclass
class Mover:
    rank: int  # position after validation, 1-based
    symbol: str
    name: str
    change: float  # fraction; only valid numbers make it into a ranking
    close: float | None
    close_date: str | None


@dataclass
class MoverList:
    which: str  # gainers | losers
    period: str
    rows: list[Mover] = field(default_factory=list)
    dropped: int = 0  # rows skipped for a missing/invalid change
    resorted: bool = False  # API order broke the contract and we had to sort

    @property
    def ref_dates(self) -> list[str]:
        return sorted({r.close_date for r in self.rows if r.close_date})


@dataclass
class ActiveRow:
    rank: int
    symbol: str
    name: str
    volume: float  # shares
    close: float | None  # that day's close, IDR


@dataclass
class ActiveDay:
    day: str  # YYYY-MM-DD, the trading date the ranking belongs to
    rows: list[ActiveRow] = field(default_factory=list)  # up to ACTIVE_FETCH_N
    dropped: int = 0
    resorted: bool = False


def _symbol(v: Any) -> str:
    return v.removesuffix(".JK") if isinstance(v, str) else ""


def _str(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _date(v: Any) -> str | None:
    return v if isinstance(v, str) and _DATE_RE.match(v) else None


def _ordered(values: list[float], descending: bool) -> bool:
    pairs = zip(values, values[1:])
    return all(a >= b for a, b in pairs) if descending else all(a <= b for a, b in pairs)


def parse_movers_rows(raw: Any, which: str, period: str) -> MoverList:
    """Normalise one classification x period. Keeps the API's ranking when it holds."""
    rows = (raw.get(CLASSES[which]) or {}).get(period) if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise EmptyData(f"no {which} {period} block")
    out = MoverList(which, period)
    kept: list[Mover] = []
    for r in rows:
        r = r if isinstance(r, dict) else {}
        change, symbol = num(r.get("price_change")), _symbol(r.get("symbol"))
        if change is None or not symbol:
            out.dropped += 1
            continue
        kept.append(Mover(0, symbol, _str(r.get("name")), change, num(r.get("last_close_price")), _date(r.get("latest_close_date"))))
    descending = which == "gainers"
    if not _ordered([m.change for m in kept], descending):
        # Stable rule for ties: bigger move first, then ticker A-Z.
        kept.sort(key=lambda m: (-m.change if descending else m.change, m.symbol))
        out.resorted = True
    for i, m in enumerate(kept, 1):
        m.rank = i
    out.rows = kept
    return out


def parse_most_traded(raw: Any) -> dict[str, ActiveDay]:
    """{date: ActiveDay}. Days are kept separate: top-N lists are never added together."""
    days: dict[str, ActiveDay] = {}
    for key, rows in (raw.items() if isinstance(raw, dict) else []):
        if not _date(key) or not isinstance(rows, list):
            continue  # e.g. {"message": "No traded stocks found ..."}
        day = ActiveDay(key)
        kept: list[ActiveRow] = []
        for r in rows:
            r = r if isinstance(r, dict) else {}
            volume, symbol = num(r.get("volume")), _symbol(r.get("symbol"))
            if volume is None or volume < 0 or not symbol:
                day.dropped += 1
                continue
            kept.append(ActiveRow(0, symbol, _str(r.get("company_name")), volume, num(r.get("price"))))
        if not _ordered([x.volume for x in kept], descending=True):
            kept.sort(key=lambda x: (-x.volume, x.symbol))
            day.resorted = True
        for i, x in enumerate(kept, 1):
            x.rank = i
        day.rows = kept
        if kept or day.dropped:
            days[key] = day
    return days


def pick_day(days: dict[str, ActiveDay], wanted: date | None) -> ActiveDay | None:
    """The requested date only, never a neighbour; or the newest date that has rows."""
    if wanted is not None:
        day = days.get(wanted.isoformat())
        return day if day and day.rows else None
    with_rows = [d for d in days.values() if d.rows]
    return max(with_rows, key=lambda d: d.day) if with_rows else None


# ----------------------------------------------------------------------------- fetching

_movers_cache = TTLCache(settings.rankings_ttl, settings.rankings_stale_max)
_latest_cache = TTLCache(settings.rankings_ttl, settings.rankings_stale_max)
_dated_cache = TTLCache(settings.rankings_history_ttl, settings.rankings_stale_max)


def reset_caches() -> None:
    for c in (_movers_cache, _latest_cache, _dated_cache):
        c.clear()


def movers_params(which: str, period: str) -> dict[str, Any]:
    return {
        "classifications": CLASSES[which],
        "periods": period,
        "n_stock": MOVERS_N,
        "min_mcap_billion": settings.movers_min_mcap_billion,
    }


async def get_movers_list(which: str, period: str) -> Fetched[MoverList]:
    params = movers_params(which, period)
    key = ("top_changes", *sorted(params.items()))

    async def fetch():
        return parse_movers_rows(await get_client().get("top_changes", params=params), which, period)

    return await _movers_cache.get(key, fetch)


@dataclass
class MoversResult:
    query: MoversQuery
    parts: dict[str, Fetched[MoverList]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)  # which -> friendly message
    failures: dict[str, Exception] = field(default_factory=dict)  # which -> exception, for logs


async def load_movers(q: MoversQuery) -> MoversResult:
    """Only the requested classifications, in parallel; one failing keeps the other."""
    got = await asyncio.gather(*(get_movers_list(w, q.period) for w in q.classes), return_exceptions=True)
    result = MoversResult(q)
    for which, item in zip(q.classes, got):
        if isinstance(item, BaseException):
            if not isinstance(item, Exception):
                raise item
            result.errors[which] = describe_error(item)
            result.failures[which] = item
        else:
            result.parts[which] = item
    return result


def active_params(day: date | None) -> dict[str, Any]:
    params: dict[str, Any] = {"n_stock": ACTIVE_FETCH_N, "adjusted": "false"}
    if day is not None:
        params["start"] = params["end"] = day.isoformat()
    return params  # no start/end = the API's default window ending on ITS today


async def get_active_days(day: date | None, today: date) -> Fetched[dict[str, ActiveDay]]:
    params = active_params(day)
    key = ("most_traded", *sorted(params.items()))
    # A finished past day is stable; the latest window and today's date are not.
    cache = _dated_cache if day is not None and day < today else _latest_cache

    async def fetch():
        return parse_most_traded(await get_client().get("most_traded", params=params))

    return await cache.get(key, fetch)


# ----------------------------------------------------------------------------- buttons
# Payloads carry the whole query, so any button can be replayed from cache or the API:
# "mov:<b|g|l>:<period>" (<= 10 bytes), "act:<L|YYYY-MM-DD>:<n>" (<= 17 bytes).

_W2C = {"both": "b", "gainers": "g", "losers": "l"}
_C2W = {v: k for k, v in _W2C.items()}
_MOV_RE = re.compile(r"^mov:([bgl]):(1d|7d|14d|30d|365d)$")
_ACT_RE = re.compile(r"^act:(L|\d{4}-\d{2}-\d{2}):(\d{1,2})$")


def cb_movers(q: MoversQuery) -> str:
    return f"mov:{_W2C[q.which]}:{q.period}"


def cb_active(day: date | None, n: int) -> str:
    return f"act:{day.isoformat() if day else 'L'}:{n}"


def parse_movers_callback(data: str | None) -> MoversQuery | None:
    m = _MOV_RE.match(data or "")
    return MoversQuery(_C2W[m[1]], m[2]) if m else None


def parse_active_callback(data: str | None, today: date) -> ActiveQuery | None:
    m = _ACT_RE.match(data or "")
    if m is None:
        return None
    n = int(m[2])
    if n not in ACTIVE_SIZES:
        return None
    if m[1] == "L":
        return ActiveQuery(None, n)
    try:
        day = date.fromisoformat(m[1])
    except ValueError:
        return None
    return ActiveQuery(day, n) if day <= today else None
