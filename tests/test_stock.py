"""Tests for /stock. No network, no credits: a fake Sectors client serves the fixtures in
tests/fixtures/ (real API responses for BBCA captured on 8 Oct 2026).

    uv run python -m unittest discover -s tests -t . -v
"""

import asyncio
import copy
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Message
from telegram.constants import MessageLimit

from app import handlers
from app import stock as stocks
from app.cache import Fetched, TTLCache
from app.config import settings
from app.render import STOCK_HELP, render_stock
from app.sectors import SectorsError

FIXTURES = Path(__file__).parent / "fixtures"
REPORT_RAW = json.loads((FIXTURES / "company_report_bbca.json").read_text())
DAILY_RAW = json.loads((FIXTURES / "daily_bbca.json").read_text())
SEARCH_RAW = json.loads((FIXTURES / "companies_search_telkom.json").read_text())
T0 = datetime(2026, 10, 8, 7, 5, tzinfo=UTC)


def report_raw(sections=stocks.BASE_SECTIONS) -> dict:
    raw = copy.deepcopy(REPORT_RAW)
    return {"symbol": raw["symbol"], "company_name": raw["company_name"], **{s: raw[s] for s in sections}}


def row(date, close, volume=1000, market_cap=None):
    return {"symbol": "BBCA.JK", "date": date, "close": close, "volume": volume, "market_cap": market_cap}


def card(report=None, price=None, valuation=None) -> stocks.StockCard:
    rep = stocks.parse_report("BBCA", report if report is not None else report_raw())
    prc = stocks.compute_price(price if price is not None else DAILY_RAW)
    return stocks.StockCard(
        "BBCA",
        price=Fetched(prc, T0),
        report=Fetched(rep, T0),
        valuation=Fetched(valuation, T0) if valuation else None,
    )


def text_of(c: stocks.StockCard, view="ov") -> str:
    return "\n".join(render_stock(c, view))


def assert_safe(case, chunks: list[str]) -> None:
    for text in chunks:
        for tag in ("b", "i", "code"):
            case.assertEqual(text.count(f"<{tag}>"), text.count(f"</{tag}>"), tag)
        case.assertLessEqual(len(text), MessageLimit.MAX_TEXT_LENGTH)


# ----------------------------------------------------------------------------- input


class InputTests(unittest.TestCase):
    def test_ticker_normalisation(self):
        for raw in ("BBCA", "bbca", "BBCA.JK", "bbca.jk", " Bbca.Jk "):
            self.assertEqual(stocks.normalize_ticker(raw), "BBCA", raw)
        for raw in ("BBC", "BBCAX", "BB1A", "BBCA.SI", ""):
            self.assertIsNone(stocks.normalize_ticker(raw), raw)

    def test_parse_args(self):
        P = stocks.Parsed
        self.assertEqual(stocks.parse_args([]), P("help"))
        self.assertEqual(stocks.parse_args(["bbca.jk"]), P("ticker", "BBCA"))
        self.assertEqual(stocks.parse_args(["BBCA", "bbri"]), P("multi", tickers=("BBCA", "BBRI")))
        self.assertEqual(stocks.parse_args(["BBCA,BBRI"]), P("multi", tickers=("BBCA", "BBRI")))
        self.assertEqual(stocks.parse_args(["BBCA", "hari", "ini"]), P("invalid"))
        self.assertEqual(stocks.parse_args(["Telkom"]), P("search", "telkom"))
        self.assertEqual(stocks.parse_args(["bc"]), P("invalid"))
        self.assertEqual(stocks.parse_args(["x'or'1"]), P("invalid"))  # never reaches `where`

    def test_search_rejects_unsafe_keywords(self):
        with self.assertRaises(ValueError):
            asyncio.run(stocks.search("a' or '1'='1"))


# ----------------------------------------------------------------------------- prices


class PriceTests(unittest.TestCase):
    def test_fixture(self):
        p = stocks.compute_price(DAILY_RAW)
        self.assertEqual((p.last.date, p.last.close, p.prev.date, p.prev.close), ("2026-10-07", 6050, "2026-10-06", 6100))
        self.assertEqual(p.change, -50)
        self.assertAlmostEqual(p.change_pct, -50 / 6100)

    def test_up_down_flat(self):
        up = stocks.compute_price([row("2026-10-01", 100), row("2026-10-02", 110)])
        self.assertAlmostEqual(up.change_pct, 0.10)
        down = stocks.compute_price([row("2026-10-01", 100), row("2026-10-02", 90)])
        self.assertAlmostEqual(down.change_pct, -0.10)
        flat = stocks.compute_price([row("2026-10-01", 100), row("2026-10-02", 100)])
        self.assertEqual((flat.change, flat.change_pct), (0, 0))
        text = text_of(card(price=[row("2026-10-01", 100), row("2026-10-02", 100)]))
        self.assertIn("Rp 0 (0,00%)", text)
        self.assertIn("+Rp 10 (+10,00%)", text_of(card(price=[row("2026-10-01", 100), row("2026-10-02", 110)])))

    def test_unsorted_and_duplicate_dates(self):
        p = stocks.compute_price(
            [row("2026-10-07", 120), row("2026-10-01", 100), row("2026-10-06", 999), row("2026-10-06", 110)]
        )
        self.assertEqual((p.prev.date, p.prev.close, p.last.date), ("2026-10-06", 110, "2026-10-07"))
        self.assertEqual([r.date for r in p.rows], ["2026-10-01", "2026-10-06", "2026-10-07"])

    def test_zero_or_missing_close_is_not_an_observation(self):
        p = stocks.compute_price([row("2026-10-01", 100), row("2026-10-02", 0), row("2026-10-03", None), row("2026-10-06", 105)])
        self.assertEqual((p.prev.date, p.change), ("2026-10-01", 5))

    def test_single_observation_or_zero_reference(self):
        for rows in ([row("2026-10-07", 6050)], [row("2026-10-06", 0), row("2026-10-07", 6050)]):
            p = stocks.compute_price(rows)
            self.assertIsNone(p.prev)
            self.assertIsNone(p.change_pct)
            self.assertIn("tidak dihitung", text_of(card(price=rows)))

    def test_no_valid_rows(self):
        for raw in ([], [row("2026-10-07", 0)], {"detail": "x"}, None, [row("bad-date", 10)]):
            self.assertIsNone(stocks.compute_price(raw))


# ----------------------------------------------------------------------------- report


class ReportTests(unittest.TestCase):
    def test_fixture_fields(self):
        r = stocks.parse_report("BBCA", report_raw())
        self.assertEqual(r.name, "PT Bank Central Asia Tbk.")
        self.assertEqual((r.sector, r.sub_sector), ("Financials", "Banks"))
        self.assertAlmostEqual(r.pe_ttm, 12.8098258047661)
        self.assertAlmostEqual(r.pb_mrq, 2.75279097233183)
        self.assertEqual(r.latest_fin.year, 2025)
        self.assertEqual(r.eps_year, 2025)
        self.assertEqual(r.missing, [])
        self.assertNotIn("BBCA", [p.symbol for p in r.peers])
        caps = [p.market_cap for p in r.peers]
        self.assertEqual(caps, sorted(caps, reverse=True))
        self.assertLessEqual(len(r.peers), 5)

    def test_annual_growth_is_computed_from_consecutive_years(self):
        r = stocks.parse_report("BBCA", report_raw())
        hist = {f["year"]: f for f in REPORT_RAW["financials"]["historical_financials"]}
        self.assertAlmostEqual(
            r.latest_fin.revenue_growth, (hist[2025]["revenue"] - hist[2024]["revenue"]) / hist[2024]["revenue"]
        )

    def test_growth_needs_a_positive_base_and_no_gap(self):
        raw = report_raw()
        fin = raw["financials"]["historical_financials"]
        next(f for f in fin if f["year"] == 2024)["earnings"] = -5e12
        raw["financials"]["historical_financials"] = [f for f in fin if f["year"] != 2022]
        ratios = raw["financials"]["historical_financial_ratio"]
        raw["financials"]["historical_financial_ratio"] = [x for x in ratios if x["year"] != "2022"]
        r = stocks.parse_report("BBCA", raw)
        years = {f.year: f for f in r.fin_years}
        self.assertIsNone(years[2025].earnings_growth)  # previous year was a loss
        self.assertNotIn(2022, years)
        self.assertIsNone(years[2023].revenue_growth)  # 2022 is missing: no 2021->2023 jump

    def test_peers_as_object_form(self):
        raw = report_raw()
        raw["peers"] = raw["peers"][0]  # the docs show an object, the API sends a list
        self.assertAlmostEqual(stocks.parse_report("BBCA", raw).pe_ttm, 12.8098258047661)

    def test_empty_report(self):
        for raw in ({}, None, [], {"symbol": "BBCA.JK", "overview": {}}):
            with self.assertRaises(stocks.EmptyData):
                stocks.parse_report("BBCA", raw)

    def test_partial_report_hides_empty_tabs(self):
        raw = report_raw(("overview",))
        c = card(report={**raw, "dividend": None, "financials": {}, "peers": []})
        self.assertEqual(c.report.value.missing, ["dividend", "financials", "peers"])
        self.assertEqual(c.tabs(), ["ov", "pr", "va"])
        text = text_of(c)
        self.assertIn("P/E TTM: Tidak tersedia", text)
        self.assertIn("Pendapatan: Tidak tersedia", text)
        self.assertIn("Data tidak lengkap", text)


# ----------------------------------------------------------------------------- rendering


class RenderTests(unittest.TestCase):
    def test_card_content_and_periods(self):
        text = text_of(card())
        for needle in (
            "Penutupan 7 Okt 2026: <b>Rp 6.050</b>",
            "Perubahan vs 6 Okt 2026 (Rp 6.100): −Rp 50 (−0,82%)",
            "162.690.800 lembar",
            "Market cap 7 Okt 2026: Rp 738,36 T",
            "P/E TTM: 12,81x",
            "P/B MRQ: 2,75x",
            "Dividend yield TTM: 6,30%",
            "Pendapatan FY2025: Rp 112,01 T",
            "ROE FY2025: 20,43%",
            "YoY (TTM)",
            "bukan total return",
            "Waktu pengambilan harga",
            "bukan rekomendasi",
        ):
            self.assertIn(needle, text)

    def test_every_view_is_safe(self):
        val = stocks.parse_valuation(REPORT_RAW)
        c = card(valuation=val)
        for view in c.tabs():
            assert_safe(self, render_stock(c, view))

    def test_html_in_names_is_escaped(self):
        raw = report_raw()
        raw["company_name"] = "A&B <Tbk>"
        raw["overview"]["address"] = "Jl. <script>"
        c = card(report=raw)
        self.assertIn("A&amp;B &lt;Tbk&gt;", text_of(c))
        self.assertIn("Jl. &lt;script&gt;", text_of(c, "pr"))
        assert_safe(self, render_stock(c, "pr"))

    def test_negative_pe_zero_and_missing_values(self):
        raw = report_raw()
        raw["peers"][0]["peers_data"]["companies"][0]["pe_ttm"] = -26.2
        raw["dividend"]["yield_ttm"] = 0
        raw["peers"][0]["peers_data"]["companies"][0]["pb_mrq"] = None
        text = text_of(card(report=raw))
        self.assertIn("P/E TTM: −26,20x (negatif karena laba TTM rugi; bukan tanda murah)", text)
        self.assertIn("Dividend yield TTM: 0,00%", text)  # zero is data, not "missing"
        self.assertIn("P/B MRQ: Tidak tersedia", text)

    def test_ratio_date_only_when_unambiguous(self):
        # In the fixture, 2 Oct and 6 Oct both closed at 6100 -> same market cap -> no date.
        self.assertIn("Tanggal acuan P/E dan P/B tidak dicantumkan API", text_of(card()))
        mcap = REPORT_RAW["peers"][0]["peers_data"]["companies"][0]["market_cap"]
        rows = [row("2026-10-06", 6100, market_cap=mcap), row("2026-10-07", 6050, market_cap=1)]
        self.assertIn("sama dengan penutupan 6 Okt 2026", text_of(card(price=rows)))

    def test_price_missing_falls_back_to_report_close(self):
        c = card()
        c.price, c.price_error = None, "Batas permintaan Sectors API tercapai (429)."
        text = text_of(c)
        self.assertIn("Penutupan 7 Okt 2026: <b>Rp 6.050</b>", text)
        self.assertIn("Perubahan: tidak dihitung (data harian tidak tersedia)", text)
        self.assertIn("(429)", text)


# ----------------------------------------------------------------------------- handlers


class FakeClient:
    def __init__(self):
        self.calls: list[tuple] = []
        self.fail: dict[str, Exception] = {}
        self.unknown = {"ZZZZ", "TELK"}

    async def get(self, name, *, path_params=None, params=None):
        self.calls.append((name, dict(path_params or {}), dict(params or {})))
        await asyncio.sleep(0)
        if name in self.fail:
            raise self.fail[name]
        symbol = (path_params or {}).get("symbol")
        if symbol in self.unknown:
            raise SectorsError(404, "Given stock symbol does not exist.")
        if name == "daily":
            return copy.deepcopy(DAILY_RAW)
        if name == "company_report":
            return report_raw(tuple(params["sections"].split(",")))
        if name == "companies":
            return copy.deepcopy(SEARCH_RAW) if "telk" in params["where"] else {"results": []}
        raise AssertionError(name)

    def count(self, name, **match):
        return len([c for c in self.calls if c[0] == name and all(c[2].get(k) == v for k, v in match.items())])


def status_message():
    msg = AsyncMock(spec=Message)
    msg.chat_id = 7
    return msg


def command(args, status=None):
    update = MagicMock()
    update.message.reply_text = AsyncMock(return_value=status or status_message())
    context = MagicMock()
    context.args = args
    context.bot.send_message = AsyncMock()
    return update, context


def button(data, message=None):
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.message = message or status_message()
    update.callback_query.answer = AsyncMock()
    context = MagicMock()
    context.bot.send_message = AsyncMock()
    return update, context


def buttons(markup) -> list[str]:
    return [b.callback_data for r in markup.inline_keyboard for b in r] if markup else []


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        stocks.reset_caches()
        self.client = FakeClient()
        for p in (
            patch.object(stocks, "get_client", lambda: self.client),
            patch.object(settings, "groq_api_key", ""),  # /stock must not need the LLM
        ):
            p.start()
            self.addCleanup(p.stop)

    async def run_stock(self, args):
        status = status_message()
        update, context = command(args, status)
        await handlers.stock(update, context)
        return update, status

    async def test_help(self):
        update, _ = await self.run_stock([])
        self.assertEqual(update.message.reply_text.call_args.args[0], STOCK_HELP)
        self.assertIn("Gunakan /stock &lt;kode saham&gt;.\nContoh: /stock BBCA.", STOCK_HELP)
        self.assertEqual(self.client.calls, [])

    async def test_card_from_lowercase_with_suffix(self):
        update, status = await self.run_stock(["bbca.jk"])
        self.assertEqual(update.message.reply_text.call_args.args[0], "⏳ Mengambil data BBCA…")
        text = status.edit_text.call_args.args[0]
        self.assertIn("<b>BBCA</b> · PT Bank Central Asia Tbk. — Ringkasan", text)
        data = buttons(status.edit_text.call_args.kwargs["reply_markup"])
        self.assertEqual(data, [stocks.cb_view(t, "BBCA") for t in ("ov", "pr", "fi", "va", "dv")])
        self.assertEqual(self.client.count("daily"), 1)
        self.assertEqual(self.client.count("company_report", sections="dividend,financials,overview,peers"), 1)
        self.assertEqual(self.client.count("company_report", sections="valuation"), 0)  # lazy

    async def test_not_listed_offers_candidates(self):
        _, status = await self.run_stock(["TELK"])
        text = status.edit_text.call_args.args[0]
        self.assertIn("Kode saham <b>TELK</b> tidak ditemukan di IDX", text)
        self.assertEqual(buttons(status.edit_text.call_args.kwargs["reply_markup"]), ["stk:s:TLKM"])

    async def test_not_listed_without_candidates(self):
        _, status = await self.run_stock(["ZZZZ"])
        self.assertIn("Tidak ada perusahaan yang cocok", status.edit_text.call_args.args[0])
        self.assertIsNone(status.edit_text.call_args.kwargs["reply_markup"])

    async def test_multiple_tickers_point_to_compare(self):
        update, _ = await self.run_stock(["BBCA", "BBRI"])
        self.assertIn("/compare BBCA BBRI", update.message.reply_text.call_args.args[0])
        self.assertEqual(self.client.calls, [])

    async def test_extra_args_get_usage(self):
        update, _ = await self.run_stock(["BBCA", "hari", "ini"])
        self.assertEqual(update.message.reply_text.call_args.args[0], STOCK_HELP)

    async def test_name_search_asks_to_pick(self):
        update, _ = await self.run_stock(["telkom"])
        call = update.message.reply_text.call_args
        self.assertIn("bot tidak memilih otomatis", call.args[0])
        self.assertEqual(buttons(call.kwargs["reply_markup"]), ["stk:s:TLKM"])
        self.assertEqual(self.client.count("company_report"), 0)

    async def test_report_fails_price_shown(self):
        self.client.fail["company_report"] = SectorsError(503, "down")
        _, status = await self.run_stock(["BBCA"])
        text = status.edit_text.call_args.args[0]
        self.assertIn("Penutupan 7 Okt 2026", text)
        self.assertIn("Laporan perusahaan belum tersedia: Server Sectors API sedang bermasalah (503)", text)
        self.assertEqual(buttons(status.edit_text.call_args.kwargs["reply_markup"]), ["stk:v:ov:BBCA", "stk:s:BBCA"])

    async def test_price_fails_report_shown(self):
        self.client.fail["daily"] = SectorsError(None, "network", timed_out=True)
        _, status = await self.run_stock(["BBCA"])
        text = status.edit_text.call_args.args[0]
        self.assertIn("P/E TTM: 12,81x", text)
        self.assertIn("Data harga harian belum tersedia", text)
        self.assertIn("timeout", text)

    async def test_both_fail(self):
        for exc, needle in ((SectorsError(401, "x"), "ditolak (401)"), (SectorsError(429, "x"), "(429)")):
            stocks.reset_caches()
            self.client.fail = {"daily": exc, "company_report": exc}
            _, status = await self.run_stock(["BBCA"])
            text = status.edit_text.call_args.args[0]
            self.assertIn("Gagal memuat data <b>BBCA</b>", text)
            self.assertIn(needle, text)
            self.assertNotIn(f"[{exc.status}]", text)  # the raw exception text never reaches users

    async def test_cache_hit_expiry_and_stale(self):
        clock = [1000.0]
        daily = TTLCache(900, 5000, clock=lambda: clock[0])
        report = TTLCache(21600, 50000, clock=lambda: clock[0])
        with patch.object(stocks, "_daily_cache", daily), patch.object(stocks, "_report_cache", report):
            await self.run_stock(["BBCA"])
            await self.run_stock(["BBCA"])
            self.assertEqual((self.client.count("daily"), self.client.count("company_report")), (1, 1))
            clock[0] += 1000  # daily expired, report still fresh
            await self.run_stock(["BBCA"])
            self.assertEqual((self.client.count("daily"), self.client.count("company_report")), (2, 1))
            clock[0] += 1000
            self.client.fail["daily"] = SectorsError(503, "down")
            _, status = await self.run_stock(["BBCA"])
        text = status.edit_text.call_args.args[0]
        self.assertIn("Sectors API gagal saat memperbarui harga", text)
        self.assertIn("Penutupan 7 Okt 2026", text)

    async def test_concurrent_requests_share_fetches(self):
        await asyncio.gather(stocks.load_card("BBCA"), stocks.load_card("BBCA"), stocks.load_card("BBCA"))
        self.assertEqual((self.client.count("daily"), self.client.count("company_report")), (1, 1))

    async def test_tab_switch_uses_cache_and_valuation_is_lazy(self):
        await self.run_stock(["BBCA"])
        update, context = button("stk:v:pr:BBCA")
        await handlers.stock_button(update, context)
        self.assertIn("Profil perusahaan", update.callback_query.message.edit_text.call_args.args[0])
        self.assertEqual(len(self.client.calls), 2)

        for _ in range(2):
            update, context = button("stk:v:va:BBCA")
            await handlers.stock_button(update, context)
            self.assertIn("P/E forward", update.callback_query.message.edit_text.call_args.args[0])
        self.assertEqual(self.client.count("company_report", sections="valuation"), 1)

    async def test_valuation_failure_keeps_tab(self):
        await self.run_stock(["BBCA"])
        self.client.fail["company_report"] = SectorsError(429, "x")
        update, context = button("stk:v:va:BBCA")
        await handlers.stock_button(update, context)
        text = update.callback_query.message.edit_text.call_args.args[0]
        self.assertIn("P/E TTM: 12,81x", text)
        self.assertIn("Rincian valuasi historis belum tersedia", text)

    async def test_expired_invalid_and_unavailable_buttons(self):
        update, context = button("stk:v:fi:BBCA")
        await handlers.stock_button(update, context)
        self.assertIn("kedaluwarsa", update.callback_query.answer.call_args.args[0])

        update, context = button("stk:v:fi:bbca")
        await handlers.stock_button(update, context)
        self.assertIn("tidak dikenali", update.callback_query.answer.call_args.args[0])

        self.client.fail["company_report"] = SectorsError(503, "x")
        await self.run_stock(["BBCA"])
        update, context = button("stk:v:dv:BBCA")
        await handlers.stock_button(update, context)
        self.assertIn("tidak tersedia", update.callback_query.answer.call_args.args[0])

    async def test_select_button_loads_card(self):
        update, context = button("stk:s:BBCA")
        await handlers.stock_button(update, context)
        update.callback_query.answer.assert_awaited_once_with()
        texts = [c.args[0] for c in update.callback_query.message.edit_text.call_args_list]
        self.assertEqual(texts[0], "⏳ Mengambil data BBCA…")
        self.assertIn("— Ringkasan", texts[-1])

    def test_callback_data(self):
        for t in stocks.VIEWS:
            data = stocks.cb_view(t, "BBCA")
            self.assertLessEqual(len(data.encode()), 64)
            self.assertEqual(stocks.parse_callback(data), ("view", (t, "BBCA")))
        for bad in (None, "", "stk:v:xx:BBCA", "stk:s:BBC", "stk:s:BBCA.JK", "sec:s:banks", "stk:v:ov:BBCA:1"):
            self.assertIsNone(stocks.parse_callback(bad), bad)

    def test_registered(self):
        from app.main import COMMANDS

        self.assertIn("stock", [c.command for c in COMMANDS])
        self.assertIn("/stock", handlers.HELP_TEXT)
        app = MagicMock()
        handlers.register(app)
        registered = [c.args[0] for c in app.add_handler.call_args_list]
        self.assertTrue(any(getattr(h, "commands", None) == frozenset({"stock"}) for h in registered))
        self.assertTrue(any(getattr(getattr(h, "pattern", None), "pattern", "") == "^stk:" for h in registered))


class OldCommandsTests(unittest.IsolatedAsyncioTestCase):
    async def test_ihsg_still_works(self):
        client = MagicMock()
        client.get = AsyncMock(return_value=[{"date": "2026-10-06", "price": 100}, {"date": "2026-10-07", "price": 101}])
        update, context = command([])
        with patch.object(handlers, "get_client", lambda: client):
            await handlers.ihsg(update, context)
        self.assertIn("IHSG: 101.00", update.message.reply_text.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
