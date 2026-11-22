@echo off
rem Slide Studio -> http://127.0.0.1:8765 (opens by itself). Add "demo" for the offline replay demo.
cd /d "%~dp0studio"
set PY=python
if exist "%~dp0.venv\Scripts\python.exe" set PY="%~dp0.venv\Scripts\python.exe"
if /i "%1"=="demo" (
  set SDL_LLM=replay
  set SDL_REPLAY_DIR=replay
)
%PY% webapp\server.py
pause
