@echo off
chcp 65001 >nul
title HD2 Weapon Stat Editor
cd /d "%~dp0"

echo Starting HD2 Weapon Stat Editor...
echo.

REM Start the server, then open the browser once it answers.
start "" http://127.0.0.1:8777
python gui\app.py

echo.
echo Server stopped.
pause
