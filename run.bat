@echo off
title ThumbsAI
set "LOG=%~dp0data\launch.log"
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
