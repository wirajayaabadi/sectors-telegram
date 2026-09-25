"""Turn a Report into Telegram-safe HTML messages.

The agents produce a structured Report; this turns it into the actual text a
subscriber sees. Two jobs code must do (never the LLM): escape <, > & so Telegram's
HTML parser doesn't choke, and split under Telegram's 4096-char-per-message limit.
"""
from __future__ import annotations

import html

from telegram.constants import MessageLimit

from app.schemas import Report

DISCLAIMER = "This is not investment advice."
SAFE_LEN = MessageLimit.MAX_TEXT_LENGTH - 96  # stay safely under 4096

SENTIMENT = {"bullish": "🟢", "neutral": "⚪", "bearish": "🔴"}

e = html.escape


def render_report(report: Report) -> list[str]:
    """Return one or more Telegram-HTML messages for the report."""
    blocks: list[str] = [
        f"🌅 <b>{e(report.headline)}</b>\n<i>As of {e(report.as_of)}</i>",
    ]

    for s in report.sections:
        lines = [f"<b>{e(s.title)}</b> {SENTIMENT.get(s.sentiment, '')}".rstrip()]
        lines += [f"• {e(b)}" for b in s.bullets]
        blocks.append("\n".join(lines))

    if report.watchlist:
        lines = ["👀 <b>On the Radar</b>"]
        lines += [f"• <code>{e(w.symbol)}</code> — {e(w.reason)}" for w in report.watchlist]
        blocks.append("\n".join(lines))

    blocks.append(f"<i>{e(DISCLAIMER)}</i>")

    return _pack(blocks)


def _pack(blocks: list[str], limit: int = SAFE_LEN) -> list[str]:
    """Greedily join blocks with a blank line, starting a new message before the limit."""
    messages: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                messages.append(current)
            current = block[:limit]  # a single oversized block is truncated to fit
    if current:
        messages.append(current)
    return messages
