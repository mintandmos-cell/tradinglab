@echo off
REM TradingLab auto-trader - keep MT5 open, logged in, Algo Trading = green. Close this window to stop.
REM Restarts by itself with the newest code when the program on GitHub changes (exit code 3).
cd /d "%~dp0"
chcp 65001 >NUL
title TradingLab auto-trader
powercfg /change standby-timeout-ac 0 >NUL 2>&1
:run
git checkout -- data/signals.json data/research/latest.json >NUL 2>&1
git pull --rebase --autostash -X theirs >NUL 2>&1
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt >NUL 2>&1
".venv\Scripts\python.exe" -W ignore -m app.autotrader
if %errorlevel%==3 goto run
pause
