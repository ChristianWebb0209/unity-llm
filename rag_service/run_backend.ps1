Param(
  [string]$ListenHost = "0.0.0.0",
  [int]$ListenPort = 8001,
  [switch]$Reload
)

$ErrorActionPreference = "Stop"

# Go to script dir (rag_service)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host "[run_backend] Starting in $scriptDir"

# -----------------------------
# Load env files:
# 1) repo root .env (shared defaults)
# 2) rag_service/.env (optional local overrides)
# -----------------------------
$rootEnvFile = [System.IO.Path]::GetFullPath("$scriptDir\..\.env")
$serviceEnvFile = Join-Path $scriptDir ".env"

if (Test-Path $rootEnvFile) {
  Get-Content $rootEnvFile | ForEach-Object {
    if ($_ -match "^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$") {
      $key   = $matches[1]
      $value = $matches[2].Trim().Trim('"').Trim("'")
      [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
  }
}

if (Test-Path $serviceEnvFile) {
  Get-Content $serviceEnvFile | ForEach-Object {
    if ($_ -match "^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$") {
      $key   = $matches[1]
      $value = $matches[2].Trim().Trim('"').Trim("'")
      # service-local overrides shared root value
      [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
  }
}

if (-not $env:OPENAI_API_KEY) {
  Write-Error "OPENAI_API_KEY missing from .env"
  exit 1
}

# -----------------------------
# Use venv python (prefer rag_service/.venv, then repo root .venv)
# -----------------------------
$pythonLocal = [System.IO.Path]::GetFullPath("$scriptDir\.venv\Scripts\python.exe")
$pythonRoot = [System.IO.Path]::GetFullPath("$scriptDir\..\\.venv\Scripts\python.exe")
$python = $pythonLocal

if (!(Test-Path $python)) {
  $python = $pythonRoot
}

if (!(Test-Path $python)) {
  Write-Host "[run_backend] venv python not found at $pythonLocal or $pythonRoot; falling back to python on PATH"
  $python = "python"
}

# -----------------------------
# Run
# -----------------------------
$uvicornArgs = @(
  "-m", "uvicorn", "app.main:app",
  "--host", $ListenHost,
  "--port", $ListenPort,
  "--log-level", "info"
)

if ($Reload) {
  $uvicornArgs += "--reload"
}

& $python @uvicornArgs