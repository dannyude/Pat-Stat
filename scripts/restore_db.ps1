param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $BackupFile)) {
    throw "Backup file not found: $BackupFile"
}

if (-not (Test-Path ".env")) {
    throw ".env file not found. Run from repository root."
}

if (-not (Get-Command pg_restore -ErrorAction SilentlyContinue)) {
    throw "pg_restore was not found in PATH. Install PostgreSQL client tools."
}

Get-Content ".env" | ForEach-Object {
    if ($_ -match "^\s*#") { return }
    if ($_ -match "^\s*$") { return }
    $pair = $_ -split "=", 2
    if ($pair.Length -eq 2) {
        [Environment]::SetEnvironmentVariable($pair[0], $pair[1], "Process")
    }
}

$databaseUrl = $env:DATABASE_URL_SYNC
if (-not $databaseUrl) {
    throw "DATABASE_URL_SYNC not found in .env"
}

Write-Host "Restoring backup from $BackupFile"
pg_restore --clean --if-exists --no-owner --no-privileges --dbname "$databaseUrl" "$BackupFile"

if ($LASTEXITCODE -ne 0) {
    throw "pg_restore failed with exit code $LASTEXITCODE"
}

Write-Host "Restore complete"
