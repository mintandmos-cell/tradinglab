@echo off
REM TradingLab auto-trader (DEMO) - keep MT5 open, logged in, Algo Trading = green. Close this window to stop.
cd /d "%~dp0"
chcp 65001 >NUL
title TradingLab auto-trader (DEMO)
git checkout -- data/signals.json data/research/latest.json >NUL 2>&1
git pull --rebase --autostash -X theirs >NUL 2>&1
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt >NUL 2>&1
powercfg /change standby-timeout-ac 0 >NUL 2>&1
".venv\Scripts\python.exe" -W ignore -m app.autotrader
pause
