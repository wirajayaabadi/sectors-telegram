# Step 5: Schedule and broadcast

The same report now goes out on its own every morning to everyone who's subscribed. And
`/testjob` (admin only) fires the whole delivery pipeline at just you in about 10 seconds, so
you don't have to wait until 07:30 to see if it works.

## Run it

```powershell
copy .env.example .env      # bot token + Sectors key + ADMIN_CHAT_IDS (your user id from /whoami)
uv sync
uv run python -m app.main
```

Subscribe with `/start`, then send `/testjob`. About 10 seconds later the bot messages you the
report without being asked. That's the scheduled path working.

## What's going on

`app/jobs.py` is where delivery lives. `morning_job` builds the report once, then sends it to
every subscriber, pacing itself so it doesn't trip Telegram's flood limit and quietly dropping
anyone who's blocked the bot. `schedule_jobs` registers the daily run at `REPORT_TIME`.

The new `/testjob` handler is admin-only and just runs `morning_job` at you in a few seconds.
Handy for testing the real thing end to end.

Everything about delivery that has to be right (the pacing, the retry when Telegram says slow
down, dropping blocked users, sending to the correct chats) is plain code, none of it left to a
model.

One thing to be clear about: the bot only sends while `python -m app.main` is actually running.
Keeping it up 24/7 is a deployment problem, and that's what the root `DEPLOY.md` covers. It's
out of scope for these checkpoints.

Next, step 6 finally brings in the AI: one agent that answers free-form questions with `/ask`.
