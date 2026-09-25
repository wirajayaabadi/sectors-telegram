import logging

from telegram import BotCommand, Update
from telegram.ext import Application, ApplicationBuilder

from app.config import settings
from app.sectors import close_client
from app import store, handlers

log = logging.getLogger(__name__)

# The command menu Telegram shows when you type "/". Stored on Telegram's servers,
# so we re-set it on every startup to keep it pointed at THIS bot's handlers.
COMMANDS = [
    BotCommand("report", "Generate the morning report now"),
    BotCommand("ihsg", "Latest IHSG level"),
    BotCommand("ask", "Ask about markets or a company"),
    BotCommand("reset", "Clear conversation memory"),
    BotCommand("start", "Subscribe / show help"),
    BotCommand("stop", "Unsubscribe"),
    BotCommand("status", "Check subscription status"),
    BotCommand("whoami", "Get your chat ID"),
    BotCommand("testjob", "(admin) run the scheduled job now"),
]


async def on_startup(application: Application) -> None:
    log.info("Bot is starting up...")
    store.init_db()
    log.info("Database initialized.")
    from app.provider import configure_llm  # registers the Groq client for the agents

    log.info("LLM: %s", configure_llm())
    from app import jobs

    jobs.schedule_jobs(application)  # register the daily report
    await application.bot.set_my_commands(COMMANDS)  # repoint the "/" menu to these
    me = await application.bot.get_me()
    log.info("%s online. Username: @%s, ID: %s", me.first_name, me.username, me.id)


async def on_shutdown(application: Application) -> None:
    await close_client()


def build_app() -> Application:
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set in the environment variables.")
    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
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
    # httpx logs are too verbose, so we silence it to avoid leaking sensitive information like API keys in logs. Same for telegram library.
    for noisy in ["httpx", "telegram"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)
    build_app().run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
