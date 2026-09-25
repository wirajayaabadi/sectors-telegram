import logging

from telegram import BotCommand, Update
from telegram.ext import Application, ApplicationBuilder

from app.config import settings
from app import store, handlers

log = logging.getLogger(__name__)

# The command menu Telegram shows when you type "/". Stored on Telegram's servers,
# so we re-set it on every startup to keep it pointed at THIS bot's handlers.
COMMANDS = [
    BotCommand("start", "Subscribe / show help"),
    BotCommand("stop", "Unsubscribe"),
    BotCommand("status", "Check subscription status"),
    BotCommand("whoami", "Get your chat ID"),
]


async def on_startup(application: Application) -> None:
    log.info("Bot is starting up...")
    store.init_db()
    log.info("Database initialized.")
    await application.bot.set_my_commands(COMMANDS)  # repoint the "/" menu to these
    me = await application.bot.get_me()
    log.info("%s online. Username: @%s, ID: %s", me.first_name, me.username, me.id)


def build_app() -> Application:
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set in the environment variables.")
    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(on_startup)
        .build()
    )
    handlers.register(application)
    return application


# logging verbose level sorted by the lowest: DEBUG < INFO < WARNING < ERROR < CRITICAL


def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # telegram library logs are verbose and can leak sensitive info, so we quiet them.
    logging.getLogger("telegram").setLevel(logging.WARNING)
    build_app().run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
