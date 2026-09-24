# Getting your bot online

So you've got the bot running on your laptop, you can chat with it on your phone, and `/report` works. Nice.
But here's the thing: right now, our bot is only
alive while that terminal is open. The moment you close it, or your laptop sleeps, the
scheduled report stops happening.
It's just your machine, running a Python process.

That's the whole reason "deployment" exists for this project. We're not doing anything
fancy. We just need to move that same Python process onto a computer that never sleeps.
Everything in this note is in service of that one goal.

Fortunately, our bot uses _polling_; it reaches out to
Telegram to ask "any new messages?" rather than waiting for Telegram to call it. That means
we don't need a public web address, open ports, or any of the networking headaches that
usually come with putting something online. We just need the process to keep running.

## What we're actually shipping

Two things travel to the host, and I want you to keep them separate in your head:

**The code.** We package it into something called a Docker image — think of it as a sealed
box (or more often called as a container) that contains your code _and_ the exact Python and libraries it needs, so it runs the
same way everywhere. No more "works on my machine." The `Dockerfile` in this repo is the
recipe for that container.

**The secrets.** Your bot token, your Sectors key, your Groq key. These do **not** go inside
the container, and they never, ever get committed to git. Instead, you paste them into the hosting
platform's dashboard as "environment variables," and they get handed to the bot when it
starts. I've set up `.dockerignore` and `.gitignore` so your real `.env` file physically
cannot end up in the image or the repo .

## Try the box on your own machine first

Before you touch any hosting platform, prove the container works locally. If Docker's installed:

```bash
docker build -t sectors-bot .
docker run --rm --env-file .env sectors-bot
```

If it logs "online" and `/report` works in Telegram, you're done here.
Any host will run the exact same container.

## Where to actually host it

I'll give you 2 most common options, easiest first. I'd personally recommend **Railway**,
easiest and arguably cheapest. Pick whichever fits you; they all just run the container 24/7.

**Railway.** Connect your GitHub repo, and it notices the `Dockerfile` and builds it for you.
Drop your secrets into the Variables tab. It keeps the thing running. That's basically it.
We can keep the bot running on the "free" tier that costs 1 dollar per month.

**A VPS (a cloud server you SSH into).** Most control, most manual work. You install
Docker yourself, clone the repo, make your `.env`, then you would typically do something like:

```bash
docker run -d --restart=unless-stopped --env-file .env -v /srv/botdata:/app/data sectors-bot
```

The flags matter: `-d` runs it in the background, `--restart=unless-stopped` brings it back
if it crashes or the server reboots, and `-v` keeps your database alive (more on that next).
Use a VPS if you already have one or you want to understand what the managed platforms are
doing for you under the hood.

## The gotcha (Persistency Notes)

Here's the one I really want you to remember. Your bot keeps its subscriber list and chat
memory in a little SQLite file at `data/bot.db`. But a container's storage is _ephemeral_ —
on most hosting platforms, every time you redeploy, the disk gets wiped and starts fresh.
Which means your subscriber list would reset to empty, and thus no one would receive any scheduled report,
unless they re-subscribe.

Three ways to deal with it:

1. **Just accept it** if you're totally building it for yourself. Doing a `/start` is not harmful.
2. **Attach a "volume"**. Railway let you mount a bit of permanent storage
   at `/app/data` so the database survives redeploys. On a VPS that's the `-v` flag above.
3. **Move to a proper database** (Postgres) when you're serious. Remember how we said memory
   is just an _interface_? This is where that pays off. you swap the storage backend without
   rewriting a single agent.

## Last thing — what going live does _not_ do

Being online 24/7 doesn't make the bot smarter or more reliable. The free-tier rate limits,
the occasional dropped section from a flaky model, the grounding rules; all of that is the
same as it was on your laptop. Deployment only keeps the lights on so the schedule fires and
people can reach it any time. The _quality_ of what it produces is handled back in the code —
the graceful degradation, the critic, the grounding — not by where it runs.

If you'd wish to take it a step further and improve the workflow, the agents, or even connect to
your preferred model, you're more than welcome to explore!

Calvin
