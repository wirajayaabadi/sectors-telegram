"""Scheduled delivery: build the report once, broadcast it to every subscriber.

The bot only sends while `python -m app.main` is running. Running it 24/7 needs a
server; that's out of scope here.
"""

import asyncio
import logging

from telegram import Bot
from telegram.error import Forbidden, RetryAfter
from telegram.ext import Application, ContextTypes

from app import store
from app.config import settings
from app.render import render_report
from app.report import build_report

log = logging.getLogger(__name__)

JOB_NAME = "morning_report"
BROADCAST_DELAY = 0.05  # ~20 msg/s, under Telegram's ~30 msg/s limit


async def _send_chunks(bot: Bot, chat_id: int, chunks: list[str]) -> str:
    """Send all chunks to one chat. Returns 'ok', 'blocked', or 'failed'."""
    from telegram.constants import ParseMode

    for chunk in chunks:
        try:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.HTML)
        except RetryAfter as e:  # flood control: wait the time Telegram asks, retry once
            await asyncio.sleep(e.retry_after + 1)
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.HTML)
        except Forbidden:  # user blocked the bot / removed it from the group
            return "blocked"
        except Exception:
            log.exception("send failed for %s", chat_id)
            return "failed"
    return "ok"


async def morning_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Build the report once, then broadcast. job.data={'only_chat': id} sends to one chat."""
    only_chat = (context.job.data or {}).get("only_chat")
    try:
        report = await build_report()
    except Exception as e:
        log.exception("morning job: report build failed")
        for admin in settings.admin_ids:
            await context.bot.send_message(admin, f"Morning report failed: {e}")
        return

    chunks = render_report(report)
    targets = [only_chat] if only_chat else store.active_chat_ids()
    sent = blocked = failed = 0
    for chat_id in targets:
        status = await _send_chunks(context.bot, chat_id, chunks)
        if status == "ok":
            sent += 1
        elif status == "blocked":
            blocked += 1
            store.unsubscribe(chat_id)  # self-clean: they left, stop sending
        else:
            failed += 1
        await asyncio.sleep(BROADCAST_DELAY)  # pace so we don't trip flood control
    log.info("morning job delivered: sent=%d blocked=%d failed=%d", sent, blocked, failed)


def schedule_jobs(app: Application) -> None:
    """Register the daily report at REPORT_TIME."""
    app.job_queue.run_daily(morning_job, time=settings.report_time_obj, name=JOB_NAME)
    log.info("scheduled %s at %s %s", JOB_NAME, settings.report_time, settings.timezone)
