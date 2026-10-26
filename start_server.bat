@echo off
REM TradingLab server — open http://localhost:8800 (phone on same WiFi: http://<this PC IP>:8800)
cd /d "%~dp0"
title TradingLab server
".venv\Scripts\python.exe" -m uvicorn app.server:app --host 0.0.0.0 --port 8800
pause
