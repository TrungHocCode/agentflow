# Test suites

Unit tests: `python scripts/run_tests.py unit` from the repository root.
The runner uses a temporary working directory, test adapters, deterministic DNS
and blocks unmocked Requests/HTTPX transports. Attempted HTTP fails the suite even
if application code catches the exception. This is test isolation, not an OS sandbox.
Live Ollama tests stay opt-in and are not part of CI's unit suite.

Real integration uses separate PostgreSQL/Redis services, not the application's volumes:

```powershell
docker compose -p agentflow-phase0-test -f compose.test.yml up -d --wait
$env:AGENTFLOW_INTEGRATION_TESTS = '1'
$env:TESTING = 'false'
$env:POSTGRES_URL = 'postgresql+asyncpg://agentflow_test:test_only_password@127.0.0.1:55432/agentflow_test'
$env:REDIS_URL = 'redis://127.0.0.1:56379/15'
python scripts/run_tests.py integration
docker compose -p agentflow-phase0-test -f compose.test.yml down -v
```

Use a dedicated terminal; close it after running to discard these environment overrides.
On Linux export the same variables. The integration runner fails rather than skips
when TESTING is enabled, configuration is missing/unsafe, or infrastructure is absent.
Only loopback ports 55432/56379 and database names agentflow_test/15 are accepted.
Migrations/seeds are applied to that disposable database, never inferred from `.env`.
Cleanup removes fixture-owned rows/queue keys; `down -v` removes only this test project's volumes.

Coverage: fresh/repeated migration, persisted workflow version, atomic run claim,
ownership-filtered reads, cursor event replay, real Redis delivery, unauthenticated
API rejection. Not yet covered: worker crash recovery, DB upgrades with old business
data, full authenticated API E2E, browser recovery, live research quality/load.

CI runs unit, integration and frontend lint/build separately.
Architecture documents and generated evaluation records remain private/ignored.
