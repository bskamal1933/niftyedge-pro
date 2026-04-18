@echo off
title NiftyEdge Pro — Installer
color 0A
cls

echo.
echo  =====================================================
echo    NiftyEdge Pro v3 -- Windows Installer
echo  =====================================================
echo.

REM ── Check Python ──────────────────────────────────────
echo  [1/4] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERROR] Python not found!
    echo.
    echo  Please install Python from: https://www.python.org/downloads/
    echo  IMPORTANT: Check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo  [OK] Python %PYVER% found
echo.

REM ── Install core packages ─────────────────────────────
echo  [2/4] Installing core packages (flask, requests, flask-cors)...
echo.
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo.
    echo  [ERROR] Failed to install core packages.
    echo  Try running: pip install flask requests flask-cors
    echo.
    pause
    exit /b 1
)
echo  [OK] Core packages installed
echo.

REM ── Install desktop packages ──────────────────────────
echo  [3/4] Installing desktop app packages (optional)...
echo       pywebview, pystray, Pillow, win10toast
echo.
pip install -r requirements-desktop.txt --quiet
if errorlevel 1 (
    echo  [WARN] Some desktop packages failed - desktop app may be limited
    echo         You can still use Browser Mode (dashboard.html + server.py)
) else (
    echo  [OK] Desktop packages installed
)
echo.

REM ── Create desktop shortcut ───────────────────────────
echo  [4/4] Creating desktop shortcuts...
set SCRIPT_DIR=%~dp0..
set DESKTOP=%USERPROFILE%\Desktop

REM Shortcut: Start Server
echo Set oWS = WScript.CreateObject("WScript.Shell") > "%TEMP%\mkshortcut.vbs"
echo sLinkFile = "%DESKTOP%\NiftyEdge - Start Server.lnk" >> "%TEMP%\mkshortcut.vbs"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%TEMP%\mkshortcut.vbs"
echo oLink.TargetPath = "python" >> "%TEMP%\mkshortcut.vbs"
echo oLink.Arguments = """"%SCRIPT_DIR%\server.py"""" >> "%TEMP%\mkshortcut.vbs"
echo oLink.WorkingDirectory = "%SCRIPT_DIR%" >> "%TEMP%\mkshortcut.vbs"
echo oLink.Description = "Start NiftyEdge Pro Server" >> "%TEMP%\mkshortcut.vbs"
echo oLink.Save >> "%TEMP%\mkshortcut.vbs"
cscript /nologo "%TEMP%\mkshortcut.vbs"

REM Shortcut: Windows App
echo Set oWS = WScript.CreateObject("WScript.Shell") > "%TEMP%\mkshortcut2.vbs"
echo sLinkFile = "%DESKTOP%\NiftyEdge Pro.lnk" >> "%TEMP%\mkshortcut2.vbs"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%TEMP%\mkshortcut2.vbs"
echo oLink.TargetPath = "python" >> "%TEMP%\mkshortcut2.vbs"
echo oLink.Arguments = """"%SCRIPT_DIR%\app.py"""" >> "%TEMP%\mkshortcut2.vbs"
echo oLink.WorkingDirectory = "%SCRIPT_DIR%" >> "%TEMP%\mkshortcut2.vbs"
echo oLink.Description = "NiftyEdge Pro Windows App" >> "%TEMP%\mkshortcut2.vbs"
echo oLink.Save >> "%TEMP%\mkshortcut2.vbs"
cscript /nologo "%TEMP%\mkshortcut2.vbs"

echo  [OK] Desktop shortcuts created:
echo       - "NiftyEdge - Start Server" (starts backend)
echo       - "NiftyEdge Pro" (Windows app)
echo.

REM ── Done ──────────────────────────────────────────────
echo  =====================================================
echo    Installation Complete!
echo  =====================================================
echo.
echo  HOW TO USE:
echo.
echo  BROWSER MODE (Recommended):
echo    1. Double-click "NiftyEdge - Start Server" on your desktop
echo    2. Open dashboard.html in Chrome or Edge
echo    3. Look for the green [LIVE] indicator
echo.
echo  WINDOWS APP:
echo    1. Double-click "NiftyEdge Pro" on your desktop
echo    2. The server starts automatically
echo.
echo  Market hours: 09:15 - 15:30 IST (Mon-Fri)
echo.
echo  [WARNING] For educational use only - Not SEBI registered
echo.
pause
