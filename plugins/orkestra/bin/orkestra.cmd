@echo off
rem Orkestra CLI launcher for cmd.exe and PowerShell on Windows (Claude Code puts this folder on PATH).
rem It finds a working Python 3.9+ among: the py launcher, python, python3. A Microsoft Store stub is found by name
rem but fails the probe, so it is skipped. Everything after "orkestra" is passed on and the exit code is kept.
rem The file has CRLF line endings on purpose: cmd.exe mis-reads labels and blocks in LF-only batch files.
setlocal
set "ORKESTRA_SCRIPT=%~dp0orkestra"
for %%P in ("py -3" python python3) do (
  %%~P -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
  if not errorlevel 1 (
    %%~P "%ORKESTRA_SCRIPT%" %*
    exit /b
  )
)
echo orkestra: no Python 3.9+ found (tried py -3, python and python3). Install it from https://www.python.org/downloads/ and reopen the terminal. 1>&2
exit /b 127
