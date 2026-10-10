"""/stock: one IDX company's summary card, in plain code (no LLM).

    input -> ticker -> cache/API (report + daily, in parallel) -> normalise -> maths -> render

Every figure comes from the Sectors API; price changes and annual YoY growth are computed
here; render.py only formats. No agent is involved, so /stock works without GROQ_API_KEY.

API facts, checked against docs.sectors.app AND live responses (Oct 2026):
- GET /v2/company/report/{symbol}/?sections=... -> 1 credit PER section. Symbol is 4
  letters, ".JK" optional. Unknown symbol -> 404 "Given stock symbol does not exist."
  (1 credit). "snapshot" in app/tools.py is a local trim of this same endpoint.
- P/E TTM and P/B MRQ are NOT in the valuation section: they only exist in `peers`, in
  the entry whose group is ["self"]. `peers` comes back as a LIST of {"peers_data": ...},
  not the object the docs show. Its market cap lags the overview by one trading day.
- dividend.yield_ttm == dividend_ttm / overview.last_close_price (checked: 381/6050).
- financials.historical_financials is per fiscal year; `earnings` equals peers'
  `net_income`, so it is net profit. Ratios and growth are fractions (0.2043 = 20.43%).
- GET /v2/daily/{symbol}/ -> 1 credit; default window is the 30 days ending today. Fields:
  date, close (IDR), open/high/low (nullable), volume (shares), market_cap (IDR). Order
  and corporate-action adjustment are NOT documented, so rows are sorted here and the
  change is called a price change, never a return.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.cache import EmptyData, Fetched, TTLCache, describe_api_error
from app.config import settings
from app.sectors import SectorsError, get_client
from app.subsector import num

log = logging.getLogger(__name__)

TICKER_RE = re.compile(r"^[A-Z]{4}$")
SEARCH_RE = re.compile(r"^[a-z0-9]{3,30}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# What the card needs: identity/price (overview), P/E TTM + P/B MRQ (peers), yield
# (dividend), revenue/profit/ROE (financials). `valuation` is fetched only when its tab
# is opened, so most users spend 4 + 1 credits, not 5 + 1.
BASE_SECTIONS = ("dividend", "financials", "overview", "peers")
VALUATION_SECTIONS = ("valuation",)
SECTION_LABELS = {
    "overview": "profil & harga",
    "peers": "P/E & P/B",
    "financials": "keuangan",
    "dividend": "dividen",
    "valuation": "valuasi historis",
}


class NotListed(Exception):
    """The API says this symbol does not exist (404)."""


def describe_error(exc: Exception) -> str:
    return describe_api_error(exc, not_found="Kode saham tidak ditemukan di IDX (404).")


# ----------------------------------------------------------------------------- input


def normalize_ticker(text: str) -> str | None:
    """'bbca', 'BBCA.JK', ' Bbca.jk ' -> 'BBCA'. Shape only: existence is the API's call."""
    t = text.strip().upper().removesuffix(".JK")
    return t if TICKER_RE.match(t) else None


@dataclass(frozen=True)
class Parsed:
    kind: str  # help | ticker | search | multi | invalid
    value: str = ""
    tickers: tuple[str, ...] = ()


def parse_args(args: list[str]) -> Parsed:
    tokens = " ".join(args).replace(",", " ").split()
    if not tokens:
        return Parsed("help")
    if len(tokens) > 1:
        tickers = tuple(t for t in map(normalize_ticker, tokens) if t)
        if len(tickers) == len(tokens):
            return Parsed("multi", tickers=tickers)  # several stocks -> point to /compare
        return Parsed("invalid")
    if ticker := normalize_ticker(tokens[0]):
        return Parsed("ticker", ticker)
    keyword = tokens[0].lower()
    if SEARCH_RE.match(keyword):  # strict charset: it goes into a screener `where`
        return Parsed("search", keyword)
    return Parsed("invalid")


# ----------------------------------------------------------------------------- prices


@dataclass
class PricePoint:
    date: str
    close: float
    volume: float | None
    market_cap: float | None


@dataclass
class PriceInfo:
    last: PricePoint
    prev: PricePoint | None
    change: float | None  # last.close - prev.close, IDR
    change_pct: float | None  # fraction
    rows: list[PricePoint] = field(default_factory=list)  # sorted, valid only


def compute_price(raw: Any) -> PriceInfo | None:
    """Last two valid closes on distinct dates, oldest-first after sorting here.

    A close that is missing, zero or negative is not an observation (a holiday filled
    with 0 must not become a -100% day), so it is skipped rather than compared against.
    """
    rows = raw if isinstance(raw, list) else (raw.get("results") or raw.get("data") or []) if isinstance(raw, dict) else []
    by_date: dict[str, PricePoint] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        date, close = r.get("date"), num(r.get("close"))
        if not isinstance(date, str) or not DATE_RE.match(date) or close is None or close <= 0:
            continue
        by_date[date] = PricePoint(date, close, num(r.get("volume")), num(r.get("market_cap")))
    if not by_date:
        return None
    points = [by_date[d] for d in sorted(by_date)]
    last = points[-1]
    prev = points[-2] if len(points) > 1 else None
    if prev is None or not prev.close:
        return PriceInfo(last, prev, None, None, points)
    change = last.close - prev.close
    return PriceInfo(last, prev, change, change / prev.close, points)


# ----------------------------------------------------------------------------- report


@dataclass
class FinYear:
    year: int
    revenue: float | None = None
    earnings: float | None = None
    roe: float | None = None
    roa: float | None = None
    net_margin: float | None = None
    revenue_growth: float | None = None  # vs the previous fiscal year, computed here
    earnings_growth: float | None = None


@dataclass
class DivYear:
    year: int
    total: float | None
    yield_: float | None


@dataclass
class Peer:
    symbol: str
    name: str
    market_cap: float | None
    pe_ttm: float | None
    pb_mrq: float | None


@dataclass
class StockReport:
    symbol: str
    name: str
    missing: list[str] = field(default_factory=list)
    # overview
    sector: str = ""
    sub_sector: str = ""
    industry: str = ""
    sub_industry: str = ""
    listing_board: str = ""
    listing_date: str | None = None
    employees: float | None = None
    website: str = ""
    address: str = ""
    indices: list[str] = field(default_factory=list)
    affiliates: list[str] = field(default_factory=list)
    market_cap: float | None = None
    close: float | None = None
    close_date: str | None = None
    low_52w: tuple[str, float] | None = None
    high_52w: tuple[str, float] | None = None
    # peers
    pe_ttm: float | None = None
    pb_mrq: float | None = None
    ratio_market_cap: float | None = None  # the market cap P/E and P/B were computed on
    peers: list[Peer] = field(default_factory=list)
    # financials
    eps: float | None = None
    eps_year: int | None = None
    fin_years: list[FinYear] = field(default_factory=list)
    ttm_revenue_growth: float | None = None
    ttm_earnings_growth: float | None = None
    quarter_revenue_growth: float | None = None
    quarter_earnings_growth: float | None = None
    # dividend
    yield_ttm: float | None = None
    dividend_ttm: float | None = None
    payout_ratio: float | None = None
    cash_payout_ratio: float | None = None
    avg_yield: float | None = None
    avg_yield_years: int | None = None
    last_ex_date: str | None = None
    has_upcoming: bool = False
    div_years: list[DivYear] = field(default_factory=list)

    @property
    def latest_fin(self) -> FinYear | None:
        rows = [f for f in self.fin_years if f.revenue is not None or f.earnings is not None]
        return rows[-1] if rows else None

    @property
    def has_financials(self) -> bool:
        return "financials" not in self.missing and bool(
            self.fin_years or self.eps is not None or self.ttm_revenue_growth is not None
        )

    @property
    def has_dividend(self) -> bool:
        return "dividend" not in self.missing and bool(
            self.div_years or self.yield_ttm is not None or self.dividend_ttm is not None
        )


@dataclass
class ValuationDetail:
    forward_pe: float | None
    years: list[dict[str, Any]]  # year, pe, pb, ps, pcf, pe_peer_avg, pb_peer_avg


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _str(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _int(v: Any) -> int | None:
    n = num(v)
    return int(n) if n is not None and n == int(n) else None


def _date(v: Any) -> str | None:
    return v if isinstance(v, str) and DATE_RE.match(v) else None


def _strings(v: Any) -> list[str]:
    return [x for x in v if isinstance(x, str) and x] if isinstance(v, list) else []


def _dated_price(v: Any) -> tuple[str, float] | None:
    """{'2026-06-09': 4820} -> ('2026-06-09', 4820.0)."""
    for d, p in _dict(v).items():
        if _date(d) and (price := num(p)) is not None:
            return d, price
    return None


def _growth(cur: float | None, prev: float | None) -> float | None:
    """YoY growth only against a positive base: from a loss, a % change is meaningless."""
    if cur is None or prev is None or prev <= 0:
        return None
    return (cur - prev) / prev


def _peer_companies(raw: Any) -> list[dict]:
    blocks = raw if isinstance(raw, list) else [raw]
    out: list[dict] = []
    for block in blocks:
        out += [c for c in _dict(_dict(block).get("peers_data")).get("companies") or [] if isinstance(c, dict)]
    return out


def parse_report(symbol: str, raw: Any, sections: tuple[str, ...] = BASE_SECTIONS) -> StockReport:
    if not isinstance(raw, dict):
        raise EmptyData("company report is not an object")
    present = [s for s in sections if raw.get(s)]
    if not present:
        raise EmptyData("no report sections")
    r = StockReport(
        symbol=symbol,
        name=_str(raw.get("company_name")),
        missing=[s for s in sections if s not in present],
    )

    ov = _dict(raw.get("overview"))
    r.sector, r.sub_sector = _str(ov.get("sector")), _str(ov.get("sub_sector"))
    r.industry, r.sub_industry = _str(ov.get("industry")), _str(ov.get("sub_industry"))
    r.listing_board, r.listing_date = _str(ov.get("listing_board")), _date(ov.get("listing_date"))
    r.employees, r.website = num(ov.get("employee_num")), _str(ov.get("website"))
    r.address = " ".join(_str(ov.get("address")).split())  # API sends \r\n line breaks
    r.indices, r.affiliates = _strings(ov.get("indices")), _strings(ov.get("affiliates"))
    r.market_cap, r.close = num(ov.get("market_cap")), num(ov.get("last_close_price"))
    r.close_date = _date(ov.get("latest_close_date"))
    prices = _dict(ov.get("all_time_price"))
    r.low_52w, r.high_52w = _dated_price(prices.get("52_w_low")), _dated_price(prices.get("52_w_high"))

    me = f"{symbol}.JK"
    peers = _peer_companies(raw.get("peers"))
    for c in peers:
        if "self" in (c.get("group") or []) or c.get("symbol") == me:
            r.pe_ttm, r.pb_mrq = num(c.get("pe_ttm")), num(c.get("pb_mrq"))
            r.ratio_market_cap = num(c.get("market_cap"))
            r.name = r.name or _str(c.get("company_name"))
        else:
            r.peers.append(
                Peer(
                    _str(c.get("symbol")).removesuffix(".JK"),
                    _str(c.get("company_name")),
                    num(c.get("market_cap")),
                    num(c.get("pe_ttm")),
                    num(c.get("pb_mrq")),
                )
            )
    r.peers = sorted(r.peers, key=lambda p: (p.market_cap is None, -(p.market_cap or 0)))[:5]

    fin = _dict(raw.get("financials"))
    r.eps = num(fin.get("eps"))
    for y, v in _dict(fin.get("historical_eps")).items():  # label EPS with its year only on a match
        if r.eps is not None and num(_dict(v).get("eps")) == r.eps:
            r.eps_year = _int(y)
    years: dict[int, FinYear] = {}
    for row in fin.get("historical_financials") or []:
        if (y := _int(_dict(row).get("year"))) is not None:
            years[y] = FinYear(y, num(row.get("revenue")), num(row.get("earnings")))
    for row in fin.get("historical_financial_ratio") or []:
        if (y := _int(_dict(row).get("year"))) is not None:
            prof = _dict(row.get("profitability"))
            f = years.setdefault(y, FinYear(y))
            f.roe, f.roa, f.net_margin = num(prof.get("roe")), num(prof.get("roa")), num(prof.get("net_profit_margin"))
    r.fin_years = [years[y] for y in sorted(years)]
    for prev, cur in zip(r.fin_years, r.fin_years[1:]):
        if cur.year == prev.year + 1:  # never compare across a gap
            cur.revenue_growth = _growth(cur.revenue, prev.revenue)
            cur.earnings_growth = _growth(cur.earnings, prev.earnings)
    r.ttm_revenue_growth = num(fin.get("yoy_ttm_revenue_growth"))
    r.ttm_earnings_growth = num(fin.get("yoy_ttm_earnings_growth"))
    r.quarter_revenue_growth = num(fin.get("yoy_quarter_revenue_growth"))
    r.quarter_earnings_growth = num(fin.get("yoy_quarter_earnings_growth"))

    div = _dict(raw.get("dividend"))
    r.yield_ttm, r.dividend_ttm = num(div.get("yield_ttm")), num(div.get("dividend_ttm"))
    r.payout_ratio, r.cash_payout_ratio = num(div.get("payout_ratio")), num(div.get("cash_payout_ratio"))
    avg = _dict(div.get("dividend_yield_avg"))
    r.avg_yield, r.avg_yield_years = num(avg.get("avg_yield")), _int(avg.get("period"))
    r.last_ex_date = _date(div.get("last_ex_dividend_date"))
    r.has_upcoming = bool(div.get("upcoming_dividends"))
    r.div_years = sorted(
        (
            DivYear(y, num(_dict(v).get("total_dividend")), num(_dict(v).get("total_yield")))
            for k, v in _dict(div.get("historical_dividends")).items()
            if (y := _int(k)) is not None
        ),
        key=lambda d: d.year,
    )
    return r


def parse_valuation(raw: Any) -> ValuationDetail:
    val = _dict(_dict(raw).get("valuation"))
    if not val:
        raise EmptyData("no valuation section")
    keep = ("pe", "pb", "ps", "pcf", "pe_peer_avg", "pb_peer_avg")
    years = []
    for row in val.get("historical_valuation") or []:
        if (y := _int(_dict(row).get("year"))) is not None:
            years.append({"year": y, **{k: num(row.get(k)) for k in keep}})
    return ValuationDetail(num(val.get("forward_pe")), sorted(years, key=lambda d: d["year"]))


# ----------------------------------------------------------------------------- fetching

_daily_cache = TTLCache(settings.stock_daily_ttl, settings.stock_stale_max)
_report_cache = TTLCache(settings.stock_report_ttl, settings.stock_stale_max)
_search_cache = TTLCache(settings.stock_report_ttl, settings.stock_stale_max)


def reset_caches() -> None:
    for c in (_daily_cache, _report_cache, _search_cache):
        c.clear()


# The daily request uses the API's default window (30 days ending "today" on the API's
# clock). We don't pass `end` ourselves: WIB runs ahead of UTC, and a future `end` is a 400.
def _daily_key(symbol: str) -> tuple:
    return ("daily", symbol, "default-window")


def _report_key(symbol: str, sections: tuple[str, ...]) -> tuple:
    return ("company_report", symbol, sections)


async def _fetch_report_raw(symbol: str, sections: tuple[str, ...]) -> Any:
    return await get_client().get(
        "company_report", path_params={"symbol": symbol}, params={"sections": ",".join(sections)}
    )


async def get_price(symbol: str) -> Fetched[PriceInfo | None]:
    async def fetch():
        return compute_price(await get_client().get("daily", path_params={"symbol": symbol}))

    return await _daily_cache.get(_daily_key(symbol), fetch)


async def get_report(symbol: str) -> Fetched[StockReport]:
    async def fetch():
        return parse_report(symbol, await _fetch_report_raw(symbol, BASE_SECTIONS))

    return await _report_cache.get(_report_key(symbol, BASE_SECTIONS), fetch)


async def get_valuation(symbol: str) -> Fetched[ValuationDetail]:
    async def fetch():
        return parse_valuation(await _fetch_report_raw(symbol, VALUATION_SECTIONS))

    return await _report_cache.get(_report_key(symbol, VALUATION_SECTIONS), fetch)


def peek_valuation(symbol: str) -> Fetched[ValuationDetail] | None:
    return _report_cache.peek(_report_key(symbol, VALUATION_SECTIONS))


async def search(keyword: str) -> list[tuple[str, str]]:
    """Name search through the screener. `keyword` already matched SEARCH_RE."""
    if not SEARCH_RE.match(keyword):
        raise ValueError(f"bad search keyword: {keyword!r}")

    async def fetch():
        raw = await get_client().get(
            "companies",
            params={"where": f"company_name like '%{keyword}%'", "order_by": "-market_cap", "limit": 6},
        )
        rows = raw.get("results") if isinstance(raw, dict) else raw
        out = []
        for r in rows if isinstance(rows, list) else []:
            sym = normalize_ticker(_str(_dict(r).get("symbol")))
            if sym and sym not in {s for s, _ in out}:
                out.append((sym, _str(r.get("company_name"))))
        return out

    return (await _search_cache.get(("search", keyword), fetch)).value


@dataclass
class StockCard:
    symbol: str
    price: Fetched[PriceInfo | None] | None = None
    price_error: str | None = None
    report: Fetched[StockReport] | None = None
    report_error: str | None = None
    valuation: Fetched[ValuationDetail] | None = None
    valuation_error: str | None = None

    @property
    def name(self) -> str:
        return self.report.value.name if self.report else ""

    def tabs(self) -> list[str]:
        """Only offer views that have data behind them."""
        out = ["ov"]
        if self.report:
            r = self.report.value
            if "overview" not in r.missing:
                out.append("pr")
            if r.has_financials:
                out.append("fi")
            out.append("va")  # its detail is fetched on demand
            if r.has_dividend:
                out.append("dv")
        return out


def _is_404(x: Any) -> bool:
    return isinstance(x, SectorsError) and x.status == 404


def _note_failure(what: str, symbol: str, exc: BaseException) -> None:
    if isinstance(exc, (SectorsError, EmptyData)):
        log.warning("/stock %s %s failed: %s", symbol, what, exc)
    else:
        log.error("/stock %s %s failed", symbol, what, exc_info=exc)


async def load_card(symbol: str) -> StockCard:
    """Report and prices are independent: fetch them together, keep whichever arrives."""
    price, report = await asyncio.gather(get_price(symbol), get_report(symbol), return_exceptions=True)
    if _is_404(report) or (_is_404(price) and isinstance(report, BaseException)):
        raise NotListed(symbol)
    if isinstance(price, BaseException) and isinstance(report, BaseException):
        _note_failure("prices", symbol, price)
        raise report
    card = StockCard(symbol)
    if isinstance(price, BaseException):
        _note_failure("prices", symbol, price)
        card.price_error = describe_error(price)
    else:
        card.price = price
    if isinstance(report, BaseException):
        _note_failure("report", symbol, report)
        card.report_error = describe_error(report)
    else:
        card.report = report
    return card


def peek_card(symbol: str) -> StockCard | None:
    """For button taps: what was already fetched, no network. None = session expired."""
    report = _report_cache.peek(_report_key(symbol, BASE_SECTIONS))
    price = _daily_cache.peek(_daily_key(symbol))
    if report is None and price is None:
        return None
    card = StockCard(symbol, price=price, report=report, valuation=peek_valuation(symbol))
    if price is None:
        card.price_error = "Data harga tidak lagi tersimpan; kirim /stock lagi untuk memuat ulang."
    if report is None:
        card.report_error = "Laporan perusahaan tidak lagi tersimpan; kirim /stock lagi untuk memuat ulang."
    return card


# ----------------------------------------------------------------------------- buttons

VIEWS = {"ov": "Ringkasan", "pr": "Profil", "fi": "Keuangan", "va": "Valuasi", "dv": "Dividen"}
_CALLBACK_RE = re.compile(r"^stk:(?:(?P<s>s):(?P<s_sym>[A-Z]{4})|(?P<v>v):(?P<tab>ov|pr|fi|va|dv):(?P<v_sym>[A-Z]{4}))$")


def cb_select(symbol: str) -> str:
    return f"stk:s:{symbol}"


def cb_view(tab: str, symbol: str) -> str:
    return f"stk:v:{tab}:{symbol}"  # at most 13 bytes, far under Telegram's 64


def parse_callback(data: str | None) -> tuple[str, Any] | None:
    m = _CALLBACK_RE.match(data or "")
    if m is None:
        return None
    if m["s"]:
        return "select", m["s_sym"]
    return "view", (m["tab"], m["v_sym"])
