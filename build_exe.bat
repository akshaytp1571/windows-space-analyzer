@echo off
rem Builds dist\WindowsSpaceAnalyzer.exe, one file that runs on a PC without Python installed.
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
    echo Creating the build environment in .venv ...
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)

".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r requirements-build.txt
if errorlevel 1 goto :fail

echo Building the .exe. This takes a few minutes ...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name WindowsSpaceAnalyzer --collect-all send2trash main.py
if errorlevel 1 goto :fail
if not exist "dist\WindowsSpaceAnalyzer.exe" goto :fail

echo.
echo Done. The program is at: %CD%\dist\WindowsSpaceAnalyzer.exe
echo Copy that one file anywhere. It runs as a normal user. Use "Restart as administrator" inside the app when you need the admin items.
pause
exit /b 0

:fail
echo.
echo The build did not finish. Read the messages above, then press a key to close this window.
pause >nul
exit /b 1
