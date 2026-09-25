"""Step 1: the smallest possible Telegram bot.

Run:  uv run bot.py
Stop: Ctrl+C

Long polling: while this runs, it keeps asking Telegram "anything new for me?".
No server, no public URL. When you stop it, the bot is offline.
"""
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv()  # copies the lines in .env into os.environ

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN missing: copy .env.example to .env and paste your token")


# A handler is an async function. `update` is what arrived, `context` is the bot.
async def hello(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("hello 👋 I am the Sectors bot")


# You will need your own user id for admin commands in step 5. This is how you get it.
async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"user_id: {update.effective_user.id}")


app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("hello", hello))    # "/hello" -> hello()
app.add_handler(CommandHandler("whoami", whoami))
print("bot online, press Ctrl+C to stop")
app.run_polling()
