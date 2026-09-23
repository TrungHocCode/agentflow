# AgentFlow

## Web crawler

`news_crawler` uses static HTTP fetching first. In `auto` mode it checks extracted
content quality and renders the page with Chromium only when the static result is
empty or low quality in a JavaScript app shell, or appears to be an access
challenge. The result includes the selected engine, quality score, warnings,
duration, and fetch attempts.

The tool accepts `mode="auto"` (default), `mode="static"`, or `mode="browser"`.
Browser rendering runs in the background worker, has a bounded timeout and
concurrency limit, and blocks image, media, font, and stylesheet downloads. It
does not solve CAPTCHAs or bypass authentication and anti-bot challenges.

For local development, install the project dependencies and then the matching
Chromium binary:

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

The Docker worker image installs Chromium during its build; the API image does not.
Browser fallback can be tuned with these environment variables:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `AGENTFLOW_CRAWLER_BROWSER_ENABLED` | `true` | Enable or disable browser fallback |
| `AGENTFLOW_CRAWLER_BROWSER_TIMEOUT_MS` | `18000` | Maximum navigation time |
| `AGENTFLOW_CRAWLER_BROWSER_SETTLE_MS` | `600` | Short wait for client-rendered content |
| `AGENTFLOW_CRAWLER_BROWSER_MAX_CONCURRENCY` | `1` | Maximum simultaneous browser pages per worker |
| `AGENTFLOW_CRAWLER_BROWSER_SLOT_TIMEOUT_SECONDS` | `2` | Maximum wait for a browser slot |
