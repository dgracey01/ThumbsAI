@echo off
title ThumbsAI — Installer
color 0A

echo ================================================================================
echo   ThumbsAI — AI Image Browser
echo   Designed by: Zero  ^|  Built by: Jarvis
echo ================================================================================
echo.

set "DIR=%~dp0"

echo [CHECKING] Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [NOT FOUND] Python not found. Attempting install via winget...
    winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo [ERROR] Install Python manually: https://www.python.org/downloads/
        pause & exit /b 1
    )
    echo [OK] Python installed. Please re-run this installer.
    pause & exit /b 0
)
echo [OK] Python found:
python --version
echo.

echo [1/2] Creating virtual environment...
if not exist "%DIR%.venv\Scripts\python.exe" (
    python -m venv "%DIR%.venv"
    if errorlevel 1 ( echo [ERROR] venv creation failed. & pause & exit /b 1 )
    echo [OK] Virtual environment created.
) else (
    echo [OK] Already exists, skipping.
)
echo.

"%DIR%.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --disable-pip-version-check

echo [2/2] Installing packages...
echo.
"%DIR%.venv\Scripts\python.exe" -m pip install ^
    PySide6 ^
    Pillow ^
    pillow-avif-plugin ^
    --quiet --disable-pip-version-check

if errorlevel 1 (
    echo [WARNING] Some packages failed — trying without optional formats...
    "%DIR%.venv\Scripts\python.exe" -m pip install PySide6 Pillow ^
        --quiet --disable-pip-version-check
)

echo.
echo ================================================================================
echo   Installation complete!  Run the app:  run.bat
echo ================================================================================
echo.
pause
