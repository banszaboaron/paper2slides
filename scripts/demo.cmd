@echo off
rem Offline demo: the recorded answers of the live Minerva run, replayed through the Studio's MCP servers.
rem No model, no key, no network. Needs Windows + desktop PowerPoint for the renders. See README.md.
cd /d "%~dp0..\studio"
set PY=python
if exist "%~dp0..\.venv\Scripts\python.exe" set PY="%~dp0..\.venv\Scripts\python.exe"
%PY% tools\headless_run.py --replay --session minerva --pdf ..\examples\papers\minerva_2206.14858.pdf ^
  --content spec --spec ..\examples\minerva\deck.json --slides 12 --minutes 15 ^
  --team "paper2slides demo" --venue "Demo talk" --judge --export %*
if errorlevel 1 (echo. & echo Demo stopped, see the messages above. & exit /b 1)
echo.
echo Deck, PNG and PDF: studio\sessions\minerva\deck\
