"""Tests for /movers and /active. No network, no credits: a fake Sectors client serves the
fixtures in tests/fixtures/ (real API responses captured on 8 Oct 2026).

    uv run python -m unittest discover -s tests -t . -v
"""

import asyncio
import copy
import json
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Message
from telegram.constants import MessageLimit

from app import handlers
from app import rankings as rk
from app.cache import Fetched, TTLCache
from app.config import settings
from app.render import MOVERS_HELP, render_active, render_movers
from app.sectors import SectorsError

FIXTURES = Path(__file__).parent / "fixtures"
MOVERS_RAW = json.loads((FIXTURES / "top_changes_1d_7d.json").read_text())
TRADED_RAW = json.loads((FIXTURES / "most_traded_default.json").read_text())
SUNDAY_RAW = json.loads((FIXTURES / "most_traded_sunday.json").read_text())
TODAY = date(2026, 10, 8)
T0 = datetime(2026, 10, 8, 7, 5, tzinfo=UTC)


def mover(symbol, change, close=100, day="2026-10-07", name=None):
    return {"name": name or f"PT {symbol} Tbk", "symbol": f"{symbol}.JK", "price_change": change,
            "last_close_price": close, "latest_close_date": day}


def movers_raw(which, period, rows):
    return {rk.CLASSES[which]: {period: rows}}


def result(q, **parts) -> rk.MoversResult:
    return rk.MoversResult(q, parts={w: Fetched(v, T0) for w, v in parts.items()})


def assert_safe(case, chunks):
    for text in chunks:
        for tag in ("b", "i", "code"):
            case.assertEqual(text.count(f"<{tag}>"), text.count(f"</{tag}>"), tag)
        case.assertLessEqual(len(text), MessageLimit.MAX_TEXT_LENGTH)


# ----------------------------------------------------------------------------- parsing


class ParseTests(unittest.TestCase):
    def test_movers_syntax(self):
        Q = rk.MoversQuery
        self.assertEqual(rk.parse_movers([]), Q("both", "1d"))
        self.assertEqual(rk.parse_movers(["7d"]), Q("both", "7d"))
        self.assertEqual(rk.parse_movers(["gainers", "30d"]), Q("gainers", "30d"))
        self.assertEqual(rk.parse_movers(["LOSERS", "1D"]), Q("losers", "1d"))
        self.assertEqual(rk.parse_movers(["30d", "Gainer"]), Q("gainers", "30d"))
        for p in rk.PERIODS:
            self.assertEqual(rk.parse_movers([p]).period, p)

    def test_movers_invalid(self):
        for args in (["2d"], ["gainers", "losers"], ["7d", "30d"], ["gainers", "7d", "x"], ["naik"], ["1w"]):
            self.assertIsNone(rk.parse_movers(args), args)

    def test_active_syntax(self):
        Q, E = rk.ActiveQuery, rk.ActiveError
        cases = {
            (): Q(None, 5),
            ("10",): Q(None, 10),
            ("2026-10-07",): Q(date(2026, 10, 7), 5),
            ("2026-10-07", "10"): Q(date(2026, 10, 7), 10),
            ("2026-10-08",): Q(TODAY, 5),  # today (Jakarta) is not the future
            ("2026-10-09",): E("future"),
            ("2026-02-30",): E("bad_date"),
            ("07-10-2026",): E("bad_date"),
            ("2026/10/07",): E("bad_date"),
            ("0",): E("count"),
            ("11",): E("count"),
            ("20",): E("count"),  # the API caps n_stock at 10
            ("-1",): E("usage"),
            ("abc",): E("usage"),
            ("10", "2026-10-07"): E("usage"),
            ("2026-10-07", "10", "x"): E("usage"),
            ("2026-10-07", "2026-10-06"): E("usage"),
        }
        for args, expected in cases.items():
            self.assertEqual(rk.parse_active(list(args), TODAY), expected, args)


# ----------------------------------------------------------------------------- normalisation


class MoversDataTests(unittest.TestCase):
    def test_fixture_keeps_api_ranking(self):
        g = rk.parse_movers_rows(MOVERS_RAW, "gainers", "7d")
        l = rk.parse_movers_rows(MOVERS_RAW, "losers", "7d")
        self.assertEqual([m.symbol for m in g.rows], ["MBSS", "VICI", "BSWD", "GEMS", "JECX"])
        self.assertEqual([m.rank for m in l.rows], [1, 2, 3, 4, 5])
        self.assertFalse(g.resorted or l.resorted)
        self.assertEqual(l.rows[0].symbol, "FORU")
        self.assertAlmostEqual(l.rows[0].change, -0.357142857142857)

    def test_positive_negative_zero_and_missing(self):
        rows = [mover("AAAA", 0.1), mover("BBBB", 0), mover("CCCC", None), mover("DDDD", "x"), mover("EEEE", -0.02)]
        lst = rk.parse_movers_rows(movers_raw("gainers", "1d", rows), "gainers", "1d")
        self.assertEqual([m.symbol for m in lst.rows], ["AAAA", "BBBB", "EEEE"])
        self.assertEqual(lst.dropped, 2)
        text = "\n".join(render_movers(result(rk.MoversQuery("gainers", "1d"), gainers=lst)))
        self.assertIn("<b>+10,00%</b>", text)
        self.assertIn("<b>0,00%</b>", text)
        self.assertIn("<b>−2,00%</b>", text)
        self.assertIn("2 baris diabaikan", text)

    def test_resort_with_stable_tie_rule(self):
        rows = [mover("ZZZZ", 0.1), mover("AAAA", 0.3), mover("MMMM", 0.1)]
        lst = rk.parse_movers_rows(movers_raw("gainers", "7d", rows), "gainers", "7d")
        self.assertTrue(lst.resorted)
        self.assertEqual([m.symbol for m in lst.rows], ["AAAA", "MMMM", "ZZZZ"])
        losers = rk.parse_movers_rows(movers_raw("losers", "7d", [mover("BBBB", -0.1), mover("AAAA", -0.3)]), "losers", "7d")
        self.assertEqual([m.symbol for m in losers.rows], ["AAAA", "BBBB"])

    def test_fewer_than_five_are_not_padded(self):
        lst = rk.parse_movers_rows(movers_raw("losers", "30d", [mover(s, -0.1 * i) for i, s in enumerate(("AAAA", "BBBB", "CCCC"), 1)][::-1]), "losers", "30d")
        text = "\n".join(render_movers(result(rk.MoversQuery("losers", "30d"), losers=lst)))
        self.assertEqual(text.count("<code>"), 3)
        self.assertIn("API hanya mengembalikan 3 emiten", text)

    def test_missing_block(self):
        with self.assertRaises(rk.EmptyData):
            rk.parse_movers_rows({"top_gainers": {}}, "gainers", "7d")

    def test_reference_dates(self):
        same = rk.parse_movers_rows(MOVERS_RAW, "gainers", "1d")
        self.assertIn("Tanggal acuan: 7 Okt 2026", "\n".join(render_movers(result(rk.MoversQuery("gainers", "1d"), gainers=same))))
        mixed = rk.parse_movers_rows(
            movers_raw("gainers", "1d", [mover("AAAA", 0.2, day="2026-10-07"), mover("BBBB", 0.1, day="2026-10-06"), mover("CCCC", 0.05, day=None)]),
            "gainers", "1d",
        )
        text = "\n".join(render_movers(result(rk.MoversQuery("gainers", "1d"), gainers=mixed)))
        self.assertIn("Tanggal acuan berbeda per emiten", text)
        self.assertIn("tutup Rp 100 (6 Okt 2026)", text)
        self.assertNotIn("CCCC</code> PT CCCC Tbk — <b>+5,00%</b> · tutup", text)  # no date, no close
        none = rk.parse_movers_rows(movers_raw("gainers", "1d", [mover("AAAA", 0.2, day=None)]), "gainers", "1d")
        self.assertIn("Tanggal acuan tidak tersedia", "\n".join(render_movers(result(rk.MoversQuery("gainers", "1d"), gainers=none))))

    def test_html_escaped_and_disclaimer(self):
        lst = rk.parse_movers_rows(movers_raw("gainers", "1d", [mover("AAAA", 0.1, name="A&B <Tbk>")]), "gainers", "1d")
        chunks = render_movers(result(rk.MoversQuery("gainers", "1d"), gainers=lst))
        self.assertIn("A&amp;B &lt;Tbk&gt;", chunks[0])
        self.assertIn("bukan rekomendasi", chunks[-1])
        self.assertIn("bukan real-time", chunks[-1])
        assert_safe(self, chunks)


class ActiveDataTests(unittest.TestCase):
    def setUp(self):
        self.days = rk.parse_most_traded(TRADED_RAW)

    def test_days_stay_separate_and_latest_is_picked(self):
        self.assertEqual(len(self.days), 22)
        latest = rk.pick_day(self.days, None)
        self.assertEqual(latest.day, "2026-10-07")
        goto = next(r for r in latest.rows if r.symbol == "GOTO")
        self.assertEqual(goto.volume, 12843222300)  # that day's volume, never a sum of days
        self.assertEqual(rk.pick_day(self.days, date(2026, 10, 6)).rows[0].volume, 18443953300)

    def test_requested_date_is_never_swapped(self):
        self.assertIsNone(rk.pick_day(self.days, date(2026, 10, 4)))  # a Sunday in the window
        self.assertEqual(rk.parse_most_traded(SUNDAY_RAW), {})
        self.assertIsNone(rk.pick_day({}, None))

    def test_ties_zero_and_invalid_volume(self):
        raw = {"2026-10-07": [
            {"symbol": "ZZZZ.JK", "company_name": "Z", "volume": 500, "price": 10},
            {"symbol": "AAAA.JK", "company_name": "A", "volume": 500, "price": 10},
            {"symbol": "BBBB.JK", "company_name": "B", "volume": 900, "price": None},
            {"symbol": "CCCC.JK", "company_name": "C", "volume": 0, "price": 5},
            {"symbol": "DDDD.JK", "company_name": "D", "volume": None, "price": 5},
        ]}
        day = rk.parse_most_traded(raw)["2026-10-07"]
        self.assertTrue(day.resorted)
        self.assertEqual([r.symbol for r in day.rows], ["BBBB", "AAAA", "ZZZZ", "CCCC"])
        self.assertEqual(day.dropped, 1)
        text = "\n".join(render_active(Fetched({}, T0), day, rk.ActiveQuery(None, 5)))
        self.assertIn("0 lembar", text)
        self.assertIn("1 baris diabaikan", text)
        self.assertIn("API hanya mengembalikan 4 emiten", text)
        self.assertIn("<code>BBBB</code> B — 900 lembar\n", text)  # no price -> no price shown

    def test_volume_units_and_render(self):
        day = rk.pick_day(self.days, None)
        chunks = render_active(Fetched(self.days, T0), day, rk.ActiveQuery(None, 10))
        text = "\n".join(chunks)
        self.assertIn("Tanggal perdagangan: 7 Okt 2026 (terbaru yang tersedia)", text)
        self.assertIn("12,84 miliar lembar", text)
        self.assertIn("907,07 juta lembar", text)
        self.assertIn("bukan nilai transaksi atau frekuensi", text)
        self.assertNotIn("lot", text.lower().replace("pilot", ""))
        self.assertEqual(text.count("<code>"), 10)
        assert_safe(self, chunks)

    def test_no_data_messages(self):
        text = "\n".join(render_active(Fetched({}, T0), None, rk.ActiveQuery(date(2026, 10, 4), 5)))
        self.assertIn("Data tidak tersedia untuk tanggal 4 Okt 2026.", text)
        self.assertNotIn("libur", text)


class CallbackTests(unittest.TestCase):
    def test_round_trip_and_length(self):
        for w in rk.WHICH:
            for p in rk.PERIODS:
                data = rk.cb_movers(rk.MoversQuery(w, p))
                self.assertLessEqual(len(data.encode()), 64)
                self.assertEqual(rk.parse_movers_callback(data), rk.MoversQuery(w, p))
        for day in (None, date(2026, 10, 7)):
            for n in rk.ACTIVE_SIZES:
                data = rk.cb_active(day, n)
                self.assertLessEqual(len(data.encode()), 64)
                self.assertEqual(rk.parse_active_callback(data, TODAY), rk.ActiveQuery(day, n))

    def test_bad_payloads(self):
        for bad in (None, "", "mov:x:7d", "mov:b:2d", "mov:b:7d:1", "stk:s:BBCA"):
            self.assertIsNone(rk.parse_movers_callback(bad), bad)
        for bad in ("act:L:7", "act:L:20", "act:2026-13-01:5", "act:2026-10-09:5", "act:X:5", "act:L:5:1"):
            self.assertIsNone(rk.parse_active_callback(bad, TODAY), bad)


# ----------------------------------------------------------------------------- handlers


class FakeClient:
    """Serves top_changes / most_traded from the fixtures, honouring the request params."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, Exception] = {}  # key: endpoint or "endpoint:classification"
        self.delay: dict[str, float] = {}  # period -> seconds

    async def get(self, name, *, path_params=None, params=None):
        params = dict(params or {})
        self.calls.append((name, params))
        await asyncio.sleep(self.delay.get(params.get("periods"), 0))
        for key in (name, f"{name}:{params.get('classifications')}"):
            if key in self.fail:
                raise self.fail[key]
        if name == "top_changes":
            cls, period = params["classifications"], params["periods"]
            rows = MOVERS_RAW[cls].get(period) or MOVERS_RAW[cls]["7d"]  # 14d/30d/365d reuse 7d rows
            return {cls: {period: copy.deepcopy(rows)}}
        if name == "most_traded":
            if "start" not in params:
                return copy.deepcopy(TRADED_RAW)
            day = params["start"]
            return {day: copy.deepcopy(TRADED_RAW[day])} if day in TRADED_RAW else copy.deepcopy(SUNDAY_RAW)
        raise AssertionError(name)


def new_message(message_id=1):
    msg = AsyncMock(spec=Message)
    msg.chat_id, msg.message_id = 9, message_id
    return msg


def command(args, status=None):
    update = MagicMock()
    update.message.reply_text = AsyncMock(return_value=status or new_message())
    context = MagicMock()
    context.args = args
    context.bot.send_message = AsyncMock()
    return update, context


def tap(data, message):
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.message = message
    update.callback_query.answer = AsyncMock()
    context = MagicMock()
    context.bot.send_message = AsyncMock()
    return update, context


def buttons(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        rk.reset_caches()
        self.client = FakeClient()
        self.clock = [1000.0]
        clock = lambda: self.clock[0]  # noqa: E731
        for p in (
            patch.object(rk, "get_client", lambda: self.client),
            patch.object(rk, "_movers_cache", TTLCache(900, 5000, clock=clock)),
            patch.object(rk, "_latest_cache", TTLCache(900, 5000, clock=clock)),
            patch.object(rk, "_dated_cache", TTLCache(86400, 200000, clock=clock)),
            patch.object(handlers, "_jakarta_today", lambda: TODAY),
            patch.object(settings, "groq_api_key", ""),  # neither command may need the LLM
        ):
            p.start()
            self.addCleanup(p.stop)

    def calls(self, name):
        return [c[1] for c in self.client.calls if c[0] == name]

    async def invoke(self, fn, args):
        status = new_message()
        update, context = command(args, status)
        await fn(update, context)
        return update, status

    # ---- /movers

    async def test_movers_default_requests_two_combinations(self):
        update, status = await self.invoke(handlers.movers, [])
        self.assertEqual(update.message.reply_text.call_args.args[0], "⏳ Mengambil pergerakan saham…")
        sent = sorted((c["classifications"], c["periods"]) for c in self.calls("top_changes"))
        self.assertEqual(sent, [("top_gainers", "1d"), ("top_losers", "1d")])
        for c in self.calls("top_changes"):
            self.assertEqual((c["n_stock"], c["min_mcap_billion"]), (5, 5000))
        text = status.edit_text.call_args.args[0]
        self.assertIn("Top Movers — 1D", text)
        self.assertIn("Top Gainers", text)
        self.assertIn("Top Losers", text)
        data = buttons(status.edit_text.call_args.kwargs["reply_markup"])
        self.assertEqual(data[:5], [f"mov:b:{p}" for p in rk.PERIODS])
        self.assertEqual(data[5:], ["mov:g:1d", "mov:l:1d", "mov:b:1d"])

    async def test_movers_each_period_and_classification(self):
        for p in rk.PERIODS:
            for which, expected in (("gainers", ["top_gainers"]), ("losers", ["top_losers"])):
                rk.reset_caches()
                self.client.calls.clear()
                _, status = await self.invoke(handlers.movers, [which, p])
                self.assertEqual([c["classifications"] for c in self.calls("top_changes")], expected)
                self.assertEqual({c["periods"] for c in self.calls("top_changes")}, {p})
                self.assertIn(f"Top Movers — {p.upper()}", status.edit_text.call_args.args[0])

    async def test_movers_invalid_shows_help_without_calls(self):
        update, _ = await self.invoke(handlers.movers, ["gainers", "2d"])
        self.assertEqual(update.message.reply_text.call_args.args[0], MOVERS_HELP)
        self.assertEqual(self.client.calls, [])

    async def test_one_half_fails(self):
        self.client.fail["top_changes:top_losers"] = SectorsError(429, "RATE_LIMIT_EXCEEDED")
        _, status = await self.invoke(handlers.movers, ["7d"])
        text = status.edit_text.call_args.args[0]
        self.assertIn("MBSS", text)
        self.assertIn("Top Losers</b>\n⚠️ Bagian ini gagal dimuat. Batas permintaan Sectors API tercapai (429)", text)
        self.assertNotIn("RATE_LIMIT_EXCEEDED", text)

    async def test_both_halves_fail(self):
        for exc, needle in (
            (SectorsError(None, "network", timed_out=True), "timeout"),
            (SectorsError(503, "x"), "(503)"),
            (SectorsError(401, "x"), "ditolak (401)"),
        ):
            rk.reset_caches()
            self.client.fail["top_changes"] = exc
            _, status = await self.invoke(handlers.movers, [])
            text = status.edit_text.call_args.args[0]
            self.assertIn("Gagal memuat pergerakan saham", text)
            self.assertIn(needle, text)

    async def test_movers_cache_hit_and_expiry(self):
        await self.invoke(handlers.movers, [])
        await self.invoke(handlers.movers, [])
        self.assertEqual(len(self.calls("top_changes")), 2)
        self.clock[0] += 901
        await self.invoke(handlers.movers, [])
        self.assertEqual(len(self.calls("top_changes")), 4)

    async def test_stale_copy_is_labelled(self):
        await self.invoke(handlers.movers, ["gainers"])
        self.clock[0] += 901
        self.client.fail["top_changes"] = SectorsError(502, "x")
        _, status = await self.invoke(handlers.movers, ["gainers"])
        text = status.edit_text.call_args.args[0]
        self.assertIn("FORU", text)
        self.assertIn("Sectors API gagal saat memperbarui", text)

    async def test_movers_buttons_reuse_cache(self):
        _, status = await self.invoke(handlers.movers, ["7d"])
        update, context = tap("mov:g:7d", status)
        await handlers.movers_button(update, context)
        update.callback_query.answer.assert_awaited_once_with()
        self.assertEqual(len(self.calls("top_changes")), 2)  # gainers 7d was already cached
        self.assertNotIn("Top Losers", status.edit_text.call_args.args[0])

    async def test_invalid_movers_button(self):
        update, context = tap("mov:b:2d", new_message())
        await handlers.movers_button(update, context)
        self.assertTrue(update.callback_query.answer.call_args.kwargs["show_alert"])
        self.assertEqual(self.client.calls, [])

    async def test_slow_old_tap_never_overwrites_newer_tap(self):
        message = new_message(77)
        self.client.delay["7d"] = 0.05  # the first tap is slow, the second fast
        first = handlers.movers_button(*tap("mov:b:7d", message))
        second = handlers.movers_button(*tap("mov:b:30d", message))
        await asyncio.gather(first, second)
        texts = [c.args[0] for c in message.edit_text.call_args_list]
        self.assertEqual(len(texts), 1)
        self.assertIn("Top Movers — 30D", texts[0])

    async def test_rapid_identical_taps_share_one_request(self):
        message = new_message(5)
        await asyncio.gather(*(handlers.movers_button(*tap("mov:g:14d", message)) for _ in range(5)))
        self.assertEqual(len(self.calls("top_changes")), 1)

    # ---- /active

    async def test_active_default(self):
        update, status = await self.invoke(handlers.active, [])
        self.assertEqual(update.message.reply_text.call_args.args[0], "⏳ Mengambil saham paling aktif…")
        self.assertEqual(self.calls("most_traded"), [{"n_stock": 10, "adjusted": "false"}])
        text = status.edit_text.call_args.args[0]
        self.assertIn("Tanggal perdagangan: 7 Okt 2026 (terbaru yang tersedia)", text)
        self.assertEqual(text.count("<code>"), 5)
        self.assertEqual(
            buttons(status.edit_text.call_args.kwargs["reply_markup"]), ["act:L:5", "act:L:10", "act:L:5"]
        )

    async def test_active_count_date_and_both(self):
        _, status = await self.invoke(handlers.active, ["10"])
        self.assertEqual(status.edit_text.call_args.args[0].count("<code>"), 10)
        _, status = await self.invoke(handlers.active, ["2026-10-06"])
        self.assertIn("Tanggal perdagangan: 6 Okt 2026\n", status.edit_text.call_args.args[0])
        _, status = await self.invoke(handlers.active, ["2026-10-06", "10"])
        self.assertEqual(status.edit_text.call_args.args[0].count("<code>"), 10)
        dated = [c for c in self.calls("most_traded") if "start" in c]
        self.assertEqual(dated, [{"n_stock": 10, "adjusted": "false", "start": "2026-10-06", "end": "2026-10-06"}])

    async def test_active_date_without_data(self):
        _, status = await self.invoke(handlers.active, ["2026-10-04"])
        text = status.edit_text.call_args.args[0]
        self.assertIn("Data tidak tersedia untuk tanggal 4 Okt 2026.", text)
        self.assertIn("act:L:5", buttons(status.edit_text.call_args.kwargs["reply_markup"]))

    async def test_active_input_errors(self):
        for args, needle in ((["2026-10-09"], "belum terjadi"), (["2026-13-01"], "tidak valid"), (["50"], "1–10"), (["x", "y", "z"], "Gunakan /active")):
            update, _ = await self.invoke(handlers.active, args)
            self.assertIn(needle, update.message.reply_text.call_args.args[0])
        self.assertEqual(self.client.calls, [])

    async def test_active_buttons_reuse_cache(self):
        _, status = await self.invoke(handlers.active, [])
        update, context = tap("act:L:10", status)
        await handlers.active_button(update, context)
        self.assertEqual(status.edit_text.call_args.args[0].count("<code>"), 10)
        update, context = tap("act:L:5", status)
        await handlers.active_button(update, context)
        self.assertEqual(len(self.calls("most_traded")), 1)

    async def test_past_dates_cached_longer_than_latest(self):
        await self.invoke(handlers.active, [])
        await self.invoke(handlers.active, ["2026-10-06"])
        self.clock[0] += 1000
        await self.invoke(handlers.active, [])
        await self.invoke(handlers.active, ["2026-10-06"])
        latest = [c for c in self.calls("most_traded") if "start" not in c]
        dated = [c for c in self.calls("most_traded") if "start" in c]
        self.assertEqual((len(latest), len(dated)), (2, 1))

    async def test_active_errors(self):
        for exc, needle in ((SectorsError(429, "x"), "(429)"), (SectorsError(500, "x"), "(500)"),
                            (SectorsError(None, "n", timed_out=True), "timeout")):
            rk.reset_caches()
            self.client.fail["most_traded"] = exc
            _, status = await self.invoke(handlers.active, [])
            text = status.edit_text.call_args.args[0]
            self.assertIn("Gagal memuat saham paling aktif", text)
            self.assertIn(needle, text)

    async def test_today_400_is_explained(self):
        self.client.fail["most_traded"] = SectorsError(400, "future date")
        _, status = await self.invoke(handlers.active, ["2026-10-08"])
        self.assertIn("belum tersedia", status.edit_text.call_args.args[0])

    async def test_invalid_active_button(self):
        update, context = tap("act:2026-10-09:5", new_message())
        await handlers.active_button(update, context)
        self.assertIn("tidak dikenali", update.callback_query.answer.call_args.args[0])

    def test_registered(self):
        from app.main import COMMANDS

        names = [c.command for c in COMMANDS]
        self.assertIn("movers", names)
        self.assertIn("active", names)
        self.assertIn("/movers", handlers.HELP_TEXT)
        self.assertIn("/active", handlers.HELP_TEXT)
        app = MagicMock()
        handlers.register(app)
        registered = [c.args[0] for c in app.add_handler.call_args_list]
        commands = [getattr(h, "commands", frozenset()) for h in registered]
        patterns = [getattr(getattr(h, "pattern", None), "pattern", "") for h in registered]
        self.assertIn(frozenset({"movers"}), commands)
        self.assertIn(frozenset({"active"}), commands)
        self.assertIn("^mov:", patterns)
        self.assertIn("^act:", patterns)


if __name__ == "__main__":
    unittest.main()
