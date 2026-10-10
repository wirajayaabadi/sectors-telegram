import json
import logging

from agents import function_tool

from app.sectors import SectorsError, get_client

log = logging.getLogger(__name__)


def _rows(data) -> list:
    """Pull the list of rows out of a raw response (bare list or {'results'|'data': [...]})."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("results") or data.get("data") or []
    return []


def _pick(data, keep: tuple[str, ...], limit: int) -> list:
    """First `limit` rows, keeping only the `keep` fields (drops fat fields like body/thumbnail)."""
    return [{k: r[k] for k in keep if k in r} for r in _rows(data)[:limit]]


async def _out(fetcher, transform=None) -> str:
    """Run a fetch; optionally reshape it; return compact JSON for the LLM or an error string.

    transform(data) -> anything JSON-serialisable. Use it to trim fat payloads
    (e.g. last N rows, or only certain fields) so requests fit the model's token limit.
    """
    try:
        data = await fetcher
        if transform is not None:
            data = transform(data)
        return json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":"))
    except SectorsError as e:
        return f"ERROR (data API): {e}. Tell the user this part is unavailable."
    except ValueError as e:  # bad input from the model (e.g. invalid ticker)
        return f"ERROR (invalid input): {e}"


@function_tool
async def get_daily(symbol: str, n_rows: int = 7) -> str:
    """
    Daily close price and volume for a given IDX symbol. Returns the last n_rows, oldest to newest.
    To get the 1-d change, take the last two rows and call `change` tool.

    Args:
        symbol (str): 4-letter IDX ticker, e.g. BBCA.
        n_rows (int): How many recent days to return. Default 7; only ask for more if truly needed.
    """
    return await _out(
        get_client().get("daily", path_params={"symbol": symbol}, params={}),
        transform=lambda d: _rows(d)[-max(1, n_rows) :],
    )


@function_tool
async def get_index(index_code: str = "ihsg", n_rows: int = 7) -> str:
    """Daily level of an IDX index. Returns the last n_rows, oldest-to-newest.

    Args:
        index_code: "ihsg" (composite) or "lq45".
        n_rows: How many recent days to return. Default 7; only ask for more if truly needed.
    """
    return await _out(
        get_client().get(
            "index_daily", path_params={"index_code": index_code.lower()}
        ),
        transform=lambda d: _rows(d)[-max(1, n_rows) :],
    )


@function_tool
async def get_idx_total(n_rows: int = 7) -> str:
    """Total IDX market capitalisation over recent trading days.

    Market cap is pre-scaled to TRILLIONS of rupiah (idx_total_market_cap_idr_trillion),
    so use that value directly - do not multiply or rescale it.

    Args:
        n_rows: How many recent days to return. Default 7; only ask for more if truly needed.
    """

    def scale(data):
        out = []
        for r in _rows(data)[-max(1, n_rows) :]:
            cap = r.get("idx_total_market_cap")
            out.append(
                {
                    "date": r.get("date"),
                    "idx_total_market_cap_idr_trillion": (
                        round(cap / 1e12, 2) if cap else None
                    ),
                }
            )
        return out

    return await _out(get_client().get("idx_total"), transform=scale)


@function_tool
async def get_foreign_flow(symbol: str, n_rows: int = 7) -> str:
    """Daily net foreign inflow for one IDX stock. Positive = foreigners net bought.

    Args:
        symbol: 4-letter IDX ticker, e.g. BBCA.
        n_rows: How many recent days to return. Default 7; only ask for more if truly needed.
    """
    return await _out(
        get_client().get("foreign_flow", path_params={"symbol": symbol.upper()}),
        transform=lambda d: _rows(d)[-max(1, n_rows) :],
    )


# ------------------------------------------------------------------ reports / screener
@function_tool
async def get_company_report(
    symbol: str, sections: str = "overview,financials,dividend"
) -> str:
    """IDX company report. Pass only the sections you need to keep it small.

    Market cap is pre-scaled: use market_cap_scaled with market_cap_unit as given; do not
    rescale. This report has NO 'last close date' - for a dated latest price, call get_daily.

    Args:
        symbol: 4-letter IDX ticker, e.g. BBCA.
        sections: comma-separated: overview, financials, dividend, valuation, peers,
            future, management, ownership. Ask for financials for growth questions.
    """

    def scale(data):
        ov = data.get("overview") if isinstance(data, dict) else None
        if isinstance(ov, dict) and ov.get("market_cap"):
            ov["market_cap_scaled"] = round(ov["market_cap"] / 1e12, 2)
            ov["market_cap_unit"] = "trillion IDR"
            ov.pop(
                "market_cap", None
            )  # drop the raw giant int so the model can't mis-scale it
        return data

    return await _out(
        get_client().get(
            "company_report",
            path_params={"symbol": symbol.upper()},
            params={"sections": sections},
        ),
        transform=scale,
    )


def _scalars(section) -> dict:
    """Keep only scalar fields, dropping every list/dict.

    The report's bulk is historical series (historical_financials, historical_dividends,
    ...) which alone blow past a free-tier per-minute token limit, while every figure a
    side-by-side comparison needs is a scalar.
    """
    if not isinstance(section, dict):
        return {}
    return {
        k: v for k, v in section.items() if v is None or isinstance(v, (int, float, str, bool))
    }


@function_tool
async def get_company_snapshot(symbol: str) -> str:
    """Compact IDX company snapshot: the headline figures only, no historical series.

    Use this instead of get_company_report when comparing companies side by side, or
    whenever you only need current figures. Market cap is pre-scaled: use
    market_cap_scaled with market_cap_unit as given; do not rescale it.

    Args:
        symbol: 4-letter IDX ticker, e.g. BBCA.
    """

    def trim(data):
        if not isinstance(data, dict):
            return data
        out = {
            "symbol": data.get("symbol"),
            "company_name": data.get("company_name"),
        }
        for name in ("overview", "financials", "dividend", "valuation"):
            section = _scalars(data.get(name))
            if name == "overview" and section.get("market_cap"):
                section["market_cap_scaled"] = round(section["market_cap"] / 1e12, 2)
                section["market_cap_unit"] = "trillion IDR"
                # drop the raw giant int so the model can't mis-scale it
                section.pop("market_cap", None)
            if section:
                out[name] = section
        return out

    return await _out(
        get_client().get(
            "company_report",
            path_params={"symbol": symbol.upper()},
            params={"sections": "overview,financials,dividend,valuation"},
        ),
        transform=trim,
    )


@function_tool
async def get_subsector_report(
    sub_sector: str, sections: str = "companies,growth,valuation"
) -> str:
    """Report for one IDX sub-sector.

    Args:
        sub_sector: slug, e.g. "banks", "basic-materials".
        sections: comma-separated: companies, growth, market_cap, stability, statistics, valuation.
    """
    return await _out(
        get_client().get(
            "subsector_report",
            path_params={"sub_sector": sub_sector},
            params={"sections": sections},
        )
    )


@function_tool
async def screen_companies(
    q: str = "",
    where: str = "",
    order_by: str = "-market_cap",
    limit: int = 10,
    offset: int = 0,
) -> str:
    """Screen IDX companies. Either ask in plain English (`q`) OR use a SQL-like filter (`where`).

    Args:
        q: natural-language screen, e.g. "biggest banks by market cap". Takes precedence if set.
        where: SQL-like filter, e.g. "market_cap > 1000000000000 AND sub_sector = 'banks'".
        order_by: field to sort by, e.g. "market_cap". if desc, use `-` in front of the field.
        limit: number of companies (1-200).
        offset.
    """
    params = {
        "order_by": order_by,
        "limit": max(1, min(200, limit)),
        "offset": max(0, min(200, offset)),
    }
    if q:
        params["q"] = q  # per API: q takes precedence over where
    elif where:
        params["where"] = where
    log.info(f"Executing screener with params: {json.dumps(params)}")
    # Keep only symbol + name. The API also returns a large `query_values` blob explaining
    # each match; dropping it keeps the payload small and stops the model mis-reading tickers.
    return await _out(
        get_client().get("companies", params=params),
        transform=lambda d: [
            {"symbol": r.get("symbol"), "company_name": r.get("company_name")}
            for r in _rows(d)
        ],
    )


# ------------------------------------------------------------------ text
@function_tool
async def get_news(symbols: str = "", limit: int = 4) -> str:
    """Recent IDX news headlines (title, date, source, tickers - not full text).

    Args:
        symbols: optional comma-separated tickers to filter by, e.g. "BBCA,BBRI".
        limit: number of articles. Default 4; keep it small.
    """
    limit = max(1, min(10, limit))
    params = {"limit": limit}
    if symbols:
        params["symbols"] = symbols.upper()
    return await _out(
        get_client().get("news", params=params),
        transform=lambda d: _pick(
            d, ("title", "timestamp", "source", "symbols", "sub_sector"), limit
        ),
    )


@function_tool
async def get_filings(symbol: str = "", limit: int = 4) -> str:
    """Recent IDX insider / ownership filings (key fields only, not full text).

    Args:
        symbol: optional ticker to filter by, e.g. BBCA.
        limit: number of filings. Default 4; keep it small.
    """
    limit = max(1, min(10, limit))
    params = {"limit": limit}
    if symbol:
        params["symbol"] = symbol.upper()
    return await _out(
        get_client().get("filings", params=params),
        transform=lambda d: _pick(
            d,
            (
                "title",
                "timestamp",
                "symbol",
                "transaction_type",
                "holder_name",
                "share_percentage_transaction",
            ),
            limit,
        ),
    )


@function_tool
async def change(series: list[float]) -> str:
    """
    Calculate the change and percent change between the last two values in a series of numbers.

    Args:
        series (list[float]): A list of numbers representing a time series, oldest to newest. Must contain at least two values.
    """
    log.info("Calling change tool")
    if len(series) < 2:
        return json.dumps(
            {"error": "Series must contain at least two values to calculate change."}
        )
    last = series[-1]
    prev = series[-2]
    change_value = last - prev
    change_pct = (change_value / prev * 100) if prev else 0
    changes = [None]
    for i in range(1, len(series)):
        prev_value = series[i - 1]
        curr_value = series[i]
        change_val = curr_value - prev_value
        change_percentage = (change_val / prev_value * 100) if prev_value else 0
        changes.append({"change": change_val, "percent_change": change_percentage})
    return json.dumps(
        {
            "latest_change": change_value,
            "latest_percent_change": change_pct,
            "all_changes": changes,
        },
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
