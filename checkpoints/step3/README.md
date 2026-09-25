# Step 3: First data

The bot makes its first call to the [Sectors](https://sectors.app) data API. `/ihsg` pulls the
live Indonesian composite index and shows the level plus the daily change. Still no AI, this is
just the data layer going in.

## Run it

```powershell
copy .env.example .env      # paste your bot token AND your Sectors API key
uv sync
uv run python -m app.main
```

Send `/ihsg` and you'll get the latest IHSG level, its change, and the date the data is from.

## What's going on

`app/sectors.py` is the API client. The `ENDPOINTS` dict at the top maps a
`(market, name)` pair to a URL template, so adding a new data source is just adding a line
there. `get()` handles the annoying parts: it retries with a backoff when the API returns a
429 or a 5xx, and it turns HTTP errors into a readable `SectorsError` with a hint about what
went wrong. `get_client()` hands back a single shared client for the whole process.

The `ihsg` handler is worth reading closely. It computes the change in Python
(`price - prev_price`), formats it, and replies. No model is involved in the arithmetic, and
that's on purpose.

You now have a genuinely useful bot with zero AI in it. Everything so far is the "plain code
handles what has to be exact" half. The robustness in `sectors.py` matters more than it looks
right now, because in step 7 the agents will be hammering this same client dozens of times per
report.

Next, step 4 builds a whole multi-section `/report`, still by plain code, so you can see the
shape of the finished product before the agents fill it in.
