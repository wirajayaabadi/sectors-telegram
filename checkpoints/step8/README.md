# Step 8: The full app (the handoff pattern and guardrails)

The finished bot. `/ask` (or free text) now runs through a triage agent that hands off to the
right specialist. A group-research pipeline answers questions about several companies at once.
And a guardrail up front blocks off-topic or personal-advice questions before any of that runs.
This checkpoint is the same as the project root.

## Run it

```powershell
copy .env.example .env      # bot token + Sectors key + GROQ_API_KEY (+ ADMIN_CHAT_IDS)
uv sync
uv run python -m app.main
```

A few things to try:

- `How is IHSG doing?` goes to the Market Agent.
- `Tell me about BBCA` goes to the IDX Company Agent.
- `How are Prajogo Pangestu's companies doing?` goes to the Research Agent, which screens the
  group, researches each one, and synthesizes a single report.
- `Should I buy GOTO?` gets blocked by the guardrail.

## What's going on

`app/sectors_agents/chat.py` now has the full set of Q&A specialists: market, IDX company, SGX
company, and a research agent that exposes the research pipeline as a tool.

`app/sectors_agents/triage.py` is the router. The `triage_agent` has a list of handoffs and its
whole job is to pick one specialist and hand the conversation over.

`app/sectors_agents/guardrail.py` is a small classifier agent that trips a wire when a question
is off-topic or asks for personal advice. Notice the split even here: the AI does the
classifying, but a plain boolean does the blocking.

`app/sectors_agents/research.py` is a second manager. It screens for companies, researches each
one in parallel, and synthesizes them into a single report. It's tucked behind a handoff via the
`research_group` tool.

## The two patterns, side by side

This is the payoff of the whole build:

- The handoff (triage): the router picks a specialist and steps aside, and that specialist
  answers the user directly. Control moves away.
- The manager (the report in step 7, and research here): the coordinator calls its sub-agents,
  gets their results back, and assembles the answer itself. Control stays put.

And under both of them, the same idea you've been watching since step 2: the AI decides what to
say, and plain code guarantees the things that have to be exact. The numbers from tools, the
schema, the escaping, the message splitting, the disclaimer, the rate-limit pacing.

## Going further

The root `README.md` and `DEPLOY.md` cover running this online 24/7 so the morning schedule
fires even with your laptop closed.
