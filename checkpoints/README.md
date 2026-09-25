# Checkpoints

Each `stepN/` folder is a complete, runnable snapshot of the bot at the end of that step. If
you fall behind, or you just want to jump ahead and poke at a later version, copy the folder,
add your keys, and run it:

```powershell
copy .env.example .env      # paste the keys that step needs
uv sync                     # installs only what that step depends on
uv run <the command in that step's README>
```

Every step has its own README that tells you what to run, what got added, and what's worth
paying attention to. Read them in order, because each one builds directly on the one before it.

## The steps

| Step | Title | What it adds | Keys | Run |
|------|-------|--------------|------|-----|
| [1](step1/) | Hello bot | a `/hello` handler | Telegram | `uv run bot.py` |
| [2](step2/) | Subscribers | `app/` package, SQLite store, `/start /stop /status` | Telegram | `uv run python -m app.main` |
| [3](step3/) | First data | Sectors API client, `/ihsg` | + Sectors | `uv run python -m app.main` |
| [4](step4/) | The report, in plain code | schemas, render, a code-built `/report` | + Sectors | `uv run python -m app.main` |
| [5](step5/) | Schedule and broadcast | daily job, `/testjob` | + Sectors | `uv run python -m app.main` |
| [6](step6/) | Your first agent | provider, tools, one agent, `/ask` | + Groq | `uv run python -m app.main` |
| [7](step7/) | The report team | analysts, editor, critic (the manager pattern) | + Groq | `uv run python -m app.main` |
| [8](step8/) | The full app | triage, specialists, research, guardrail (the handoff pattern) | + Groq | `uv run python -m app.main` |

Step 8 is the same as the project root. The root `README.md`, `GUIDE.md`, and `DEPLOY.md` have
the fuller picture.

## The idea running through all of it

Let the AI decide what to say, and let plain code handle everything that has to be exactly
right. You'll see that split turn up step after step. Subscriptions and the `/ihsg` math (steps
2 and 3) are plain code. The tools do the arithmetic (step 6). The critic fact-checks the
writers (step 7). Rendering escapes and splits messages on its own (step 4). The AI only ever
writes the words.

And two patterns for wiring agents together, which you can compare directly by the end:

- The manager (the report in step 7, research in step 8): the coordinator calls its workers,
  gets the results back, and assembles the answer itself. It keeps control.
- The handoff (triage in step 8): the router picks one specialist and steps aside, and that
  specialist answers the user directly. Control moves away.
