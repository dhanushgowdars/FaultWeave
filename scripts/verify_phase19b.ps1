$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$pythonExe = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "FaultWeave virtual environment was not found at $pythonExe"
}

$phaseFiles = @(
    "services/gateway/app/demo_api.py",
    "services/gateway/app/demo_orchestration.py",
    "services/gateway/app/demo_workload.py",
    "scripts/verify_phase19b.py",
    "tests/test_demo_workload.py",
    "tests/test_phase19a_blind_challenge.py"
)

Write-Host "[1/7] Checking Phase 19B formatting..." -ForegroundColor Cyan
& $pythonExe -m ruff format --check @phaseFiles
if ($LASTEXITCODE -ne 0) { throw "Phase 19B formatting check failed." }

Write-Host "`n[2/7] Running Phase 19B code-quality checks..." -ForegroundColor Cyan
& $pythonExe -m ruff check @phaseFiles
if ($LASTEXITCODE -ne 0) { throw "Phase 19B code-quality check failed." }

Write-Host "`n[3/7] Running the complete backend test suite..." -ForegroundColor Cyan
& $pythonExe -m pytest
if ($LASTEXITCODE -ne 0) { throw "Backend test suite failed." }

Write-Host "`n[4/7] Rebuilding the model-ready gateway..." -ForegroundColor Cyan
docker compose build gateway
if ($LASTEXITCODE -ne 0) { throw "Gateway build failed." }

Write-Host "`n[5/7] Starting and checking the complete backend..." -ForegroundColor Cyan
docker compose up -d --wait --wait-timeout 180
if ($LASTEXITCODE -ne 0) {
    docker compose ps
    throw "Docker services did not become healthy."
}
docker compose ps

Write-Host "`n[6/7] Running the sustained sealed-model acceptance..." -ForegroundColor Cyan
Write-Host "This takes approximately 70-90 seconds. Do not interrupt the controlled run." -ForegroundColor Yellow
& $pythonExe -m scripts.verify_phase19b
$phaseExit = $LASTEXITCODE

Write-Host "`nEnsuring the complete backend is healthy after the challenge..." -ForegroundColor Cyan
docker compose up -d --wait --wait-timeout 180
if ($LASTEXITCODE -ne 0) {
    docker compose ps
    throw "Backend recovery check failed."
}

if ($phaseExit -ne 0) {
    throw "Phase 19B live acceptance failed. Send the complete output."
}

Write-Host "`n[7/7] Checking repository state..." -ForegroundColor Cyan
git diff --check
if ($LASTEXITCODE -ne 0) { throw "Working-tree validation failed." }
git status --short

Write-Host "`nPASS: Phase 19B model-ready blind challenge completed." -ForegroundColor Green
Write-Host "Do not commit yet. Send me the complete output." -ForegroundColor Yellow
