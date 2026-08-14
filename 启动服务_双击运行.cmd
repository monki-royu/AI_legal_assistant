@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM ============================================================
REM  法智引擎 - 零交互一键启动（自动选 1 → 启动前端 8501）
REM  双击本文件即可，无需输入任何选项
REM ============================================================

echo.
echo ============================================================
echo   法智引擎 AI法律助理 - 自动启动
echo ============================================================
echo.

REM 找 Python
if exist "%cd%\.venv\Scripts\python.exe" (
    set PYEXE="%cd%\.venv\Scripts\python.exe"
    echo [环境] 虚拟环境 Python
) else (
    for /f "delims=" %%i in ('where python 2^>nul ^| findstr /i python.exe') do (
        if not defined PYEXE set PYEXE="%%i"
    )
    echo [环境] 系统 Python
)

echo.
echo [1/3] 启动前诊断...
%PYEXE% "%cd%\_start_streamlit_server.py" --port 8501 --diagnose
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [失败] 诊断未通过，请查看上方报错。
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [2/3] 等待 3 秒后启动服务...
timeout /t 3 /nobreak >nul

echo.
echo [3/3] 正在启动 Streamlit 前端服务 (端口 8501)...
echo       启动过程约 15-40 秒，请耐心等待。
echo.

%PYEXE% "%cd%\_start_streamlit_server.py" --port 8501

echo.
echo ============================================================
if %ERRORLEVEL% EQU 0 (
    echo   ✅ 服务已启动成功!
    echo   👉 浏览器访问: http://localhost:8501/
) else (
    echo   ❌ 启动失败，退出码: %ERRORLEVEL%
    echo   请查看日志文件: logs\streamlit_runtime_latest.log
)
echo ============================================================
echo.
pause
