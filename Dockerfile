# A Dockerfile is a recipe for a portable, self-contained image that runs the bot
# identically on any host. We use uv's official image so `uv` + Python 3.14 are already there.
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

# 1) Install dependencies FIRST, from the lockfile, in their own layer.
#    Copying only the lock/pyproject means Docker re-uses this layer (cached) unless deps
#    change - so editing app code doesn't trigger a full reinstall on rebuild.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# 2) Now copy the application code.
COPY app ./app

# 3) Run the bot. `uv run` uses the environment uv just built.
#    This is a long-running process (polling + the in-process scheduler) - the container
#    must stay up 24/7 for the daily report to fire. See DEPLOY.md.
CMD ["uv", "run", "python", "-m", "app.main"]
