# Sectors Telegram Bot

A Telegram bot that writes a grounded morning report on the Indonesian (IDX) and Singapore
(SGX) stock markets, and answers ad-hoc market questions — built with a small team of AI
agents on top of the [Sectors](https://sectors.app) financial data API.

## A note from me

I built this as the worked example for a workshop on making AI agents _actually useful_ —
not a chatbot that makes things up, but one that pulls real market data through tools, does
its arithmetic in code (because language models are genuinely bad at big numbers), and has a
second agent fact-check the first before anything goes out.

The short version of the philosophy: **let the AI decide what to say, and let plain code
handle everything that has to be exactly right** -> the numbers, the formatting, the message
limits, the disclaimer. That one idea shows up everywhere in this repo, and it's the thing
I most want you to take away.

## What it does

- **`/report`** — a full morning report: four analyst agents gather data in parallel, an
  editor merges their work, and a critic checks it against the grounding rules before it's
  sent. (The "manager" pattern.)
- **`/ask` or just typing a question** — routes your question to the right specialist:
  market-wide, a specific IDX/SGX company, or a whole _group_ of companies (e.g. "Prajogo
  Pangestu's companies"). (The "handoff" pattern.)
- **Scheduled delivery** — the same report goes out automatically each morning to everyone
  who's subscribed.
- Other commands: `/ihsg`, `/status`, `/whoami`, `/stop`, `/reset`, and `/testjob` (admin).

## Running it

You'll need a Telegram bot token (from @BotFather), a Sectors API key, and a Groq API key.

```bash
cp .env.example .env      # then fill in your keys
uv sync
uv run python -m app.main
```

Message your bot `/report` and give it a minute.

## The pieces

- `app/tools.py` — the bridge between the agents and the Sectors API (each tool is a
  function the AI is allowed to call).
- `app/schemas.py` — the structured shapes the agents produce.
- `app/sectors_agents/` — the agents: analysts + editor + critic (the report), and
  triage + specialists + research pipeline (the Q&A).
- `app/report.py` — the manager that orchestrates the report.
- `app/render.py` — turns a report into Telegram-safe messages.
- `app/handlers.py`, `app/main.py` — the Telegram wiring.

## Going further

- `DEPLOY.md` — how to get the bot running online 24/7 (so the morning schedule fires).
