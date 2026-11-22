# OpenClaw side: installs the CLI (if missing) and the paper2slides skill.
#   powershell -ExecutionPolicy Bypass -File openclaw\setup_openclaw.ps1
# Onboarding (model provider + API key, chat channel) is interactive and done by you:
#   openclaw onboard --install-daemon
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$nodeMajor = [int]((node --version).TrimStart("v").Split(".")[0])
if ($nodeMajor -lt 22) { throw "OpenClaw needs Node >= 22 (found $(node --version))" }

if (-not (Get-Command openclaw -ErrorAction SilentlyContinue)) {
    Write-Host "Installing openclaw globally via npm..."
    npm install -g openclaw@latest
}

$stateDir = if ($env:OPENCLAW_STATE_DIR) { $env:OPENCLAW_STATE_DIR } else { Join-Path $HOME ".openclaw" }
$dest = Join-Path $stateDir "skills\paper2slides"
New-Item -ItemType Directory -Force $dest | Out-Null
$tmpl = Get-Content (Join-Path $PSScriptRoot "skills\paper2slides\SKILL.md") -Raw
[IO.File]::WriteAllText((Join-Path $dest "SKILL.md"), $tmpl.Replace("{{P2S_ROOT}}", $root),
                        (New-Object Text.UTF8Encoding $false))
Write-Host "Installed skill -> $dest"

Write-Host @"

Next (interactive, needs your own model API key):
  openclaw onboard --install-daemon
  openclaw skills check          # paper2slides should be listed as eligible
Then in OpenClaw chat: "make slides from C:\path\to\paper.pdf for team A,B, venue ICLR 2026"
"@
