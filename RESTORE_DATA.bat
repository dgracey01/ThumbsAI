@echo off
setlocal enabledelayedexpansion
title ThumbsAI — Data Restore
color 0E

echo ================================================================================
echo   ThumbsAI — Data Restore
echo   Restores settings.json and/or thumbs.db from a backup snapshot
echo   Designed by: Zero  ^|  Built by: Jarvis
echo ================================================================================
echo.
echo   WARNING: Restoring will OVERWRITE your current data folder contents.
echo   Make sure ThumbsAI is NOT running before continuing.
echo.

set "DIR=%~dp0"
set "DATADIR=%DIR%data"
set "BACKUPROOT=%DIR%_backups"

:: ── Ensure _backups exists and has snapshots ──────────────────────────────────
if not exist "!BACKUPROOT!" (
    echo [ERROR] No backups found. Run BACKUP_DATA.bat first.
    pause & exit /b 1
)

:: ── List available backups ────────────────────────────────────────────────────
echo Available backups:
echo.
set "IDX=0"
for /d %%B in ("!BACKUPROOT!\*") do (
    set /a IDX+=1
    set "BACKUP_!IDX!=%%~fB"
    set "BACKUP_!IDX!_NAME=%%~nxB"
    echo   [!IDX!] %%~nxB
)

if !IDX! EQU 0 (
    echo [ERROR] No backup snapshots found in !BACKUPROOT!
    pause & exit /b 1
)

echo.
set /p "CHOICE=Enter backup number to restore (1-!IDX!): "

:: Validate choice
if "!CHOICE!"=="" (
    echo [ERROR] No selection made.
    pause & exit /b 1
)
set "CHOSEN_DIR=!BACKUP_%CHOICE%!"
set "CHOSEN_NAME=!BACKUP_%CHOICE%_NAME!"

if "!CHOSEN_DIR!"=="" (
    echo [ERROR] Invalid selection.
    pause & exit /b 1
)

echo.
echo   Selected: !CHOSEN_NAME!
echo.

:: ── Ensure data dir exists ────────────────────────────────────────────────────
if not exist "%DATADIR%" mkdir "%DATADIR%"

:: ── Restore settings.json ─────────────────────────────────────────────────────
if exist "!CHOSEN_DIR!\settings.json" (
    set /p "RSET=Restore settings.json (favorites, watched folders, preferences)? (Y/N): "
    if /i "!RSET!"=="Y" (
        copy /Y "!CHOSEN_DIR!\settings.json" "%DATADIR%\settings.json" >nul
        echo [OK] settings.json restored.
    ) else (
        echo [SKIP] settings.json skipped.
    )
) else (
    echo [SKIP] settings.json not in this backup.
)

echo.

:: ── Restore thumbs.db ─────────────────────────────────────────────────────────
if exist "!CHOSEN_DIR!\thumbs.db" (
    for /f %%S in ('powershell -NoProfile -Command "[math]::Round((Get-Item '!CHOSEN_DIR!\thumbs.db').Length/1GB, 2)"') do set "DBGB=%%S"
    echo Backup thumbs.db size: !DBGB! GB
    set /p "RDB=Restore thumbs.db (thumbnail cache — this can take several minutes)? (Y/N): "
    if /i "!RDB!"=="Y" (
        echo [COPYING] Please wait...
        copy /Y "!CHOSEN_DIR!\thumbs.db" "%DATADIR%\thumbs.db" >nul
        if !errorlevel! EQU 0 (
            echo [OK] thumbs.db restored.
        ) else (
            echo [ERROR] thumbs.db restore failed. Is ThumbsAI still running?
        )
    ) else (
        echo [SKIP] thumbs.db skipped.
    )
) else (
    echo [INFO] thumbs.db not in this backup (was not included at backup time).
)

echo.
echo ================================================================================
echo   Restore complete. Launch ThumbsAI with run.bat
echo ================================================================================
echo.
pause
