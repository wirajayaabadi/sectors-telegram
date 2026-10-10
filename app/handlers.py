import itertools
import logging
import re
from collections import OrderedDict
from datetime import datetime

from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app import rankings
from app import stock as stocks
from app import store, subsector
from app.config import settings
from app.render import (
    ACTIVE_LOADING,
    MOVERS_HELP,
    MOVERS_LOADING,
    SECTOR_LOADING,
    STOCK_HELP,
    render_active,
    render_active_help,
    render_comparison,
    render_movers,
    render_report,
    render_stock,
    render_stock_candidates,
    render_stock_multi,
    stock_loading,
    render_subsector,
    render_subsector_candidates,
    render_subsector_picker,
)
from app.report import build_report
from app.sectors import SectorsError, get_client
from app.sectors_agents.compare import MAX_COMPANIES, MIN_COMPANIES

log = logging.getLogger(__name__)

HELP_TEXT = (
    "/report - Generate the morning report now\n"
    "/ihsg - Latest IHSG level\n"
    "/compare <tickers> - Compare companies head to head, e.g. /compare BBCA BBRI\n"
    "/movers [gainers|losers] [1d|7d|14d|30d|365d] - Biggest price moves, e.g. /movers 7d\n"
    "/active [YYYY-MM-DD] [count] - Most traded stocks by volume, e.g. /active 10\n"
    "/stock <ticker> - One IDX stock's summary card, e.g. /stock BBCA\n"
    "/sector [subsector] - IDX sub-sector summary (companies, growth, valuation), e.g. /sector banks\n"
    "/ask <question> - Ask about the IDX market or a company\n"
    "/reset - Clear our conversation memory\n"
    "/whoami - Get your chat ID\n"
    "/stop - Unsubscribe from notifications\n"
    "/status - Check your subscription status\n"
    "\nIn a private chat you can also just type your question directly."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    new_subscription = store.subscribe(chat.id, chat.title)
    text = (
        "You are now subscribed to Calvin's IDX bot."
        if new_subscription
        else "You are already subscribed to Calvin's IDX bot."
    )
    await update.message.reply_text(f"{text}\n\n{HELP_TEXT}")


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    await update.message.reply_text(
        f"Your chat ID is: {chat_id}\nYour user ID is: {user_id}"
    )


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if store.unsubscribe(chat_id):
        await update.message.reply_text("You have been unsubscribed.")
    else:
        await update.message.reply_text("You were not subscribed.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    lines = [
        f"Subscription status for chat ID {chat_id}: {'yes' if store.is_subscribed(chat_id) else 'no'}"
    ]
    if update.effective_user.id in settings.admin_ids:
        lines.append(f"Number of active subscribers: {len(store.active_chat_ids())}")
    await update.message.reply_text("\n".join(lines))


async def ihsg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await get_client().get(
        "index_daily", path_params={"index_code": "ihsg"}, params={}
    )
    rows = data if isinstance(data, list) else data.get("results", data.get("data", []))
    if not rows:
        await update.message.reply_text("No data available for IHSG.")
        return
    last = rows[-1]
    price = float(last.get("price", 0))
    prev = rows[-2] if len(rows) > 1 else None
    if prev:
        prev_price = float(prev.get("price", 0))
        change = price - prev_price
        change_pct = (change / prev_price * 100) if prev_price else 0
        change_str = f"{change:+.2f} ({change_pct:+.2f}%)"
    await update.message.reply_text(
        f"IHSG: {price:.2f}\nChange: {change_str if prev else 'N/A'}\n"
        f"Data as of: {last.get('date', 'N/A')}"
    )


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not settings.groq_api_key:
        await update.message.reply_text("Set GROQ_API_KEY in .env to enable the report.")
        return
    msg = await update.message.reply_text("Preparing the morning report... (this can take a minute)")
    try:
        result = await build_report()
    except Exception:
        log.exception("/report failed")
        await msg.edit_text("Sorry, the report could not be generated. Please try again.")
        return
    chunks = render_report(result)
    await msg.delete()
    for chunk in chunks:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=chunk, parse_mode=ParseMode.HTML
        )


def _parse_symbols(args: list[str]) -> list[str]:
    """Pull tickers out of '/compare BBCA BBRI' or '/compare bbca,bbri'.

    Plain code, not the LLM: a typo should produce a usage hint, not a hallucinated ticker.
    IDX tickers are always four letters, so anything else is dropped here.
    """
    out: list[str] = []
    for token in re.split(r"[,\s]+", " ".join(args).upper()):
        if token.isalpha() and len(token) == 4 and token not in out:
            out.append(token)
    return out[:MAX_COMPANIES]


async def compare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not settings.groq_api_key:
        await update.message.reply_text("Set GROQ_API_KEY in .env to enable /compare.")
        return
    symbols = _parse_symbols(context.args)
    if len(symbols) < MIN_COMPANIES:
        await update.message.reply_text(
            f"Give me {MIN_COMPANIES} to {MAX_COMPANIES} tickers to compare.\n"
            "Example: /compare BBCA BBRI"
        )
        return

    from app.sectors_agents.compare import compare_companies  # lazy, like _answer below

    msg = await update.message.reply_text(
        f"Comparing {', '.join(symbols)}... (this can take a minute)"
    )
    try:
        result = await compare_companies(symbols)
    except Exception:
        log.exception("/compare failed for %s", symbols)
        await msg.edit_text("Sorry, that comparison could not be generated. Please try again.")
        return
    chunks = render_comparison(result)
    await msg.delete()
    for chunk in chunks:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=chunk, parse_mode=ParseMode.HTML
        )


# ----------------------------------------------------------------------------- /sector
# Plain code end to end (see app/subsector.py): no GROQ_API_KEY check, no agent.


def _picker_markup(subs: list[subsector.Subsector], page: int) -> InlineKeyboardMarkup:
    size = subsector.PAGE_SIZE
    items = subs[page * size : (page + 1) * size]
    rows = [
        [InlineKeyboardButton(s.label, callback_data=subsector.cb_select(s.slug)) for s in items[i : i + 2]]
        for i in range(0, len(items), 2)
    ]
    pages = subsector.page_count(len(subs))
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("« Sebelumnya", callback_data=subsector.cb_page(page - 1)))
    nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data=subsector.CB_NOOP))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("Berikutnya »", callback_data=subsector.cb_page(page + 1)))
    rows.append(nav)
    return InlineKeyboardMarkup(rows)


def _result_markup(slug: str, active: str) -> InlineKeyboardMarkup:
    def tab(key: str) -> InlineKeyboardButton:
        label = subsector.VIEWS[key]
        return InlineKeyboardButton(
            f"• {label}" if key == active else label, callback_data=subsector.cb_view(key, slug)
        )

    return InlineKeyboardMarkup(
        [
            [tab("ov"), tab("co")],
            [tab("gr"), tab("va")],
            [InlineKeyboardButton("📂 Pilih subsektor", callback_data=subsector.cb_page(0))],
        ]
    )


def _retry_markup(slug: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔄 Coba lagi", callback_data=subsector.cb_select(slug)),
                InlineKeyboardButton("📂 Pilih subsektor", callback_data=subsector.cb_page(0)),
            ]
        ]
    )


async def _edit(message: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():  # tapping the tab you're already on
            raise


async def _show(message: Message, chunks: list[str], markup, context) -> None:
    """Edit the status message into the result; overflow (rare) follows as new messages."""
    await _edit(message, chunks[0], markup if len(chunks) == 1 else None)
    for i, chunk in enumerate(chunks[1:], start=2):
        await context.bot.send_message(
            chat_id=message.chat_id,
            text=chunk,
            parse_mode=ParseMode.HTML,
            reply_markup=markup if i == len(chunks) else None,
        )


def _log_failure(what: str, exc: Exception) -> None:
    if isinstance(exc, (SectorsError, subsector.EmptyData)):
        log.warning("/sector %s failed: %s", what, exc)
    else:
        log.exception("/sector %s failed", what)


async def _list_or_error(reply) -> list[subsector.Subsector] | None:
    try:
        return (await subsector.get_subsectors()).value
    except Exception as exc:
        _log_failure("subsector list", exc)
        await reply(f"Daftar subsektor tidak bisa diambil. {escape(subsector.describe_error(exc))}")
        return None


async def _load_subsector(message: Message, slug: str, context) -> None:
    """Status message -> cache/API -> render -> same message, edited."""
    try:
        fetched = await subsector.get_report(slug)
    except Exception as exc:
        _log_failure(slug, exc)
        await _edit(
            message,
            f"Gagal memuat subsektor <b>{escape(subsector.label_for(slug))}</b>.\n"
            f"{escape(subsector.describe_error(exc))}",
            _retry_markup(slug),
        )
        return
    await _show(message, render_subsector(fetched, "ov"), _result_markup(slug, "ov"), context)


async def sector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip()

    async def reply(text, markup=None):
        return await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)

    subs = await _list_or_error(reply)
    if subs is None:
        return
    if not query:
        pages = subsector.page_count(len(subs))
        await reply(render_subsector_picker(0, pages), _picker_markup(subs, 0))
        return

    found = subsector.match(query, subs)
    if found.slug is None:  # never guess: ask the user to confirm one of the candidates
        rows = [
            [InlineKeyboardButton(subsector.label_for(slug), callback_data=subsector.cb_select(slug))]
            for slug in found.candidates
        ]
        rows.append([InlineKeyboardButton("📂 Lihat semua subsektor", callback_data=subsector.cb_page(0))])
        await reply(render_subsector_candidates(query, bool(found.candidates)), InlineKeyboardMarkup(rows))
        return

    message = await reply(SECTOR_LOADING)
    await _load_subsector(message, found.slug, context)


async def sector_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    parsed = subsector.parse_callback(q.data)
    if parsed is None:
        await q.answer("Tombol ini tidak dikenali. Kirim /sector untuk mulai lagi.", show_alert=True)
        return
    kind, arg = parsed
    if kind == "noop":
        await q.answer()
        return
    if not isinstance(q.message, Message):  # too old for Telegram to let us edit it
        await q.answer("Pesan ini sudah terlalu lama. Kirim /sector lagi.", show_alert=True)
        return

    if kind == "view":  # switching tabs reuses the fetched report: no credits spent
        tab, slug = arg
        cached = subsector.peek_report(slug)
        if cached is None:
            await q.answer(
                f"Sesi tombol ini sudah kedaluwarsa. Kirim /sector {slug} untuk memuat ulang.",
                show_alert=True,
            )
            return
        await q.answer()
        await _show(q.message, render_subsector(cached, tab), _result_markup(slug, tab), context)
        return

    await q.answer()  # answer before any network call so the button stops spinning

    async def reply(text, markup=None):
        await _edit(q.message, text, markup)

    cached_list = subsector.peek_subsectors()
    subs = cached_list.value if cached_list else await _list_or_error(reply)
    if subs is None:
        return

    if kind == "page":
        pages = subsector.page_count(len(subs))
        page = min(arg, pages - 1)
        await _edit(q.message, render_subsector_picker(page, pages), _picker_markup(subs, page))
        return

    slug = arg  # kind == "select"
    if slug not in {s.slug for s in subs}:
        await _edit(
            q.message,
            "Subsektor itu tidak ada di daftar Sectors saat ini. Silakan pilih lagi:",
            _picker_markup(subs, 0),
        )
        return
    await _edit(q.message, SECTOR_LOADING)
    await _load_subsector(q.message, slug, context)


# ----------------------------------------------------------------------------- /stock
# Plain code end to end (see app/stock.py): no GROQ_API_KEY check, no agent. Nothing here
# is per-user, so buttons in a group only ever show the same public market data.


def _stock_markup(card: stocks.StockCard, active: str) -> InlineKeyboardMarkup:
    tabs = [
        InlineKeyboardButton(
            f"• {stocks.VIEWS[t]}" if t == active else stocks.VIEWS[t],
            callback_data=stocks.cb_view(t, card.symbol),
        )
        for t in card.tabs()
    ]
    rows = [tabs[i : i + 3] for i in range(0, len(tabs), 3)]
    if card.report is None or card.price is None:  # partial card: let them try again
        rows.append([InlineKeyboardButton("🔄 Coba lagi", callback_data=stocks.cb_select(card.symbol))])
    return InlineKeyboardMarkup(rows)


def _candidate_markup(candidates: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"{sym} · {name}"[:60], callback_data=stocks.cb_select(sym))]
            for sym, name in candidates
        ]
    )


async def _search_quietly(keyword: str) -> list[tuple[str, str]]:
    """Candidates for a dead end. A failed search just means no suggestions."""
    if not stocks.SEARCH_RE.match(keyword):
        return []
    try:
        return await stocks.search(keyword)
    except Exception as exc:
        log.warning("/stock search %r failed: %s", keyword, exc)
        return []


async def _load_stock(message: Message, symbol: str, context) -> None:
    """Status message -> report + prices (parallel, cached) -> card, same message edited."""
    try:
        card = await stocks.load_card(symbol)
    except stocks.NotListed:
        found = await _search_quietly(symbol.lower())
        await _edit(
            message,
            render_stock_candidates(symbol, bool(found), not_listed=True),
            _candidate_markup(found) if found else None,
        )
        return
    except Exception as exc:
        if isinstance(exc, (SectorsError, stocks.EmptyData)):
            log.warning("/stock %s failed: %s", symbol, exc)
        else:
            log.exception("/stock %s failed", symbol)
        await _edit(
            message,
            f"Gagal memuat data <b>{escape(symbol)}</b>.\n{escape(stocks.describe_error(exc))}",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Coba lagi", callback_data=stocks.cb_select(symbol))]]),
        )
        return
    await _show(message, render_stock(card, "ov"), _stock_markup(card, "ov"), context)


async def stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parsed = stocks.parse_args(context.args)

    async def reply(text, markup=None):
        return await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)

    if parsed.kind in ("help", "invalid"):
        await reply(STOCK_HELP)
        return
    if parsed.kind == "multi":
        await reply(render_stock_multi(parsed.tickers))
        return
    if parsed.kind == "search":
        try:
            found = await stocks.search(parsed.value)
        except Exception as exc:
            log.warning("/stock search %r failed: %s", parsed.value, exc)
            await reply(f"Pencarian nama gagal. {escape(stocks.describe_error(exc))}")
            return
        await reply(render_stock_candidates(parsed.value, bool(found)), _candidate_markup(found) if found else None)
        return

    message = await reply(stock_loading(parsed.value))
    await _load_stock(message, parsed.value, context)


async def stock_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    parsed = stocks.parse_callback(q.data)
    if parsed is None:
        await q.answer("Tombol ini tidak dikenali. Kirim /stock untuk mulai lagi.", show_alert=True)
        return
    if not isinstance(q.message, Message):
        await q.answer("Pesan ini sudah terlalu lama. Kirim /stock lagi.", show_alert=True)
        return
    kind, arg = parsed

    if kind == "select":
        await q.answer()
        await _edit(q.message, stock_loading(arg))
        await _load_stock(q.message, arg, context)
        return

    tab, symbol = arg
    card = stocks.peek_card(symbol)  # tab switches reuse what was fetched
    if card is None:
        await q.answer(
            f"Sesi tombol ini sudah kedaluwarsa. Kirim /stock {symbol} untuk memuat ulang.", show_alert=True
        )
        return
    if tab not in card.tabs():
        await q.answer("Data untuk tampilan ini tidak tersedia.", show_alert=True)
        return
    await q.answer()

    if tab == "va" and card.valuation is None:  # the one section fetched on demand
        await _edit(q.message, stock_loading(symbol))
        try:
            card.valuation = await stocks.get_valuation(symbol)
        except Exception as exc:
            log.warning("/stock %s valuation failed: %s", symbol, exc)
            card.valuation_error = stocks.describe_error(exc)
    await _show(q.message, render_stock(card, tab), _stock_markup(card, tab), context)


# ----------------------------------------------------------------------------- /movers, /active
# Plain code end to end (see app/rankings.py): no GROQ_API_KEY check, no agent.

# Newest tap per message. A slow answer to an older tap must not overwrite a newer one,
# so each tap takes a ticket and only the latest ticket may edit the message.
_tickets = itertools.count(1)
_latest_tap: OrderedDict[tuple[int, int], int] = OrderedDict()
_LATEST_TAP_MAX = 2000


def _claim(message: Message) -> int:
    key = (message.chat_id, message.message_id)
    ticket = next(_tickets)
    _latest_tap[key] = ticket
    _latest_tap.move_to_end(key)
    while len(_latest_tap) > _LATEST_TAP_MAX:
        _latest_tap.popitem(last=False)
    return ticket


def _is_latest(message: Message, ticket: int | None) -> bool:
    return ticket is None or _latest_tap.get((message.chat_id, message.message_id)) == ticket


def _jakarta_today():
    return datetime.now(settings.tz).date()


def _movers_markup(q: rankings.MoversQuery) -> InlineKeyboardMarkup:
    def btn(label: str, target: rankings.MoversQuery) -> InlineKeyboardButton:
        mark = "• " if target == q else ""
        return InlineKeyboardButton(mark + label, callback_data=rankings.cb_movers(target))

    periods = [btn(p.upper(), rankings.MoversQuery(q.which, p)) for p in rankings.PERIODS]
    names = {"gainers": "Gainers", "losers": "Losers", "both": "Keduanya"}
    filters_ = [btn(names[w], rankings.MoversQuery(w, q.period)) for w in ("gainers", "losers", "both")]
    return InlineKeyboardMarkup([periods, filters_])


def _log_ranking_failure(what: str, exc: Exception) -> None:
    if isinstance(exc, (SectorsError, subsector.EmptyData)):
        log.warning("%s failed: %s", what, exc)
    else:
        log.error("%s failed", what, exc_info=exc)


async def _load_movers(message: Message, q: rankings.MoversQuery, context, ticket: int | None = None) -> None:
    result = await rankings.load_movers(q)
    for which, exc in result.failures.items():
        _log_ranking_failure(f"/movers {which} {q.period}", exc)
    if not _is_latest(message, ticket):
        return
    if not result.parts:  # every requested half failed
        reasons = "\n".join(sorted(set(result.errors.values())))
        await _edit(message, f"Gagal memuat pergerakan saham.\n{escape(reasons)}", _movers_markup(q))
        return
    await _show(message, render_movers(result), _movers_markup(q), context)


async def movers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = rankings.parse_movers(context.args)
    if q is None:
        await update.message.reply_text(MOVERS_HELP)
        return
    message = await update.message.reply_text(MOVERS_LOADING)
    await _load_movers(message, q, context)


async def movers_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cq = update.callback_query
    q = rankings.parse_movers_callback(cq.data)
    if q is None:
        await cq.answer("Tombol ini tidak dikenali. Kirim /movers untuk mulai lagi.", show_alert=True)
        return
    if not isinstance(cq.message, Message):
        await cq.answer("Pesan ini sudah terlalu lama. Kirim /movers lagi.", show_alert=True)
        return
    await cq.answer()  # before any network call
    await _load_movers(cq.message, q, context, _claim(cq.message))


def _active_markup(q: rankings.ActiveQuery) -> InlineKeyboardMarkup:
    sizes = [
        InlineKeyboardButton(
            f"{'• ' if n == q.n else ''}Top {n}", callback_data=rankings.cb_active(q.day, n)
        )
        for n in rankings.ACTIVE_SIZES
    ]
    latest = InlineKeyboardButton("🔄 Data terbaru", callback_data=rankings.cb_active(None, q.n))
    return InlineKeyboardMarkup([sizes, [latest]])


async def _load_active(message: Message, q: rankings.ActiveQuery, context, ticket: int | None = None) -> None:
    today = _jakarta_today()
    try:
        fetched = await rankings.get_active_days(q.day, today)
    except Exception as exc:
        _log_ranking_failure(f"/active {q.day or 'latest'}", exc)
        if not _is_latest(message, ticket):
            return
        reason = rankings.describe_error(exc)
        if isinstance(exc, SectorsError) and exc.status == 400 and q.day == today:
            reason = "Data untuk tanggal hari ini belum tersedia di Sectors API."
        await _edit(message, f"Gagal memuat saham paling aktif.\n{escape(reason)}", _active_markup(q))
        return
    if not _is_latest(message, ticket):
        return
    day = rankings.pick_day(fetched.value, q.day)
    await _show(message, render_active(fetched, day, q), _active_markup(q), context)


async def active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = rankings.parse_active(context.args, _jakarta_today())
    if isinstance(q, rankings.ActiveError):
        await update.message.reply_text(render_active_help(q))
        return
    message = await update.message.reply_text(ACTIVE_LOADING)
    await _load_active(message, q, context)


async def active_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cq = update.callback_query
    q = rankings.parse_active_callback(cq.data, _jakarta_today())
    if q is None:
        await cq.answer("Tombol ini tidak dikenali. Kirim /active untuk mulai lagi.", show_alert=True)
        return
    if not isinstance(cq.message, Message):
        await cq.answer("Pesan ini sudah terlalu lama. Kirim /active lagi.", show_alert=True)
        return
    await cq.answer()
    await _load_active(cq.message, q, context, _claim(cq.message))


async def _answer(update: Update, context: ContextTypes.DEFAULT_TYPE, question: str) -> None:
    if not settings.groq_api_key:
        await update.message.reply_text("Set GROQ_API_KEY in .env to enable Q&A.")
        return
    from app.sectors_agents import answer  # lazy: bot starts fine without the LLM configured

    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    reply = await answer(chat_id, question)
    await update.message.reply_text(reply)


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    question = " ".join(context.args).strip()
    if not question:
        await update.message.reply_text("Example: /ask How is IHSG doing this week?")
        return
    await _answer(update, context, question)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer(update, context, update.message.text)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.sectors_agents import reset_memory

    await reset_memory(update.effective_chat.id)
    await update.message.reply_text("Conversation memory cleared.")


async def testjob(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin only: run the scheduled morning job in ~10s, delivered only to you."""
    if update.effective_user.id not in settings.admin_ids:
        await update.message.reply_text("Admin only.")
        return
    from app.jobs import morning_job

    context.job_queue.run_once(
        morning_job, when=10, data={"only_chat": update.effective_chat.id}, name="testjob"
    )
    await update.message.reply_text("Scheduled the morning job to run in ~10 seconds, sent only to you.")


def register(application: ApplicationBuilder) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("whoami", whoami))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("ihsg", ihsg))
    application.add_handler(CommandHandler("report", report))
    application.add_handler(CommandHandler("compare", compare))
    application.add_handler(CommandHandler("movers", movers))
    application.add_handler(CallbackQueryHandler(movers_button, pattern=r"^mov:"))
    application.add_handler(CommandHandler("active", active))
    application.add_handler(CallbackQueryHandler(active_button, pattern=r"^act:"))
    application.add_handler(CommandHandler("stock", stock))
    application.add_handler(CallbackQueryHandler(stock_button, pattern=r"^stk:"))
    application.add_handler(CommandHandler("sector", sector))
    application.add_handler(CallbackQueryHandler(sector_button, pattern=r"^sec:"))
    application.add_handler(CommandHandler("ask", ask))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("testjob", testjob))
    # Free text only in private chats, so the bot doesn't answer every group message.
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_text)
    )
