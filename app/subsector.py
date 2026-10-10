"""/sector: one IDX sub-sector's companies, growth and valuation, in plain code (no LLM).

    input -> match a verified slug -> cache/API -> normalise -> Python maths -> render

Every number comes from the Sectors API; the derived ones (market-cap shares, TTM margins,
rankings) are computed here; render.py turns them into text. No agent is involved, so
/sector keeps working when GROQ_API_KEY is missing or the LLM is down.

API facts, checked against docs.sectors.app AND a live response (Oct 2026):
- GET /v2/subsectors/ -> [{"sector": slug, "subsector": slug}, ...]; 1 credit.
- GET /v2/subsector/report/{slug}/?sections=a,b -> 1 credit PER section requested.
- Growth and change figures are fractions (-0.234 = -23.4%). Money is in rupiah: the docs
  don't say so, but the live values match (BBCA market cap ~Rp 738 T).
- The report carries no as-of date, and its company maps ("BBCA.JK": {...}) are NOT
  sorted, so ordering is done here.
- "filtered" PE, "weighted" growth and the *_rank fields are not defined in the docs. We
  show them under their API meaning and never re-aggregate company ratios ourselves.
"""

from __future__ import annotations

import difflib
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.cache import EmptyData, Fetched, TTLCache, describe_api_error
from app.config import settings
from app.sectors import get_client

log = logging.getLogger(__name__)

# market_cap, not stability: we show market-cap moves but nothing from stability, and each
# section is a credit.
REPORT_SECTIONS = ("statistics", "market_cap", "growth", "valuation", "companies")
SECTION_LABELS = {
    "statistics": "statistik",
    "market_cap": "market cap",
    "growth": "pertumbuhan",
    "valuation": "valuasi",
    "companies": "perusahaan",
}

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Readable Indonesian names for the 33 slugs the API returned in Oct 2026. A slug missing
# here still works; it just falls back to a title-cased version of itself.
LABELS: dict[str, str] = {
    "alternative-energy": "Energi Alternatif",
    "apparel-luxury-goods": "Pakaian & Barang Mewah",
    "automobiles-components": "Otomotif & Komponen",
    "banks": "Bank",
    "basic-materials": "Barang Baku",
    "consumer-services": "Jasa Konsumen",
    "financing-service": "Jasa Pembiayaan",
    "food-beverage": "Makanan & Minuman",
    "food-staples-retailing": "Ritel Bahan Pokok",
    "healthcare-equipment-providers": "Peralatan & Penyedia Kesehatan",
    "heavy-constructions-civil-engineering": "Konstruksi Berat & Teknik Sipil",
    "holding-investment-companies": "Holding & Investasi",
    "household-goods": "Barang Rumah Tangga",
    "industrial-goods": "Barang Perindustrian",
    "industrial-services": "Jasa Perindustrian",
    "insurance": "Asuransi",
    "investment-service": "Jasa Investasi",
    "leisure-goods": "Barang Rekreasi",
    "logistics-deliveries": "Logistik & Pengantaran",
    "media-entertainment": "Media & Hiburan",
    "multi-sector-holdings": "Holding Multisektor",
    "nondurable-household-products": "Produk Rumah Tangga Habis Pakai",
    "oil-gas-coal": "Minyak, Gas & Batu Bara",
    "pharmaceuticals-health-care-research": "Farmasi & Riset Kesehatan",
    "properties-real-estate": "Properti & Real Estat",
    "retailing": "Perdagangan Ritel",
    "software-it-services": "Perangkat Lunak & Jasa TI",
    "technology-hardware-equipment": "Perangkat Keras Teknologi",
    "telecommunication": "Telekomunikasi",
    "tobacco": "Rokok",
    "transportation": "Transportasi",
    "transportation-infrastructure": "Infrastruktur Transportasi",
    "utilities": "Utilitas",
}

# Extra words people type. Each one only counts if its target is in the LIVE list, so a
# renamed slug can never be reached through a stale alias. Deliberately no alias for a
# word that fits two subsectors ("teknologi", "holding").
ALIASES: dict[str, str] = {
    "bank": "banks",
    "perbankan": "banks",
    "banking": "banks",
    "telko": "telecommunication",
    "telco": "telecommunication",
    "telekomunikasi": "telecommunication",
    "properti": "properties-real-estate",
    "real estate": "properties-real-estate",
    "tembakau": "tobacco",
    "migas": "oil-gas-coal",
    "batubara": "oil-gas-coal",
    "batu bara": "oil-gas-coal",
    "makanan minuman": "food-beverage",
    "mamin": "food-beverage",
    "farmasi": "pharmaceuticals-health-care-research",
    "otomotif": "automobiles-components",
    "ritel": "retailing",
    "konstruksi": "heavy-constructions-civil-engineering",
    "logistik": "logistics-deliveries",
    "media": "media-entertainment",
    "pembiayaan": "financing-service",
    "multifinance": "financing-service",
    "energi terbarukan": "alternative-energy",
}


# ----------------------------------------------------------------------------- errors + cache


def describe_error(exc: Exception) -> str:
    return describe_api_error(exc, not_found="Sectors tidak punya laporan untuk subsektor ini (404).")


_list_cache = TTLCache(settings.subsector_list_ttl, settings.subsector_stale_max)
_report_cache = TTLCache(settings.subsector_report_ttl, settings.subsector_stale_max)


def reset_caches() -> None:
    _list_cache.clear()
    _report_cache.clear()


# ----------------------------------------------------------------------------- list + matching


@dataclass(frozen=True)
class Subsector:
    slug: str
    sector: str

    @property
    def label(self) -> str:
        return label_for(self.slug)


def label_for(slug: str) -> str:
    return LABELS.get(slug) or slug.replace("-", " ").title()


def parse_subsectors(raw: Any) -> list[Subsector]:
    """Validate the list: kebab-case slugs only (they go into URLs and callback data)."""
    seen: dict[str, Subsector] = {}
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        slug, sector = row.get("subsector"), row.get("sector")
        if isinstance(slug, str) and SLUG_RE.match(slug) and slug not in seen:
            seen[slug] = Subsector(slug, sector if isinstance(sector, str) else "")
    if not seen:
        raise EmptyData("empty subsector list")
    return sorted(seen.values(), key=lambda s: s.label.lower())


async def _fetch_subsectors() -> list[Subsector]:
    return parse_subsectors(await get_client().get("subsectors"))


async def get_subsectors() -> Fetched[list[Subsector]]:
    return await _list_cache.get(("subsectors",), _fetch_subsectors)


def normalize(text: str) -> str:
    """'Food & Beverage', 'food_beverage', ' FOOD  beverage ' -> 'food-beverage'."""
    tokens = re.split(r"[^a-z0-9]+", text.lower())
    return "-".join(t for t in tokens if t and t not in {"and", "dan"})


@dataclass(frozen=True)
class Match:
    slug: str | None  # set only for an exact slug, label or alias hit
    candidates: tuple[str, ...] = ()  # "did you mean" options; never auto-picked


def match(query: str, subsectors: list[Subsector], limit: int = 6) -> Match:
    q = normalize(query)
    if not q:
        return Match(None)
    valid = {s.slug for s in subsectors}
    if q in valid:
        return Match(q)

    names: dict[str, str] = {}  # normalized name -> slug, verified targets only
    for slug in valid:
        names[slug] = slug
        names.setdefault(normalize(label_for(slug)), slug)
    for alias, slug in ALIASES.items():
        if slug in valid:
            names.setdefault(normalize(alias), slug)
    if q in names:
        return Match(names[q])

    found: list[str] = []
    if len(q) >= 3:
        found += [slug for key, slug in names.items() if q in key or key in q]
    found += [names[k] for k in difflib.get_close_matches(q, list(names), n=limit, cutoff=0.6)]
    unique = tuple(dict.fromkeys(found))[:limit]
    return Match(None, unique)


# ----------------------------------------------------------------------------- report


@dataclass
class CompanyValue:
    symbol: str
    name: str
    value: float | None
    share: float | None = None  # of subsector market cap (top_mcap only)
    margin: float | None = None  # profit_ttm / revenue_ttm (top_profit only)


@dataclass
class Mover:
    symbol: str
    name: str
    change_1m: float | None
    change_1y: float | None
    last_close: float | None
    pe: float | None


@dataclass
class GrowthYear:
    year: int
    revenue: float | None
    earnings: float | None


@dataclass
class Forecast:
    year: int
    base_year: int | None
    revenue: float | None
    eps: float | None


@dataclass
class ValuationYear:
    year: int
    pe: float | None
    pb: float | None
    ps: float | None
    pcf: float | None


@dataclass
class SubsectorReport:
    slug: str
    name: str  # as the API names it, e.g. "Banks"
    sector: str
    missing: list[str] = field(default_factory=list)  # requested sections that came back empty
    # statistics
    total_companies: int | None = None
    median_pe: float | None = None
    weighted_pe: float | None = None
    min_pe: float | None = None
    max_pe: float | None = None
    # market_cap
    total_mcap: float | None = None
    avg_mcap: float | None = None
    mcap_change: dict[str, float | None] = field(default_factory=dict)  # 1w / ytd / 1y
    last_monthly_date: str | None = None
    # growth / valuation
    growth: list[GrowthYear] = field(default_factory=list)
    forecasts: list[Forecast] = field(default_factory=list)
    valuation: list[ValuationYear] = field(default_factory=list)
    # companies, each sorted high -> low by its own value
    top_mcap: list[CompanyValue] = field(default_factory=list)
    top_revenue: list[CompanyValue] = field(default_factory=list)
    top_profit: list[CompanyValue] = field(default_factory=list)
    top_growth: list[CompanyValue] = field(default_factory=list)
    movers: list[Mover] = field(default_factory=list)
    # derived
    top_mcap_share: float | None = None

    @property
    def latest_growth(self) -> GrowthYear | None:
        rows = [g for g in self.growth if g.revenue is not None or g.earnings is not None]
        return rows[-1] if rows else None


def num(value: Any) -> float | None:
    """A finite number, or None. Missing stays missing: it is never turned into 0."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _int(value: Any) -> int | None:
    v = num(value)
    return int(v) if v is not None and v == int(v) else None


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _years(mapping: Any) -> list[tuple[int, dict]]:
    out = [(_int(k), _dict(v)) for k, v in _dict(mapping).items()]
    return sorted((y, v) for y, v in out if y is not None)


def _symbol(key: str) -> str:
    return key.removesuffix(".JK")


def _desc(value: float | None) -> tuple[bool, float]:
    """Sort key: biggest first, missing values last."""
    return (value is None, -(value or 0.0))


def _companies(mapping: Any, field_name: str) -> list[CompanyValue]:
    rows = [
        CompanyValue(_symbol(str(k)), str(_dict(v).get("name") or ""), num(_dict(v).get(field_name)))
        for k, v in _dict(mapping).items()
    ]
    return sorted(rows, key=lambda r: _desc(r.value))[:5]


def share(part: float | None, total: float | None) -> float | None:
    if part is None or total is None or total <= 0:
        return None
    return part / total


def parse_report(slug: str, raw: Any) -> SubsectorReport:
    if not isinstance(raw, dict):
        raise EmptyData("report is not an object")
    present = [s for s in REPORT_SECTIONS if _dict(raw.get(s))]
    if not present:
        raise EmptyData("no report sections")

    r = SubsectorReport(
        slug=slug,
        name=str(raw.get("sub_sector") or label_for(slug)),
        sector=str(raw.get("sector") or ""),
        missing=[s for s in REPORT_SECTIONS if s not in present],
    )

    stats = _dict(raw.get("statistics"))
    r.total_companies = _int(stats.get("total_companies"))
    r.median_pe = num(stats.get("filtered_median_pe"))
    r.weighted_pe = num(stats.get("filtered_weighted_avg_pe"))
    r.min_pe = num(stats.get("min_company_pe"))
    r.max_pe = num(stats.get("max_company_pe"))

    mcap = _dict(raw.get("market_cap"))
    r.total_mcap = num(mcap.get("total_market_cap"))
    r.avg_mcap = num(mcap.get("avg_market_cap"))
    summary = _dict(mcap.get("mcap_summary"))
    changes = _dict(summary.get("mcap_change"))
    r.mcap_change = {k: num(changes.get(k)) for k in ("1w", "ytd", "1y") if k in changes}
    monthly = sorted(k for k in _dict(summary.get("monthly_performance")) if isinstance(k, str))
    r.last_monthly_date = monthly[-1] if monthly else None

    growth = _dict(raw.get("growth"))
    r.growth = [
        GrowthYear(y, num(v.get("avg_annual_revenue_growth")), num(v.get("avg_annual_earning_growth")))
        for y, v in _years(growth.get("weighted_avg_growth_data"))
    ]
    r.forecasts = [
        Forecast(y, _int(v.get("base_year")), num(v.get("revenue_growth")), num(v.get("eps_growth")))
        for y, v in _years(growth.get("growth_forecasts"))
    ]

    r.valuation = [
        ValuationYear(y, num(v.get("pe")), num(v.get("pb")), num(v.get("ps")), num(v.get("pcf")))
        for y, v in _years(_dict(raw.get("valuation")).get("historical_valuation"))
    ]

    companies = _dict(raw.get("companies"))
    top = _dict(companies.get("top_companies"))
    r.top_mcap = _companies(top.get("top_mcap"), "market_cap")
    r.top_revenue = _companies(top.get("top_revenue"), "revenue_ttm")
    r.top_profit = _companies(top.get("top_profit"), "profit_ttm")
    r.top_growth = _companies(top.get("top_growth"), "revenue_growth")
    r.movers = sorted(
        (
            Mover(
                _symbol(str(k)),
                str(_dict(v).get("name") or ""),
                num(_dict(v).get("1mth")),
                num(_dict(v).get("1yr")),
                num(_dict(v).get("last_close")),
                num(_dict(v).get("pe")),
            )
            for k, v in _dict(companies.get("top_change_companies")).items()
        ),
        key=lambda m: _desc(m.change_1y),
    )[:5]

    _derive(r)
    return r


def _derive(r: SubsectorReport) -> None:
    """The arithmetic, done once in Python so the renderer only formats."""
    for c in r.top_mcap:
        c.share = share(c.value, r.total_mcap)
    values = [c.value for c in r.top_mcap]
    if values and None not in values:
        r.top_mcap_share = share(sum(values), r.total_mcap)  # type: ignore[arg-type]

    # Both TTM, so the ratio compares like with like. Only for companies in both lists.
    revenue = {c.symbol: c.value for c in r.top_revenue}
    for c in r.top_profit:
        rev = revenue.get(c.symbol)
        if c.value is not None and rev is not None and rev > 0:
            c.margin = c.value / rev


async def _fetch_report(slug: str) -> SubsectorReport:
    raw = await get_client().get(
        "subsector_report",
        path_params={"sub_sector": slug},
        params={"sections": ",".join(REPORT_SECTIONS)},
    )
    return parse_report(slug, raw)


def _report_key(slug: str) -> tuple:
    return ("report", slug, REPORT_SECTIONS)


async def get_report(slug: str) -> Fetched[SubsectorReport]:
    if not SLUG_RE.match(slug):
        raise ValueError(f"not a subsector slug: {slug!r}")
    return await _report_cache.get(_report_key(slug), lambda: _fetch_report(slug))


def peek_report(slug: str) -> Fetched[SubsectorReport] | None:
    """For button taps: reuse what was fetched, never spend credits."""
    return _report_cache.peek(_report_key(slug))


def peek_subsectors() -> Fetched[list[Subsector]] | None:
    return _list_cache.peek(("subsectors",))


# ----------------------------------------------------------------------------- buttons

# Telegram allows 64 bytes of callback_data. The longest slug today is 36 characters, so
# "sec:v:gr:<slug>" stays under 50; SLUG_MAX below keeps any future slug within budget.
VIEWS = {"ov": "Ringkasan", "co": "Perusahaan", "gr": "Pertumbuhan", "va": "Valuasi"}
SLUG_MAX = 50
PAGE_SIZE = 10

_CALLBACK_RE = re.compile(
    r"^sec:(?:(?P<page>p):(?P<n>\d{1,3})"
    r"|(?P<select>s):(?P<s_slug>[a-z0-9-]{1,%d})"
    r"|(?P<view>v):(?P<tab>ov|co|gr|va):(?P<v_slug>[a-z0-9-]{1,%d})"
    r"|(?P<noop>noop))$" % (SLUG_MAX, SLUG_MAX)
)


def cb_page(n: int) -> str:
    return f"sec:p:{n}"


def cb_select(slug: str) -> str:
    return f"sec:s:{slug}"


def cb_view(tab: str, slug: str) -> str:
    return f"sec:v:{tab}:{slug}"


CB_NOOP = "sec:noop"


def parse_callback(data: str | None) -> tuple[str, Any] | None:
    """('page', n) | ('select', slug) | ('view', (tab, slug)) | ('noop', None) | None."""
    m = _CALLBACK_RE.match(data or "")
    if m is None:
        return None
    if m["page"]:
        return "page", int(m["n"])
    if m["select"]:
        return ("select", m["s_slug"]) if SLUG_RE.match(m["s_slug"]) else None
    if m["view"]:
        return ("view", (m["tab"], m["v_slug"])) if SLUG_RE.match(m["v_slug"]) else None
    return "noop", None


def page_count(total: int) -> int:
    return max(1, math.ceil(total / PAGE_SIZE))
