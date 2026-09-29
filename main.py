"""Goowac MC Monitor: conservative Minecraft Java status monitoring."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mcstatus import JavaServer

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("goowac")

SERVERS_FILE = Path(os.getenv("SERVERS_FILE", "servers.json"))
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
SCAN_INTERVAL = max(60, int(os.getenv("SCAN_INTERVAL_SECONDS", "300")))
PING_TIMEOUT = max(2, min(15, int(os.getenv("PING_TIMEOUT_SECONDS", "5"))))
USER_AGENT = "Goowac-MC-Monitor/1.0"

health: dict[str, Any] = {
    "ok": True,
    "service": "goowac-mc-monitor",
    "last_scan": None,
    "last_scan_duration_ms": None,
    "checked": 0,
    "online": 0,
    "offline": 0,
    "error": None,
}
previous_states: dict[str, bool] = {}


@dataclass(frozen=True)
class ServerTarget:
    name: str
    host: str
    port: int = 25565


def load_servers() -> list[ServerTarget]:
    """Load only explicitly configured targets; never generate or discover IPs."""
    try:
        raw = json.loads(SERVERS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning("No %s found; no servers configured.", SERVERS_FILE)
        return []
    except json.JSONDecodeError as exc:
        raise ValueError(f"{SERVERS_FILE} is not valid JSON: {exc}") from exc

    if not isinstance(raw, list):
        raise ValueError(f"{SERVERS_FILE} must contain a JSON list.")

    targets: list[ServerTarget] = []
    seen: set[tuple[str, int]] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            log.warning("Skipping entry %d: expected an object.", index + 1)
            continue
        host = str(item.get("host", "")).strip()
        if not host or host.startswith("-") or any(c.isspace() for c in host):
            log.warning("Skipping entry %d: host is missing or invalid.", index + 1)
            continue
        try:
            port = int(item.get("port", 25565))
        except (TypeError, ValueError):
            log.warning("Skipping %s: invalid port.", host)
            continue
        if not 1 <= port <= 65535:
            log.warning("Skipping %s: port must be between 1 and 65535.", host)
            continue
        key = (host.lower(), port)
        if key in seen:
            log.info("Skipping duplicate target %s:%d.", host, port)
            continue
        seen.add(key)
        targets.append(ServerTarget(str(item.get("name") or host)[:100], host, port))
    return targets


def send_discord(title: str, description: str, color: int) -> None:
    """Post a small Discord embed; webhook URL must be supplied as an environment secret."""
    if not WEBHOOK_URL:
        log.debug("DISCORD_WEBHOOK_URL not configured; notification skipped.")
        return
    payload = {
        "embeds": [{
            "title": title[:256],
            "description": description[:4000],
            "color": color,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "footer": {"text": "Goowac MC Monitor"},
        }]
    }
    request = Request(
        WEBHOOK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            if response.status >= 300:
                log.warning("Discord webhook returned HTTP %s.", response.status)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        log.warning("Discord notification failed: %s", exc)


def check_server(target: ServerTarget) -> dict[str, Any]:
    """Perform a Minecraft Java status ping, not a login or authentication test."""
    started = time.monotonic()
    result: dict[str, Any] = {
        "name": target.name,
        "host": target.host,
        "port": target.port,
        "online": False,
        "latency_ms": None,
        "version": None,
        "protocol": None,
        "players_online": None,
        "players_max": None,
        "motd": None,
        "whitelist_status": "unknown",
        "cracked_status": "unknown",
        "error": None,
    }
    try:
        status = JavaServer(target.host, target.port, timeout=PING_TIMEOUT).status()
        description = status.description
        if isinstance(description, str):
            motd = description
        elif isinstance(description, dict):
            motd = str(description.get("text", ""))
        else:
            motd = str(description)
        result.update({
            "online": True,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "version": getattr(status.version, "name", None),
            "protocol": getattr(status.version, "protocol", None),
            "players_online": getattr(status.players, "online", None),
            "players_max": getattr(status.players, "max", None),
            "motd": motd[:300],
        })
    except Exception as exc:
        result["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
        result["error"] = str(exc)[:250]
    return result


def describe_result(result: dict[str, Any]) -> str:
    endpoint = f'{result["host"]}:{result["port"]}'
    if result["online"]:
        online = result["players_online"]
        maximum = result["players_max"]
        players = f"{online}/{maximum}" if online is not None and maximum is not None else "not advertised"
        return (
            f'**{result["name"]}** (`{endpoint}`)\n'
            f'**Status:** Online\n'
            f'**Version:** `{result["version"] or "not advertised"}` '
            f'(protocol `{result["protocol"] if result["protocol"] is not None else "unknown"}`)\n'
            f'**Players:** `{players}` · **Latency:** `{result["latency_ms"]} ms`\n'
            f'**Whitelist:** `unknown` · **Cracked/offline-mode:** `unknown`\n'
            f'**MOTD:** {result["motd"] or "not advertised"}'
        )
    return (
        f'**{result["name"]}** (`{endpoint}`)\n'
        f'**Status:** No successful status response\n'
        f'**Whitelist:** `unknown` · **Cracked/offline-mode:** `unknown`\n'
        f'**Details:** `{result["error"] or "No response"}`'
    )


def scan_once() -> list[dict[str, Any]]:
    targets = load_servers()
    started = time.monotonic()
    health["error"] = None
    results = [check_server(target) for target in targets]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    online_count = sum(1 for result in results if result["online"])
    health.update({
        "last_scan": now,
        "last_scan_duration_ms": round((time.monotonic() - started) * 1000, 1),
        "checked": len(results),
        "online": online_count,
        "offline": len(results) - online_count,
    })

    for result in results:
        key = f'{result["host"].lower()}:{result["port"]}'
        current = bool(result["online"])
        previous = previous_states.get(key)
        if previous is None and current:
            send_discord("🟢 Minecraft server online", describe_result(result), 0x57F287)
        elif previous is not None and previous != current:
            send_discord(
                "🟢 Minecraft server is back online" if current else "🔴 Minecraft server went offline",
                describe_result(result),
                0x57F287 if current else 0xED4245,
            )
        previous_states[key] = current

    log.info(
        "Scan finished: checked=%d online=%d offline=%d duration_ms=%s",
        health["checked"], health["online"], health["offline"], health["last_scan_duration_ms"],
    )
    return results


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in ("/", "/health", "/status"):
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(health, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: Any) -> None:
        return


def start_health_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    Thread(target=server.serve_forever, daemon=True, name="health-server").start()
    log.info("Health endpoint listening on port %d.", port)


def main() -> None:
    start_health_server()
    while True:
        try:
            scan_once()
        except Exception as exc:
            health["error"] = str(exc)[:300]
            log.exception("Scan cycle failed.")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
