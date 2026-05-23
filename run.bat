@echo off
title ThumbsAI
set "LOG=%~dp0data\launch.log"
echo %DATE% %TIME% run.bat started >> "%LOG%"

:: Check if ThumbsAI is already running (window title check — fast, no wmic)
powershell -NoProfile -Command ^
  "if (Get-Process pythonw -EA SilentlyContinue | Where-Object { $_.MainWindowTitle -eq 'ThumbsAI' }) { exit 1 } else { exit 0 }"
if %errorlevel%==1 (
    echo ThumbsAI is already running.
    echo %DATE% %TIME% already running, aborting >> "%LOG%"
    exit /b 0
)

if exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo %DATE% %TIME% venv found, launching venv pythonw >> "%LOG%"
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
    echo %DATE% %TIME% start returned, exiting bat >> "%LOG%"
) else (
    echo %DATE% %TIME% venv NOT found, trying system pythonw >> "%LOG%"
    where pythonw >nul 2>&1
    if not errorlevel 1 (
        start "" pythonw "%~dp0main.py"
    ) else (
        echo [ERROR] Virtual environment not found. Run INSTALL.bat first.
        pause
        exit /b 1
    )
)
