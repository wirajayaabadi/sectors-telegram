import logging

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from app import store
from app.config import settings

log = logging.getLogger(__name__)

HELP_TEXT = (
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


def register(application: ApplicationBuilder) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("whoami", whoami))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("status", status))
