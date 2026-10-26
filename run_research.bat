@echo off
REM Re-run walk-forward research for all symbols (or pass symbols: run_research.bat USDJPY BTCUSD)
cd /d "%~dp0"
".venv\Scripts\python.exe" -m app.research %*
pause
