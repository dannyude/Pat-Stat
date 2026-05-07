<#
.SYNOPSIS
    Run the PatStat test suite inside the patstat_api Docker container.

.DESCRIPTION
    Wraps the docker exec call with the test-database env vars so you don't
    have to remember the escaping every time. Forwards any extra arguments
    to pytest via the automatic $args array — that means pytest flags like
    --no-header, --tb=short, -k, -v, etc. all pass through cleanly.

    The conftest.py guard refuses to run unless DATABASE_URL contains 'test',
    so this script is the safe way to invoke pytest on a developer machine.

.EXAMPLE
    .\scripts\test.ps1
    Runs the full suite.

.EXAMPLE
    .\scripts\test.ps1 tests/test_notification_policy.py -v
    Runs only the policy tests, verbose.

.EXAMPLE
    .\scripts\test.ps1 -k "dispatch" --tb=short
    Runs only test names matching "dispatch".

.NOTES
    Requires:
      • Docker Desktop running
      • patstat_api container running (`docker compose up -d`)
      • patstat_test_db database already created
            One-time setup:
              docker exec patstat-db psql -U postgres -c "CREATE DATABASE patstat_test_db;"
#>

# NOTE: no `param` block on purpose — that lets us use $args (auto-variable)
# to forward EVERY argument verbatim to pytest. With a param block, PowerShell
# tries to match flags like `--no-header` against script parameters and errors.

$ErrorActionPreference = "Stop"

# Sanity-check the container is up; emit a clear message if not.
# We swallow stderr here on purpose — if Docker Desktop is stopped the
# `docker inspect` call writes a noisy "pipe not found" error before we
# can report it cleanly.
$apiState = docker inspect -f '{{.State.Running}}' patstat_api 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker isn't reachable." -ForegroundColor Red
    Write-Host "  Start Docker Desktop, wait for it to be ready, then run:" -ForegroundColor Yellow
    Write-Host "    docker compose up -d" -ForegroundColor Yellow
    Write-Host "  Then re-run this script." -ForegroundColor Yellow
    exit 1
}
if ($apiState -ne "true") {
    Write-Host "patstat_api container is not running." -ForegroundColor Red
    Write-Host "  Bring the stack up first:" -ForegroundColor Yellow
    Write-Host "    docker compose up -d" -ForegroundColor Yellow
    exit 1
}

# Test-DB env vars — required by tests/conftest.py:_TEST_DB_MARKERS guard.
$dbUrlAsync = 'postgresql+asyncpg://postgres:Dannyude1Ad$@postgres:5432/patstat_test_db'
$dbUrlSync  = 'postgresql://postgres:Dannyude1Ad$@postgres:5432/patstat_test_db'

# `$args` is PowerShell's automatic variable holding every unbound argument.
# When this array is empty, pytest runs the full suite (its default behaviour).
docker exec `
    -e "DATABASE_URL=$dbUrlAsync" `
    -e "DATABASE_URL_SYNC=$dbUrlSync" `
    patstat_api pytest @args

exit $LASTEXITCODE
