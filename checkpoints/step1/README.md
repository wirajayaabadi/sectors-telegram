# Step 1: Hello bot

The smallest bot that does anything. It replies to `/hello`, and it tells you your Telegram
user id with `/whoami`. No data, no AI, nothing clever. We just want proof the wiring works
before we build on top of it.

## Run it

```powershell
copy .env.example .env      # then paste your bot token from @BotFather
uv sync
uv run bot.py
```

In Telegram, message your bot `/hello` and it should reply. Send `/whoami` and it tells you
your user id. Hang onto that number, you'll need it for the admin commands in step 5.

## What's going on

A handler is just an async function that takes `update` (the message that came in) and
`context` (the bot itself). Every command in the finished bot, `/report` and `/ask` and the
rest, is one of these functions. This is the whole idea, everything else is more of it.

`run_polling()` means the bot keeps asking Telegram "anything new for me?" in a loop. It only
works while this process is running. Close the terminal and the bot goes quiet.

Next up, step 2 moves this single script into a proper `app/` package and starts remembering
who's subscribed.
