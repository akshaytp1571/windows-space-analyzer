@echo off
rem Runs the automated tests. Uses the same .venv as run_app.bat.
setlocal EnableExtensions
cd /d "%~dp0"

set "PY=python"
where py >nul 2>nul && set "PY=py -3"

if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)

".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 goto :fail

".venv\Scripts\python.exe" -m unittest discover -s tests -t .
if errorlevel 1 goto :fail

echo.
echo All tests passed.
pause
exit /b 0

:fail
echo.
echo The tests did not all pass, or setup failed. Read the messages above, then press a key to close this window.
pause >nul
exit /b 1
