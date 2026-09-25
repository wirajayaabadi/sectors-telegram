import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from app import store
from app.config import settings
from app.render import render_report
from app.report import build_report
from app.sectors import get_client

log = logging.getLogger(__name__)

HELP_TEXT = (
    "/report - Generate the morning report now\n"
    "/ihsg - Latest IHSG level\n"
    "/start - Subscribe / show help\n"
    "/whoami - Get your chat ID\n"
    "/stop - Unsubscribe from notifications\n"
    "/status - Check your subscription status"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    new_subscription = store.subscribe(chat.id, chat.title)
    text = (
        "You are now subscribed to the Sectors bot."
        if new_subscription
        else "You are already subscribed to the Sectors bot."
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
        "idx", "index_daily", path_params={"index_code": "ihsg"}, params={}
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
    msg = await update.message.reply_text("Preparing the morning report...")
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


def register(application: ApplicationBuilder) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("whoami", whoami))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("ihsg", ihsg))
    application.add_handler(CommandHandler("report", report))
