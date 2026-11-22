@echo off
rem Wrapper so OpenClaw (or anyone) can call the pipeline without activating the venv.
setlocal
set "ROOT=%~dp0.."
pushd "%ROOT%"
"%ROOT%\.venv\Scripts\python.exe" -m p2s %*
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
