# Step 4: The report, in plain code

`/report` now produces a full morning briefing with a headline, a few sections, a watchlist,
and the disclaimer. It looks like the finished product, but it's built entirely in code with no
AI. The point is to nail down the shape of the report before we let agents write the words
inside it.

## Run it

```powershell
copy .env.example .env      # bot token + Sectors API key
uv sync
uv run python -m app.main
```

Send `/report` and a formatted briefing comes back, split across Telegram messages if it's
long.

## What's going on

`app/schemas.py` defines the shapes: `Section`, `Report`, `KeyNumber`, `WatchlistItem`. These
are the contract that code and (later) the AI both agree to fill in.

`app/render.py` turns a `Report` into Telegram HTML. Two jobs here that only code should ever
do: escaping `<`, `>` and `&` so Telegram's parser doesn't choke, and splitting the message so
it stays under Telegram's 4096-character limit. It also tacks on the disclaimer every single
time, no exceptions.

`app/report.py` is a data-only `build_report()`: fetch a couple of sections in parallel, do the
arithmetic in Python, hand back a `Report`. Read the docstring at the top, it explains that
step 7 rips out these insides and replaces them with a team of agents, while keeping this exact
output type and the way the handlers and renderer consume it. That's why we're pinning the
shape down now.

So the report to render to Telegram pipeline is fixed from here on. Everything that has to be
exact (the schema, the escaping, the splitting, the disclaimer) is settled in plain code. The
later steps only change who writes the words inside the sections.

Next, step 5 sends this report out automatically on a daily schedule.
