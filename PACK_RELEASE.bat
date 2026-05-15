@echo off
setlocal enabledelayedexpansion
title ThumbsAI — Release Packager
color 0B

echo ================================================================================
echo   ThumbsAI — Release Packager
echo   Designed by: Zero  ^|  Built by: Jarvis
echo ================================================================================
echo.

set "DIR=%~dp0"
set "SRC=%DIR:~0,-1%"

:: ── Version ──────────────────────────────────────────────────────────────────
set /p "VERSION=Enter version number (e.g. 1.0.0): "
if "!VERSION!"=="" (
    echo [ERROR] Version cannot be empty.
    pause & exit /b 1
)

set "OUTDIR=%SRC%\_releases"
set "ZIPNAME=ThumbsAI_v!VERSION!.zip"
set "ZIPPATH=!OUTDIR!\!ZIPNAME!"

if not exist "!OUTDIR!" mkdir "!OUTDIR!"
if exist "!ZIPPATH!" (
    echo [INFO] Removing old !ZIPNAME!...
    del /f /q "!ZIPPATH!"
)

:: ── Stage to temp dir via robocopy ───────────────────────────────────────────
set "STAGE=%TEMP%\ThumbsAI_stage_%RANDOM%"
if exist "!STAGE!" rmdir /s /q "!STAGE!"
mkdir "!STAGE!"

echo.
echo [1/3] Staging files  (excluding .venv, data, __pycache__, _releases, _backups)...
echo.

robocopy "%SRC%" "!STAGE!" /E ^
    /XD ".venv" "data" "__pycache__" "_releases" "_backups" ".git" ^
    /XF "*.lnk" "*.psd" "*.pyc" "*.pyd" "desktop.ini" "test_*.py" ^
    /NFL /NDL /NJH /NJS /NP

:: robocopy exit codes 0-7 are success (bit flags for files copied/skipped)
if !errorlevel! GTR 7 (
    echo [ERROR] Staging failed (robocopy code !errorlevel!^).
    rmdir /s /q "!STAGE!"
    pause & exit /b 1
)

:: ── Count staged files ────────────────────────────────────────────────────────
for /f %%C in ('powershell -NoProfile -Command "(Get-ChildItem -Path '!STAGE!' -Recurse -File).Count"') do set "FCOUNT=%%C"
echo [OK] Staged !FCOUNT! files.

:: ── Compress to zip ───────────────────────────────────────────────────────────
echo.
echo [2/3] Compressing to !ZIPNAME!...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Compress-Archive -Path '!STAGE!\*' -DestinationPath '!ZIPPATH!' -CompressionLevel Optimal"

if not exist "!ZIPPATH!" (
    echo [ERROR] Zip creation failed.
    rmdir /s /q "!STAGE!"
    pause & exit /b 1
)

:: ── Clean up stage ────────────────────────────────────────────────────────────
echo.
echo [3/3] Cleaning up staging area...
rmdir /s /q "!STAGE!"

:: ── Report ────────────────────────────────────────────────────────────────────
for /f %%S in ('powershell -NoProfile -Command "[math]::Round((Get-Item '!ZIPPATH!').Length/1MB, 2)"') do set "ZIPSIZE=%%S"

echo.
echo ================================================================================
echo   Release ready:  !ZIPNAME!  (!ZIPSIZE! MB)
echo   Saved to:       !OUTDIR!
echo ================================================================================
echo.

set /p "OPEN=Open _releases folder? (Y/N): "
if /i "!OPEN!"=="Y" explorer "!OUTDIR!"

pause
