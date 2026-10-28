@echo off
REM TradingLab server — open http://localhost:8800 (phone on same WiFi: http://<this PC IP>:8800)
cd /d "%~dp0"
git checkout -- data/signals.json data/research/latest.json >NUL 2>&1
git pull --rebase --autostash -X theirs >NUL 2>&1
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt >NUL 2>&1
title TradingLab server
".venv\Scripts\python.exe" -m uvicorn app.server:app --host 0.0.0.0 --port 8800
pause
