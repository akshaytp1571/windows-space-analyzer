@echo off
rem Run Windows Space Analyzer from the source code. The first run creates .venv and installs what it needs.
setlocal EnableExtensions
cd /d "%~dp0"

set "PY=python"
where py >nul 2>nul && set "PY=py -3"

%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
    echo Python 3.10 or newer was not found.
    echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
    goto :fail
)

if not exist ".venv\Scripts\python.exe" (
    echo First run: creating the environment in .venv ...
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)

".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 goto :fail

".venv\Scripts\python.exe" main.py
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo Something did not work. Read the messages above, then press a key to close this window.
pause >nul
exit /b 1
