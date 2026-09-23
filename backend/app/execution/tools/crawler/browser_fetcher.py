"""Bounded Playwright rendering for pages that static HTML fetch cannot read."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from time import perf_counter
from urllib.parse import urlparse

from app.execution.tools.network_policy import MAX_REDIRECTS, validate_external_url


MAX_RESPONSE_BYTES = 5 * 1024 * 1024
BLOCKED_RESOURCE_TYPES = {"font", "image", "media", "stylesheet"}


def _positive_int_env(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(max(value, 1), maximum)


_MAX_CONCURRENCY = _positive_int_env("AGENTFLOW_CRAWLER_BROWSER_MAX_CONCURRENCY", 1, 4)
_BROWSER_SLOTS = threading.BoundedSemaphore(_MAX_CONCURRENCY)


@dataclass(frozen=True)
class RenderedPage:
    html: str
    final_url: str
    status_code: int | None
    duration_ms: int
    warnings: tuple[str, ...] = ()


class BrowserFetchError(RuntimeError):
    """A safe, user-facing browser-rendering failure."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def render_page(url: str) -> RenderedPage:
    """Render one page with Chromium while applying outbound URL safeguards."""

    if os.getenv("AGENTFLOW_CRAWLER_BROWSER_ENABLED", "true").lower() in {"0", "false", "no"}:
        raise BrowserFetchError("browser_disabled", "Browser fallback is disabled by configuration.")

    safe_url, validation_error = validate_external_url(url)
    if validation_error or not safe_url:
        raise BrowserFetchError("outbound_url_blocked", validation_error or "URL is not allowed.")

    timeout_ms = _positive_int_env("AGENTFLOW_CRAWLER_BROWSER_TIMEOUT_MS", 18000, 30000)
    settle_ms = _positive_int_env("AGENTFLOW_CRAWLER_BROWSER_SETTLE_MS", 600, 2000)
    slot_timeout = _positive_int_env("AGENTFLOW_CRAWLER_BROWSER_SLOT_TIMEOUT_SECONDS", 2, 10)
    started = perf_counter()

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserFetchError(
            "browser_dependency_missing",
            "Playwright is not installed; install the project requirements to enable browser fallback.",
        ) from exc

    if not _BROWSER_SLOTS.acquire(timeout=slot_timeout):
        raise BrowserFetchError(
            "browser_capacity_reached",
            "The browser renderer is busy; retry the crawl later.",
            retryable=True,
        )

    warnings: list[str] = []
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True)
            except Exception as exc:
                raise BrowserFetchError(
                    "browser_runtime_unavailable",
                    "Chromium is unavailable. Install the Playwright Chromium browser before using browser fallback.",
                ) from exc

            try:
                context = browser.new_context(
                    user_agent="Mozilla/5.0 AgentFlowResearchCrawler/1.0",
                    accept_downloads=False,
                    java_script_enabled=True,
                )
                page = context.new_page()
                validated_hosts: dict[tuple[str, str, int | None], bool] = {}
                blocked_request_count = 0

                def guard_outbound_request(route: object) -> None:
                    nonlocal blocked_request_count
                    request = route.request  # type: ignore[attr-defined]
                    if request.resource_type in BLOCKED_RESOURCE_TYPES:
                        route.abort()  # type: ignore[attr-defined]
                        return

                    parsed = urlparse(request.url)
                    if parsed.scheme in {"data", "blob"}:
                        route.continue_()  # type: ignore[attr-defined]
                        return
                    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
                        blocked_request_count += 1
                        route.abort()  # type: ignore[attr-defined]
                        return

                    try:
                        port = parsed.port
                    except ValueError:
                        blocked_request_count += 1
                        route.abort()  # type: ignore[attr-defined]
                        return
                    host_key = (parsed.scheme, (parsed.hostname or "").lower(), port)
                    is_allowed = validated_hosts.get(host_key)
                    if is_allowed is None:
                        _, error = validate_external_url(request.url)
                        is_allowed = error is None
                        validated_hosts[host_key] = is_allowed
                    if not is_allowed:
                        blocked_request_count += 1
                        route.abort()  # type: ignore[attr-defined]
                        return

                    if request.is_navigation_request():
                        redirect_count = 0
                        previous = request.redirected_from
                        while previous is not None:
                            redirect_count += 1
                            previous = previous.redirected_from
                        if redirect_count > MAX_REDIRECTS:
                            blocked_request_count += 1
                            route.abort()  # type: ignore[attr-defined]
                            return
                    route.continue_()  # type: ignore[attr-defined]

                page.route("**/*", guard_outbound_request)
                response = None
                try:
                    response = page.goto(
                        safe_url,
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                except PlaywrightTimeoutError:
                    warnings.append("Browser navigation reached its time limit; extracting the rendered page state.")
                except Exception as exc:
                    if blocked_request_count:
                        raise BrowserFetchError(
                            "browser_outbound_request_blocked",
                            "Browser navigation was stopped by the outbound URL policy.",
                        ) from exc
                    raise

                if blocked_request_count:
                    warnings.append(
                        f"Blocked {blocked_request_count} browser request(s) by the outbound URL policy."
                    )

                page.wait_for_timeout(settle_ms)
                final_url = page.url or safe_url
                final_url, final_url_error = validate_external_url(final_url)
                if final_url_error or not final_url:
                    raise BrowserFetchError(
                        "browser_final_url_blocked",
                        final_url_error or "Browser navigated to a blocked URL.",
                    )

                html = page.content()
                if len(html.encode("utf-8", errors="ignore")) > MAX_RESPONSE_BYTES:
                    raise BrowserFetchError(
                        "browser_response_too_large",
                        f"Rendered HTML exceeds the {MAX_RESPONSE_BYTES} byte limit.",
                    )
                return RenderedPage(
                    html=html,
                    final_url=final_url,
                    status_code=response.status if response is not None else None,
                    duration_ms=round((perf_counter() - started) * 1000),
                    warnings=tuple(warnings),
                )
            finally:
                browser.close()
    except BrowserFetchError:
        raise
    except Exception as exc:
        raise BrowserFetchError(
            "browser_fetch_failed",
            f"Browser rendering failed: {exc}",
            retryable=True,
        ) from exc
    finally:
        _BROWSER_SLOTS.release()
