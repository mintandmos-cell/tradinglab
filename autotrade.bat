@echo off
REM TradingLab auto-trader (DEMO) - keep MT5 open, logged in, Algo Trading = green. Close this window to stop.
cd /d "%~dp0"
chcp 65001 >NUL
title TradingLab auto-trader (DEMO)
git pull --rebase --autostash >NUL 2>&1
powercfg /change standby-timeout-ac 0 >NUL 2>&1
".venv\Scripts\python.exe" -W ignore -m app.autotrader
pause
