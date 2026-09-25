# Step 7: The report team (the manager pattern)

`/report` is no longer plain code. It's a team of agents now: four analysts gather data in
parallel, an editor merges their work into one report, and a critic checks it against the
grounding rules in a revise loop before it goes out. The `Report` type and the whole render and
deliver pipeline from step 4 don't change at all. Only the insides of `build_report()` do.

## Run it

```powershell
copy .env.example .env      # bot token + Sectors key + GROQ_API_KEY
uv sync
uv run python -m app.main
```

Send `/report` and give it a minute. Watch the log: each analyst calls its tools, the editor
assembles a report, the critic scores it pass or needs-improvement or fail, and on anything but
pass the editor has another go with the critic's feedback. On the free Groq tier, set
`IS_PAID=false` and the analysts run one at a time with pacing so you don't blow the rate limit.

## What's going on

`app/sectors_agents/specialists.py` has the four analysts (index, movers, flow, news). Read the
docstring at the top first, because there's a real constraint hiding here: Groq can't do
tool-calling and structured output in the same request. So each analyst is actually two agents
chained together, a fetcher that uses tools and writes plain text, then a shared formatter with
no tools that turns that text into a `Section`. `run_structured()` retries when the model coughs
up bad JSON, which it occasionally does.

`app/sectors_agents/editor.py` merges the analysts' sections into one report.
`app/sectors_agents/critic.py` reviews that report and returns feedback with hard checks (no
invented numbers, tickers, or dates, no advice language) that never pass no matter what.

`app/report.py` is the manager. Read its docstring too. `build_report()` gathers the sections
(in parallel on the paid tier, paced on the free one), then runs the editor and critic back and
forth in a bounded loop.

This is the manager pattern: control stays inside `report.py` the whole time. It calls the
analysts, gets their sections back, hands them to the editor, checks with the critic, and puts
the final report together itself. It never hands control away. That editor-critic-feedback loop
is the "second agent fact-checks the first" idea, and it's what makes the output something you'd
actually trust.

Next, step 8 (the full app) turns `/ask` into a triage router that hands off to specialists,
adds a group-research pipeline, and puts a guardrail in front. That's the handoff pattern, which
is the interesting contrast to the manager you just built.
