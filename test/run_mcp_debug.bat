@echo off
REM ============================================================
REM  MCP debug launcher - runs python -m common.qichacha_client
REM  Double-click this file to run.
REM ============================================================
setlocal EnableExtensions
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
echo [START] Running MCP debug test ...
echo.
python -m common.qichacha_client
echo.
echo [DONE]
pause
endlocal
