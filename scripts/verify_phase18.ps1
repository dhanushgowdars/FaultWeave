$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$webRoot = Join-Path $projectRoot "web"

if (-not (Test-Path $pythonExe)) {
    throw "Python virtual environment was not found at $pythonExe"
}

Set-Location $projectRoot

$phase18PythonFiles = @(
    "services/gateway/app/demo_api.py",
    "services/gateway/app/demo_orchestration.py",
    "tests/test_phase17_demo_api.py",
    "tests/test_phase18_contract.py"
)

Write-Host "[1/6] Checking Phase 18 Python formatting..." -ForegroundColor Cyan
& $pythonExe -m ruff format --check @phase18PythonFiles
if ($LASTEXITCODE -ne 0) { throw "Python formatting check failed." }

Write-Host "[2/6] Running Phase 18 Python code-quality checks..." -ForegroundColor Cyan
& $pythonExe -m ruff check @phase18PythonFiles
if ($LASTEXITCODE -ne 0) { throw "Python code-quality check failed." }

Write-Host "[3/6] Running the complete backend test suite..." -ForegroundColor Cyan
& $pythonExe -m pytest
if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }

Set-Location $webRoot

Write-Host "[4/6] Installing the locked frontend dependencies..." -ForegroundColor Cyan
npm ci
if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed." }

Write-Host "[5/6] Running frontend lint and tests..." -ForegroundColor Cyan
npm run lint
if ($LASTEXITCODE -ne 0) { throw "Frontend lint failed." }
npm run test
if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed." }

Write-Host "[6/6] Building the production frontend..." -ForegroundColor Cyan
npm run build
if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }

Set-Location $projectRoot
git diff --check
if ($LASTEXITCODE -ne 0) { throw "Git whitespace validation failed." }

Write-Host "PASS: Phase 18 backend contracts and command-center source verified." -ForegroundColor Green
Write-Host "Next: rebuild Docker and complete the live six-scenario acceptance before committing." -ForegroundColor Yellow
