"""robots.txt rules and per-site pacing, so repeated monitoring stays polite."""

from __future__ import annotations

import os
import threading
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from app.execution.tools.network_policy import MAX_REDIRECTS, validate_redirect_target


USER_AGENT_TOKEN = "AgentFlowResearchCrawler"
HEADERS = {"User-Agent": f"Mozilla/5.0 {USER_AGENT_TOKEN}/1.0", "Accept": "text/plain"}
ROBOTS_TIMEOUT = (5, 10)
ROBOTS_TTL_SECONDS = 3600
MAX_ROBOTS_CHARS = 500_000  # RFC 9309 lets crawlers ignore rules past 500 KiB.
MIN_HOST_INTERVAL_SECONDS = 1.0
MAX_CRAWL_DELAY_SECONDS = 10.0
REDIRECT_STATUSES = {301, 302, 303, 307, 308}

_lock = threading.Lock()
_rules: dict[str, tuple[float, RobotFileParser | None]] = {}
_next_turn: dict[str, float] = {}


def _enabled() -> bool:
    return os.getenv("AGENTFLOW_CRAWLER_POLITENESS_ENABLED", "true").lower() not in {"0", "false", "no"}


def _fetch_rules(origin: str) -> RobotFileParser | None:
    """Return the site's rules, or None (allow all) when robots.txt is missing or unreadable.

    An unreachable robots.txt is treated as allow-all so a robots outage does not stop collection.
    """
    url = f"{origin}/robots.txt"
    for _ in range(MAX_REDIRECTS + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=ROBOTS_TIMEOUT, allow_redirects=False)
        except requests.RequestException:
            return None
        if response.status_code in REDIRECT_STATUSES:
            url, error = validate_redirect_target(url, response.headers.get("location", ""))
            if error or not url:
                return None
            continue
        if response.status_code != 200:
            return None
        parser = RobotFileParser()
        parser.parse(response.text[:MAX_ROBOTS_CHARS].splitlines())
        return parser
    return None


def _site_rules(origin: str) -> RobotFileParser | None:
    with _lock:
        cached = _rules.get(origin)
    if cached and time.monotonic() - cached[0] < ROBOTS_TTL_SECONDS:
        return cached[1]
    rules = _fetch_rules(origin)
    with _lock:
        _rules[origin] = (time.monotonic(), rules)
    return rules


def admit(url: str) -> bool:
    """Return False when robots.txt forbids the URL; otherwise wait for this site's next turn."""

    if not _enabled():
        return True
    parsed = urlparse(url)
    rules = _site_rules(f"{parsed.scheme}://{parsed.netloc}")
    if rules is not None and not rules.can_fetch(USER_AGENT_TOKEN, url):
        return False
    delay = (rules.crawl_delay(USER_AGENT_TOKEN) if rules is not None else None) or 0
    interval = max(MIN_HOST_INTERVAL_SECONDS, min(float(delay), MAX_CRAWL_DELAY_SECONDS))
    host = (parsed.hostname or "").lower()
    with _lock:
        now = time.monotonic()
        turn = max(now, _next_turn.get(host, 0.0))
        _next_turn[host] = turn + interval
    if turn > now:
        time.sleep(turn - now)
    return True


def reset() -> None:
    """Forget cached rules and turns (tests)."""

    with _lock:
        _rules.clear()
        _next_turn.clear()
