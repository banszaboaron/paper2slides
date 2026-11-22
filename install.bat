@echo off
rem One-time setup (Python venv + all packages). See README.md.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1
pause
