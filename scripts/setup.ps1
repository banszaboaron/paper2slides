# One-time setup: creates .venv in the repo root and installs both halves
# (content pipeline p2s + Slide Studio). Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1      (or double-click install.bat)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv")) {
    # 3.11-3.13 all work; prefer 3.12 for widest wheel availability
    $py = "py"
    $ver = "-3.12"
    & $py $ver -c "import sys" 2>$null
    if ($LASTEXITCODE -ne 0) { $ver = "-3" }
    & $py $ver -m venv .venv
}
$python = ".\.venv\Scripts\python.exe"
& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt            # content pipeline (LangGraph)
& $python -m pip install -r studio\requirements.txt     # Slide Studio (design, PowerPoint COM)

if (-not (Test-Path ".env")) { Copy-Item .env.example .env }
if (-not (Test-Path "config\models.local.yaml")) {
    Copy-Item config\models.local.example.yaml config\models.local.yaml   # standalone runs: Claude API
}
if (-not (Test-Path "studio\config.json")) { Copy-Item studio\config.example.json studio\config.json }

Write-Host ""
Write-Host "OK. Next:"
Write-Host "  - Claude API:      setx ANTHROPIC_API_KEY sk-ant-...   (then open a new terminal)"
Write-Host "  - or Claude Code:  claude   (once, log in in the browser; uses your Pro/Max subscription)"
Write-Host "  - start:           start.bat   ->  http://127.0.0.1:8765"
