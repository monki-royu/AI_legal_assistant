@echo off
REM ============================================================
REM  Qichacha API self-test launcher (ASCII-only, safe for all CMD code pages)
REM  Double-click this file to run. It uses cmd.exe directly so
REM  PowerShell execution policy does NOT apply.
REM  Output:
REM    1) Console prints PASS / FAIL / SKIP for every assertion
REM    2) test_qichacha_report.json - structured JSON report
REM ============================================================
setlocal EnableExtensions

cd /d "%~dp0"

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo [START] Running Qichacha API self-test ...
echo   Working dir: %cd%
echo.
echo   Python executable:
where python 2>nul
if errorlevel 1 (
    echo [ERROR] python.exe not found in PATH
    echo         Please install Python 3.10+ and add it to PATH, then retry.
    pause
    exit /b 2
)
python --version
echo ----------------------------------------------------------

python test_qichacha_api.py
set EXIT_CODE=%ERRORLEVEL%

echo.
echo ----------------------------------------------------------
if %EXIT_CODE% EQU 0 (
    echo [DONE] All assertions PASSED, exit_code=%EXIT_CODE%
    echo        Report saved: test_qichacha_report.json
) else (
    echo [DONE] Some assertions FAILED, exit_code=%EXIT_CODE%
    echo        Please scroll up to see FAIL lines and/or open test_qichacha_report.json
)
pause
endlocal & exit /b %EXIT_CODE%
