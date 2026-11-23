@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "MiniApp Tunnel" cmd /k python tunnel.py
timeout /t 3 /nobreak >nul
python bot.py
pause