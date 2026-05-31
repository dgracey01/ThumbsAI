@echo off
title ThumbsAI
set "LOG=%~dp0data\launch.log"
if not exist "%~dp0data" mkdir "%~dp0data"

:: ── Git auto-update (best-effort; never blocks launch) ──────────────────────
::  Runs once on the first invocation, then re-executes the (possibly updated)
::  script with the __updated flag so the launch logic below always runs from
::  the current on-disk version (avoids "batch modified while running" issues).
::  Requires git on PATH and a .git folder. --ff-only never overwrites local
::  changes; if it can't fast-forward it just logs and launches the current
::  version. User data lives in data\ which is gitignored, so nothing collides.
if not "%~1"=="__updated" (
    where git >nul 2>&1
    if not errorlevel 1 if exist "%~dp0.git" (
        echo %DATE% %TIME% checking for updates >> "%LOG%"
        pushd "%~dp0"
        git pull --ff-only >> "%LOG%" 2>&1
        popd
    )
    call "%~f0" __updated
    exit /b
)

echo %DATE% %TIME% run.bat started >> "%LOG%"

:: Check for a running instance and prompt to close and reopen if found.
:: Exit codes from the PS block: 0 = not running, 1 = running (user said No), 2 = killed (user said Yes)
powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; $p = Get-Process pythonw -EA SilentlyContinue | Where-Object { $_.MainWindowTitle -eq 'ThumbsAI' }; if (-not $p) { exit 0 }; $r = [Windows.MessageBox]::Show('ThumbsAI is already running. Close and reopen?', 'ThumbsAI', 'YesNo', 'Question'); if ($r -eq 'Yes') { $p | Stop-Process -Force; Start-Sleep -Milliseconds 800; exit 2 }; exit 1"
set "INST=%errorlevel%"

if "%INST%"=="1" (
    echo %DATE% %TIME% already running, user chose No >> "%LOG%"
    exit /b 0
)
if "%INST%"=="2" (
    echo %DATE% %TIME% existing instance closed by user, relaunching >> "%LOG%"
)

if exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo %DATE% %TIME% launching venv pythonw >> "%LOG%"
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
) else (
    where pythonw >nul 2>&1
    if not errorlevel 1 (
        start "" pythonw "%~dp0main.py"
    ) else (
        echo [ERROR] Virtual environment not found. Run INSTALL.bat first.
        pause
        exit /b 1
    )
)
echo %DATE% %TIME% start returned, exiting bat >> "%LOG%"
