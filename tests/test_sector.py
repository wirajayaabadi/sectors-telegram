"""Tests for /sector. No network, no credits: the Sectors client is replaced by a fake that
serves the fixtures in tests/fixtures/ (real API responses captured on 8 Oct 2026).

    uv run python -m unittest discover -s tests -v
"""

import asyncio
import copy
import json
import re
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Message
from telegram.constants import MessageLimit

from app import handlers, render, subsector
from app.config import settings
from app.render import SECTOR_LOADING, _pack, render_subsector
from app.sectors import SectorsError
from app.subsector import EmptyData, Fetched, TTLCache, describe_error, match, parse_report

FIXTURES = Path(__file__).parent / "fixtures"
SUBSECTORS_RAW = json.loads((FIXTURES / "subsectors.json").read_text())
BANKS_RAW = json.loads((FIXTURES / "subsector_report_banks.json").read_text())
SUBS = subsector.parse_subsectors(SUBSECTORS_RAW)


def banks() -> dict:
    return copy.deepcopy(BANKS_RAW)


def fetched(report, **kw) -> Fetched:
    from datetime import UTC, datetime

    return Fetched(report, datetime(2026, 10, 8, 7, 5, tzinfo=UTC), **kw)


def assert_balanced(case: unittest.TestCase, text: str) -> None:
    for tag in ("b", "i", "code"):
        case.assertEqual(text.count(f"<{tag}>"), text.count(f"</{tag}>"), f"<{tag}> in {text!r}")
    case.assertLessEqual(len(text), MessageLimit.MAX_TEXT_LENGTH)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# ----------------------------------------------------------------------------- matching


class MatchTests(unittest.TestCase):
    def test_exact_slug_with_case_spaces_and_separators(self):
        self.assertEqual(match("banks", SUBS).slug, "banks")
        self.assertEqual(match("  BANKS ", SUBS).slug, "banks")
        self.assertEqual(match("Food & Beverage", SUBS).slug, "food-beverage")
        self.assertEqual(match("food_beverage", SUBS).slug, "food-beverage")
        self.assertEqual(match("oil gas coal", SUBS).slug, "oil-gas-coal")

    def test_indonesian_label_and_aliases(self):
        self.assertEqual(match("Telekomunikasi", SUBS).slug, "telecommunication")
        self.assertEqual(match("perbankan", SUBS).slug, "banks")
        self.assertEqual(match("Bank", SUBS).slug, "banks")
        self.assertEqual(match("migas", SUBS).slug, "oil-gas-coal")

    def test_every_alias_and_label_points_at_a_live_slug(self):
        live = {s.slug for s in SUBS}
        self.assertLessEqual(set(subsector.ALIASES.values()), live)
        self.assertEqual(set(subsector.LABELS), live)

    def test_alias_is_ignored_when_its_target_is_not_in_the_live_list(self):
        without_banks = [s for s in SUBS if s.slug != "banks"]
        self.assertIsNone(match("perbankan", without_banks).slug)

    def test_typo_is_offered_never_auto_picked(self):
        m = match("bnks", SUBS)
        self.assertIsNone(m.slug)
        self.assertIn("banks", m.candidates)

    def test_ambiguous_input_lists_candidates(self):
        m = match("transport", SUBS)
        self.assertIsNone(m.slug)
        self.assertIn("transportation", m.candidates)
        self.assertIn("transportation-infrastructure", m.candidates)

    def test_unknown_and_empty(self):
        self.assertEqual(match("xyzzy qwerty", SUBS), subsector.Match(None, ()))
        self.assertEqual(match("   ", SUBS), subsector.Match(None, ()))

    def test_subsector_list_validation(self):
        raw = SUBSECTORS_RAW + [
            {"sector": "x", "subsector": "Bad Slug"},
            {"sector": "x", "subsector": "../etc"},
            {"sector": "x"},
            "junk",
        ]
        self.assertEqual(len(subsector.parse_subsectors(raw)), 33)
        with self.assertRaises(EmptyData):
            subsector.parse_subsectors([])
        with self.assertRaises(EmptyData):
            subsector.parse_subsectors({"detail": "x"})


# ----------------------------------------------------------------------------- parsing + maths


class ParseTests(unittest.TestCase):
    def test_full_report(self):
        r = parse_report("banks", banks())
        self.assertEqual((r.name, r.sector, r.total_companies), ("Banks", "Financials", 48))
        self.assertEqual(r.missing, [])
        self.assertEqual([c.symbol for c in r.top_mcap], ["BBCA", "BBRI", "BMRI", "BBNI", "BNLI"])
        values = [c.value for c in r.top_mcap]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertEqual(r.movers[0].symbol, "BSWD")  # highest 1-year change
        self.assertEqual([g.year for g in r.growth], list(range(2018, 2026)))

    def test_derived_numbers_are_computed_in_python(self):
        r = parse_report("banks", banks())
        top = BANKS_RAW["companies"]["top_companies"]
        total = BANKS_RAW["market_cap"]["total_market_cap"]
        expected = sum(v["market_cap"] for v in top["top_mcap"].values()) / total
        self.assertAlmostEqual(r.top_mcap_share, expected)
        self.assertAlmostEqual(r.top_mcap[0].share, top["top_mcap"]["BBCA.JK"]["market_cap"] / total)
        bbca = next(c for c in r.top_profit if c.symbol == "BBCA")
        self.assertAlmostEqual(
            bbca.margin,
            top["top_profit"]["BBCA.JK"]["profit_ttm"] / top["top_revenue"]["BBCA.JK"]["revenue_ttm"],
        )

    def test_missing_values_stay_missing(self):
        r = parse_report("banks", banks())
        self.assertIsNone(r.growth[0].revenue)  # 2018 is null in the API
        self.assertEqual(r.latest_growth.year, 2025)
        for bad in (None, "abc", float("nan"), True, {}):
            self.assertIsNone(subsector.num(bad))

    def test_zero_is_a_real_value(self):
        raw = banks()
        raw["market_cap"]["mcap_summary"]["mcap_change"]["1w"] = 0
        raw["growth"]["weighted_avg_growth_data"]["2025"]["avg_annual_revenue_growth"] = 0.0
        r = parse_report("banks", raw)
        self.assertEqual(r.mcap_change["1w"], 0.0)
        text = "\n".join(render_subsector(fetched(r), "ov"))
        self.assertIn("1 minggu 0,00%", text)
        self.assertIn("pendapatan 0,00%", text)

    def test_partial_report_lists_what_is_missing(self):
        raw = {"sector": "Financials", "sub_sector": "Banks", "statistics": banks()["statistics"]}
        r = parse_report("banks", raw)
        self.assertEqual(r.missing, ["market_cap", "growth", "valuation", "companies"])
        self.assertIsNone(r.top_mcap_share)
        for view in subsector.VIEWS:
            text = "\n".join(render_subsector(fetched(r), view))
            self.assertIn("Data tidak lengkap", text)
            assert_balanced(self, text)

    def test_empty_reports_raise(self):
        for raw in ({}, [], None, {"sector": "x", "statistics": {}}):
            with self.assertRaises(EmptyData):
                parse_report("banks", raw)

    def test_shares_need_a_positive_total_and_complete_values(self):
        raw = banks()
        raw["market_cap"]["total_market_cap"] = 0
        self.assertIsNone(parse_report("banks", raw).top_mcap_share)
        raw = banks()
        raw["companies"]["top_companies"]["top_mcap"]["BBCA.JK"]["market_cap"] = None
        r = parse_report("banks", raw)
        self.assertIsNone(r.top_mcap_share)  # a sum with a hole would understate the share
        self.assertEqual(r.top_mcap[-1].symbol, "BBCA")  # missing values sort last


# ----------------------------------------------------------------------------- rendering


class RenderTests(unittest.TestCase):
    def test_every_view_renders_within_limits(self):
        r = parse_report("banks", banks())
        for view in subsector.VIEWS:
            chunks = render_subsector(fetched(r), view)
            self.assertEqual(len(chunks), 1)
            for c in chunks:
                assert_balanced(self, c)
                self.assertIn("Tanggal data tidak tersedia", c)
                self.assertIn("Waktu pengambilan: 08 Okt 2026 14:05 WIB", c)
                self.assertIn("bukan rekomendasi", c)

    def test_html_in_api_strings_is_escaped(self):
        raw = banks()
        raw["sub_sector"] = "<script>"
        raw["companies"]["top_companies"]["top_mcap"]["BBCA.JK"]["name"] = "A <b>&</b> Co"
        r = parse_report("banks", raw)
        text = "\n".join(render_subsector(fetched(r), "co"))
        self.assertIn("A &lt;b&gt;&amp;&lt;/b&gt; Co", text)
        self.assertNotIn("<script>", "\n".join(render_subsector(fetched(r), "ov")))
        assert_balanced(self, text)

    def test_number_formats(self):
        self.assertEqual(render._idr(738355911975000), "Rp 738,36 T")
        self.assertEqual(render._idr(-2.5e9), "−Rp 2,50 M")
        self.assertEqual(render._idr(3600), "Rp 3.600")
        self.assertEqual(render._pct(-0.2341076529), "−23,41%")
        self.assertEqual(render._pct(None), "–")
        self.assertEqual(render._ratio(-74.27412), "−74,27x")

    def test_stale_and_aged_labels(self):
        r = parse_report("banks", banks())
        text = "\n".join(render_subsector(fetched(r, from_cache=True, stale_reason="api_failed")))
        self.assertIn("(dari cache)", text)
        self.assertIn("Sectors API gagal saat diperbarui", text)
        text = "\n".join(render_subsector(fetched(r, from_cache=True, stale_reason="aged")))
        self.assertIn("/sector banks untuk memperbarui", text)

    def test_long_output_splits_without_breaking_tags(self):
        lines = [f"• <b>Item {i}</b> <i>{'x' * 60}</i> <code>AB&amp;C</code>" for i in range(200)]
        chunks = _pack(["<b>Head</b>", "\n".join(lines), "<i>end</i>"])
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            assert_balanced(self, c)
        self.assertEqual(sum(c.count("<b>Item") for c in chunks), 200)  # nothing dropped

    def test_single_huge_line_is_shortened_safely(self):
        line = "<b>" + "a&amp;" * 2000 + "</b>"
        (chunk,) = _pack([line])
        assert_balanced(self, chunk)
        self.assertNotRegex(chunk, r"&[a-z]*…$")  # no half entity before the ellipsis


# ----------------------------------------------------------------------------- cache


class CacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.cache = TTLCache(ttl=100, stale_max=1000, clock=self.clock)
        self.calls = 0

    async def fetch_ok(self):
        self.calls += 1
        return f"v{self.calls}"

    async def test_hit_then_expiry(self):
        a = await self.cache.get(("k",), self.fetch_ok)
        b = await self.cache.get(("k",), self.fetch_ok)
        self.assertEqual((a.value, a.from_cache, b.value, b.from_cache), ("v1", False, "v1", True))
        self.clock.now += 101
        c = await self.cache.get(("k",), self.fetch_ok)
        self.assertEqual((c.value, c.from_cache, self.calls), ("v2", False, 2))

    async def test_keys_are_separate(self):
        await self.cache.get(("report", "banks", ("a",)), self.fetch_ok)
        await self.cache.get(("report", "banks", ("a", "b")), self.fetch_ok)
        self.assertEqual(self.calls, 2)

    async def test_api_failure_serves_labelled_stale_copy(self):
        await self.cache.get(("k",), self.fetch_ok)
        self.clock.now += 500

        async def fail():
            raise SectorsError(503, "down")

        got = await self.cache.get(("k",), fail)
        self.assertEqual((got.value, got.stale_reason), ("v1", "api_failed"))

    async def test_no_stale_copy_beyond_stale_max_or_for_404(self):
        await self.cache.get(("k",), self.fetch_ok)
        self.clock.now += 500

        async def not_found():
            raise SectorsError(404, "no")

        with self.assertRaises(SectorsError):
            await self.cache.get(("k",), not_found)
        self.clock.now += 1000

        async def down():
            raise SectorsError(503, "down")

        with self.assertRaises(SectorsError):
            await self.cache.get(("k",), down)

    async def test_empty_data_is_not_cached(self):
        async def empty():
            raise EmptyData("x")

        with self.assertRaises(EmptyData):
            await self.cache.get(("k",), empty)
        self.assertIsNone(self.cache.peek(("k",)))

    async def test_concurrent_requests_share_one_fetch(self):
        async def slow():
            self.calls += 1
            await asyncio.sleep(0.01)
            return "v"

        results = await asyncio.gather(*(self.cache.get(("k",), slow) for _ in range(10)))
        self.assertEqual(self.calls, 1)
        self.assertTrue(all(r.value == "v" for r in results))

    async def test_peek(self):
        self.assertIsNone(self.cache.peek(("k",)))
        await self.cache.get(("k",), self.fetch_ok)
        self.assertIsNone(self.cache.peek(("k",)).stale_reason)
        self.clock.now += 150
        self.assertEqual(self.cache.peek(("k",)).stale_reason, "aged")
        self.clock.now += 1000
        self.assertIsNone(self.cache.peek(("k",)))
        self.assertEqual(self.calls, 1)


class ErrorTests(unittest.TestCase):
    def test_each_failure_kind_has_its_own_message(self):
        cases = {
            SectorsError(401, "x"): "ditolak (401)",
            SectorsError(403, "x"): "ditolak (403)",
            SectorsError(404, "x"): "(404)",
            SectorsError(429, "x"): "(429)",
            SectorsError(500, "x"): "(500)",
            SectorsError(503, "x"): "(503)",
            SectorsError(400, "x"): "(400)",
            SectorsError(None, "network error: ReadTimeout", timed_out=True): "timeout",
            SectorsError(None, "network error: ConnectError"): "jaringan",
            SectorsError(None, "SECTORS_API_KEY is empty: paste your key"): "SECTORS_API_KEY",
            EmptyData("x"): "tidak mengembalikan data",
        }
        for exc, needle in cases.items():
            self.assertIn(needle, describe_error(exc), repr(exc))


class CallbackDataTests(unittest.TestCase):
    def test_every_callback_fits_telegram_and_round_trips(self):
        for s in SUBS:
            for tab in subsector.VIEWS:
                data = subsector.cb_view(tab, s.slug)
                self.assertLessEqual(len(data.encode()), 64)
                self.assertEqual(subsector.parse_callback(data), ("view", (tab, s.slug)))
            self.assertEqual(subsector.parse_callback(subsector.cb_select(s.slug)), ("select", s.slug))
        self.assertEqual(subsector.parse_callback("sec:p:3"), ("page", 3))
        self.assertEqual(subsector.parse_callback(subsector.CB_NOOP), ("noop", None))

    def test_bad_payloads_are_rejected(self):
        for data in (None, "", "foo", "sec:v:xx:banks", "sec:s:../etc", "sec:s:Banks",
                     "sec:s:-banks", "sec:p:-1", "sec:s:" + "a" * 60, "sec:v:ov:banks:extra"):
            self.assertIsNone(subsector.parse_callback(data), data)


# ----------------------------------------------------------------------------- handlers


class FakeClient:
    """Stands in for SectorsClient. `fail` maps endpoint name -> exception to raise."""

    def __init__(self, fail=None, report=None):
        self.calls: list[tuple] = []
        self.fail = fail or {}
        self.report = report if report is not None else banks()

    async def get(self, name, *, path_params=None, params=None):
        self.calls.append((name, path_params, params))
        if name in self.fail:
            raise self.fail[name]
        return copy.deepcopy(SUBSECTORS_RAW) if name == "subsectors" else copy.deepcopy(self.report)


def status_message() -> AsyncMock:
    msg = AsyncMock(spec=Message)
    msg.chat_id = 42
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
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        subsector.reset_caches()
        self.client = FakeClient()
        patches = [
            patch.object(subsector, "get_client", lambda: self.client),
            patch.object(settings, "groq_api_key", ""),  # /sector must not need the LLM
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def report_calls(self):
        return [c for c in self.client.calls if c[0] == "subsector_report"]

    async def test_no_args_shows_paged_picker(self):
        update, context = command([])
        await handlers.sector(update, context)
        text = update.message.reply_text.call_args.args[0]
        markup = update.message.reply_text.call_args.kwargs["reply_markup"]
        self.assertIn("tingkat <b>subsektor</b>", text)
        self.assertIn("/sector banks", text)
        data = buttons(markup)
        self.assertEqual(len([d for d in data if d.startswith("sec:s:")]), 10)
        self.assertIn("sec:p:1", data)
        self.assertEqual(self.report_calls(), [])  # browsing the list spends no report credits

    async def test_alias_loads_report_into_status_message(self):
        status = status_message()
        update, context = command(["perbankan"], status)
        await handlers.sector(update, context)
        self.assertEqual(update.message.reply_text.call_args.args[0], SECTOR_LOADING)
        text = status.edit_text.call_args.args[0]
        self.assertIn("<b>Bank</b> — Ringkasan", text)
        self.assertIn("sec:v:va:banks", buttons(status.edit_text.call_args.kwargs["reply_markup"]))
        (call,) = self.report_calls()
        self.assertEqual(call[1], {"sub_sector": "banks"})
        self.assertEqual(call[2], {"sections": "statistics,market_cap,growth,valuation,companies"})

    async def test_typo_asks_for_confirmation(self):
        update, context = command(["bnks"])
        await handlers.sector(update, context)
        text = update.message.reply_text.call_args.args[0]
        self.assertIn("Maksud Anda", text)
        self.assertIn("sec:s:banks", buttons(update.message.reply_text.call_args.kwargs["reply_markup"]))
        self.assertEqual(self.report_calls(), [])

    async def test_unknown_input(self):
        update, context = command(["<xyzzy>"])
        await handlers.sector(update, context)
        text = update.message.reply_text.call_args.args[0]
        self.assertIn("tidak dikenali", text)
        self.assertIn("&lt;xyzzy&gt;", text)

    async def test_tab_switch_reuses_cache(self):
        await handlers.sector(*command(["banks"]))
        update, context = button("sec:v:va:banks")
        await handlers.sector_button(update, context)
        update.callback_query.answer.assert_awaited_once_with()
        text = update.callback_query.message.edit_text.call_args.args[0]
        self.assertIn("— Valuasi", text)
        self.assertIn("(dari cache)", text)
        self.assertEqual(len(self.report_calls()), 1)

    async def test_expired_button(self):
        update, context = button("sec:v:co:banks")
        await handlers.sector_button(update, context)
        kwargs = update.callback_query.answer.call_args.kwargs
        self.assertTrue(kwargs["show_alert"])
        self.assertIn("kedaluwarsa", update.callback_query.answer.call_args.args[0])
        self.assertEqual(self.client.calls, [])

    async def test_invalid_payload(self):
        update, context = button("sec:v:zz:../x")
        await handlers.sector_button(update, context)
        self.assertIn("tidak dikenali", update.callback_query.answer.call_args.args[0])

    async def test_last_page(self):
        update, context = button("sec:p:3")
        await handlers.sector_button(update, context)
        msg = update.callback_query.message
        self.assertIn("Halaman 4/4", msg.edit_text.call_args.args[0])
        data = buttons(msg.edit_text.call_args.kwargs["reply_markup"])
        self.assertEqual(len([d for d in data if d.startswith("sec:s:")]), 3)
        self.assertIn("sec:p:2", data)

    async def test_select_from_picker(self):
        update, context = button("sec:s:banks")
        await handlers.sector_button(update, context)
        msg = update.callback_query.message
        texts = [c.args[0] for c in msg.edit_text.call_args_list]
        self.assertEqual(texts[0], SECTOR_LOADING)
        self.assertIn("— Ringkasan", texts[-1])

    async def test_select_unknown_slug(self):
        update, context = button("sec:s:not-a-subsector")
        await handlers.sector_button(update, context)
        self.assertIn("tidak ada di daftar", update.callback_query.message.edit_text.call_args.args[0])
        self.assertEqual(self.report_calls(), [])

    async def test_api_failure_without_cache(self):
        self.client.fail["subsector_report"] = SectorsError(429, "RATE_LIMIT_EXCEEDED")
        status = status_message()
        await handlers.sector(*command(["banks"], status))
        text = status.edit_text.call_args.args[0]
        self.assertIn("Gagal memuat subsektor <b>Bank</b>", text)
        self.assertIn("(429)", text)
        self.assertIn("sec:s:banks", buttons(status.edit_text.call_args.kwargs["reply_markup"]))

    async def test_api_failure_with_stale_cache(self):
        clock = FakeClock()
        with patch.object(subsector, "_report_cache", TTLCache(100, 1000, clock=clock)):
            await handlers.sector(*command(["banks"]))
            clock.now += 500
            self.client.fail["subsector_report"] = SectorsError(None, "network", timed_out=True)
            status = status_message()
            await handlers.sector(*command(["banks"], status))
        text = status.edit_text.call_args.args[0]
        self.assertIn("Sectors API gagal saat diperbarui", text)
        self.assertIn("— Ringkasan", text)

    async def test_empty_report(self):
        self.client.report = {"sector": "x", "sub_sector": "y"}
        status = status_message()
        await handlers.sector(*command(["banks"], status))
        self.assertIn("tidak mengembalikan data", status.edit_text.call_args.args[0])

    async def test_list_failure(self):
        self.client.fail["subsectors"] = SectorsError(401, "invalid")
        update, context = command(["banks"])
        await handlers.sector(update, context)
        self.assertIn("ditolak (401)", update.message.reply_text.call_args.args[0])

    async def test_list_is_cached(self):
        await handlers.sector(*command([]))
        await handlers.sector(*command(["banks"]))
        await handlers.sector(*command(["banks"]))
        names = [c[0] for c in self.client.calls]
        self.assertEqual(names.count("subsectors"), 1)
        self.assertEqual(names.count("subsector_report"), 1)

    def test_registered_in_handlers_menu_and_help(self):
        from app.main import COMMANDS

        self.assertIn("sector", [c.command for c in COMMANDS])
        self.assertIn("/sector", handlers.HELP_TEXT)
        app = MagicMock()
        handlers.register(app)
        registered = [c.args[0] for c in app.add_handler.call_args_list]
        self.assertTrue(any(getattr(h, "commands", None) == frozenset({"sector"}) for h in registered))
        self.assertTrue(any(getattr(getattr(h, "pattern", None), "pattern", "") == "^sec:" for h in registered))


if __name__ == "__main__":
    unittest.main()
