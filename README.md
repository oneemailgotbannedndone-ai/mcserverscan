# Goowac MC Monitor

A small Minecraft **Java Edition status monitor** with Discord alerts and a Render background-worker configuration.

> Use only with servers you own or have permission to monitor. This tool checks a fixed list from `servers.json`; it does not generate random IPs or scan public address ranges.

## Features

- Checks configured Minecraft Java server addresses on a sensible interval.
- Reports online/offline response, advertised version/protocol, player counts, MOTD, and approximate latency.
- Sends Discord webhook alerts when a server first appears online and when its state changes.
- Exposes a small JSON health endpoint at `/health` and `/status`.
- Validates entries and skips duplicate host/port pairs.
- Keeps secrets out of source control when configured correctly.

## Important detection limits

A normal Minecraft status ping does **not** reliably reveal whether a server is whitelisted or permits cracked/offline-mode clients. Those values deliberately remain `unknown`; the tool does not guess based on MOTD or software clues. A server that doesn't answer a status ping may be down, unreachable, filtering traffic, or using an unusual configuration. That is not proof of a whitelist.

This monitor does not attempt player login, authentication bypass, or whitelist evasion.

## Quick start

Requires Python 3.10 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Edit `servers.json` and replace the example host with server addresses you are authorized to monitor. Example:

```json
[
  {
    "name": "My server",
    "host": "mc.example.net",
    "port": 25565
  }
]
```

Set your Discord webhook URL as an environment variable. Never paste a real webhook into a public repository.

macOS/Linux:
```bash
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/REPLACE_ME"
python main.py
```

Windows PowerShell:
```powershell
$env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/REPLACE_ME"
python main.py
```

The health endpoint listens on port `10000` by default (or the `PORT` environment variable).

## Environment variables

| Variable | Default | Meaning |
|---|---:|---|
| `DISCORD_WEBHOOK_URL` | empty | Discord webhook secret; alerts are disabled if empty |
| `SCAN_INTERVAL_SECONDS` | `300` | Seconds between scans; minimum is 60 |
| `PING_TIMEOUT_SECONDS` | `5` | Timeout for each status ping, clamped to 2–15 seconds |
| `SERVERS_FILE` | `servers.json` | Path to server configuration |
| `PORT` | `10000` | Health endpoint port |
| `LOG_LEVEL` | `INFO` | Logging level |

The first scan only notifies about targets that are online, to avoid a burst of offline alerts on startup. Later scans notify on state transitions. The monitor doesn't repeatedly send an alert every cycle for a server that stays in the same state.

## Deploy to GitHub and Render

1. Connect this repository to Render or create a new Blueprint.
2. Review the included `render.yaml` and deploy the background worker.
3. In the Render service's Environment settings, add `DISCORD_WEBHOOK_URL` as a secret value.
4. Replace the example target in `servers.json` and redeploy after changes.

Render background workers run continuously while the service is active. A third-party ping service cannot guarantee that a sleeping, stopped, or failed worker will remain online. Check the host's current plan and service behavior before relying on it.

## Security checklist

- Do not commit `.env` files, webhook URLs, tokens, or credentials.
- If a webhook URL is accidentally exposed, delete/rotate it in Discord immediately.
- Keep the target list limited to servers you own or have permission to monitor.
- Use a reasonable scan interval; the default is five minutes.
