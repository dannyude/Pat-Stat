param(
    [string]$OutputDir = "backups"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".env")) {
    throw ".env file not found. Run from repository root."
}

if (-not (Get-Command pg_dump -ErrorAction SilentlyContinue)) {
    throw "pg_dump was not found in PATH. Install PostgreSQL client tools."
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

if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$filename = "patstat-$timestamp.dump"
$outPath = Join-Path $OutputDir $filename

Write-Host "Creating backup at $outPath"
pg_dump --format=custom --file "$outPath" "$databaseUrl"

if ($LASTEXITCODE -ne 0) {
    throw "pg_dump failed with exit code $LASTEXITCODE"
}

Write-Host "Backup complete: $outPath"
