# Step 2: Subscribers

The single `bot.py` script becomes an `app/` package, and the bot starts remembering who's
subscribed. It keeps that list in SQLite, so it survives a restart. New commands: `/start`,
`/stop`, `/status`, `/whoami`.

## Run it

```powershell
copy .env.example .env      # paste your bot token
uv sync
uv run python -m app.main
```

Send `/start` to subscribe, `/status` to check. Now stop the bot, start it again, and send
`/status` once more. You're still subscribed, because it's in the `.db` file, not in memory.

## What's going on

`app/config.py` reads everything from `.env`. Nothing else in the codebase touches
`os.environ` directly, it all goes through `settings`. One place to look when you're wondering
where a value comes from.

`app/store.py` is the whole database layer, about 55 lines of plain SQLite. One table,
`subscribers`. One small thing worth noticing: `subscribe()` returns `True` only when the row
was actually new, which is how `/start` knows whether to say "now subscribed" or "already
subscribed".

`app/main.py` is the entry point. `build_app()` wires everything together, and `on_startup`
runs once when the bot comes online (creates the database, sets the `/` command menu).
`app/handlers.py` has one function per command.

Some of you might think, hey this is deliberately boring code. Boring is where exact and determinism goes. Plain code handles the things that have to be exact. Later steps add the other half, where we let Agents construct responses for us.

Next, step 3 makes its first real data call: `/ihsg` pulls the live Indonesian index.
