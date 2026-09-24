# Sectors Telegram Bot

A Telegram bot that writes a grounded morning briefing on the Indonesian (IDX) and
Singapore (SGX) stock markets, and answers ad-hoc market questions on demand. It runs on a
small team of AI agents built on top of the [Sectors](https://sectors.app) financial data
API.

## A note from me

I built this as the worked example for a workshop on making AI agents _actually useful_.
Not a chatbot that makes things up, but one that pulls real market data through tools, does
its arithmetic in code (because language models are genuinely bad at big numbers), and has a
second agent fact-check the first before anything reaches you.

If there's one idea I want you to take away, it's this: **let the AI decide what to say, and
let plain code handle everything that has to be exactly right.** The numbers, the
formatting, the message limits, the disclaimer. You'll see that split show up again and
again once you start reading the code, and it's the difference between a demo and something
you'd actually trust.

Have fun with it, and don't be shy about pulling it apart to see how it works.

Calvin

## What it does

- **`/report`** gives you a full morning briefing. Four analyst agents gather data in
  parallel, an editor merges their work into one report, and a critic checks it against the
  grounding rules before it goes out. This is the "manager" pattern: one coordinator runs
  the workers and assembles the result.
- **`/ask`, or just typing a question,** routes you to the right specialist: market-wide,
  one specific IDX or SGX company, or a whole group of companies (try "how are Prajogo
  Pangestu's companies doing?"). This is the "handoff" pattern: a triage agent decides who
  should answer, then steps aside.
- **Scheduled delivery** sends the same morning report automatically to everyone who has
  subscribed.
- A few more: `/ihsg`, `/status`, `/whoami`, `/stop`, `/reset`, and `/testjob` (admin only).

## Running it locally

You'll need three keys: a Telegram bot token from @BotFather, a Sectors API key, and a Groq
API key.

```bash
cp .env.example .env      # then paste your keys in
uv sync
uv run python -m app.main
```

Message your bot `/report` and give it a minute to think.

## How the code is laid out

- `app/tools.py` is the bridge between the agents and the Sectors API. Each tool is just a
  function the AI is allowed to call.
- `app/schemas.py` defines the structured shapes the agents produce.
- `app/sectors_agents/` holds the agents themselves: analysts, editor, and critic for the
  report; triage, specialists, and the research pipeline for the Q&A.
- `app/report.py` is the manager that orchestrates the morning report.
- `app/render.py` turns a finished report into Telegram-safe messages.
- `app/handlers.py` and `app/main.py` are the Telegram wiring.

## Going further

- `DEPLOY.md` walks through getting the bot running online 24/7, so the morning schedule
  fires even when your laptop is closed.
