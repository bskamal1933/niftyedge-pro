@echo off
title NiftyEdge Pro — Server
color 0A
cd /d "%~dp0.."
echo.
echo  NiftyEdge Pro — Starting server...
echo  Keep this window open while using the dashboard.
echo.
python server.py
pause
