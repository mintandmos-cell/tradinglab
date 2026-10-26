@echo off
REM Export long H1 history from MT5 (keep MT5 open and logged in), then send it to GitHub for the cloud research
cd /d "%~dp0"
chcp 65001 >nul
title TradingLab - export MT5 history
".venv\Scripts\python.exe" -m tools.export_mt5_history
if errorlevel 1 goto end
git add data/history
git commit -m "history: MT5 H1 export"
git pull --rebase -X theirs origin main
git push
echo.
echo Done - history sent to GitHub. The cloud re-runs the research with it automatically (about 10-30 min).
:end
pause
