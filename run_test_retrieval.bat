@echo off
REM ============================================================
REM Retrieval Strategy Test Runner (ASCII only)
REM Run test_retrieval_strategy.py under UTF-8 environment
REM ============================================================

cd /d "%~dp0"

set PYTHONIOENCODING=utf-8
python test_retrieval_strategy.py

echo.
echo ----------------------------------------
echo Test finished. See test_retrieval_report.json
echo ----------------------------------------
pause
