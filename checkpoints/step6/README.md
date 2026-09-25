# Step 6: Your first agent

The AI shows up. `/ask` (or just typing a question in a private chat) goes to a single LLM
agent that answers by calling the Sectors tools. `/report` is still the plain-code path from
step 4, only the Q&A is AI for now.

## Run it

```powershell
copy .env.example .env      # bot token + Sectors key + GROQ_API_KEY
uv sync
uv run python -m app.main
```

Try `/ask How is IHSG doing this week?` and watch the terminal. You'll see the agent decide to
call a tool, read the JSON that comes back, and write an answer from it. `/reset` clears the
conversation memory.

## What's going on

`app/provider.py` points the Agents SDK at Groq, which speaks the same API as OpenAI. It hands
back `SMALL` and `LARGE` models, and `configure_llm()` gets called on startup.

`app/tools.py` wraps the Sectors endpoints as tools the agent is allowed to call. The important
thing here: the docstring is the prompt. That text is what the model reads to decide when and
how to call each tool. Look at how the market-cap tools pre-scale the number and throw away the
raw one, and how `change` does the percentage math itself. The code does the arithmetic, the
model just reports the result. This is the part I care about most, so it's worth slowing down on.

`app/sectors_agents/rules.py` holds `BASE_RULES`, the shared instructions every agent gets:
only use numbers from tools, never invent a date, describe don't advise.

`app/sectors_agents/chat.py` is one `market_agent` (instructions, tools, model settings, that's
it) and an `answer()` that runs it with a per-chat SQLite session so it remembers the
conversation.

An agent really is just instructions plus tools plus model settings. The model doesn't know
what IHSG did, it decides to call your function, reads the result, and writes it up. That's the
"let the AI decide what to say" half. But the numbers still come from the tools, never from the
model's memory.

Next, step 7 turns `/report` into a team: four analysts working in parallel, an editor, and a
critic that fact-checks the whole thing before it goes out.
