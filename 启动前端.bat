@echo off
chcp 65001 >nul
title 法智引擎 - Streamlit前端启动器（增强版）
cd /d "%~dp0"

echo.
echo ============================================================
echo   法智引擎 AI法律助理 - 前端启动器
echo   工作目录: %cd%
echo ============================================================

REM ====== 找 Python ======
if exist "%cd%\.venv\Scripts\python.exe" (
    set PYEXE="%cd%\.venv\Scripts\python.exe"
    echo [环境] 使用虚拟环境 Python: %PYEXE%
) else (
    for /f "delims=" %%i in ('where python 2^>nul ^| findstr /i python.exe') do (
        if not defined PYEXE set PYEXE="%%i"
    )
    if not defined PYEXE (
        echo [错误] 未找到 python.exe，请先安装 Python 并加入 PATH。
        pause
        exit /b 1
    )
    echo [环境] 使用系统 Python: %PYEXE%
)

echo.
echo ============  请选择操作  ============
echo   [1] 正常启动前端（端口 8501）
echo   [2] 先做启动前诊断（不启动服务）
echo   [3] 先诊断 → 通过后自动启动
echo   [4] 指定端口启动（例如 8502）
echo   [5] 查看最新运行日志
echo   [0] 退出
echo ======================================
set /p choice="请输入选项 [0-5]: "

if "%choice%"=="1" goto NORMAL
if "%choice%"=="2" goto DIAGNOSE
if "%choice%"=="3" goto DIAG_THEN_RUN
if "%choice%"=="4" goto CUSTOM_PORT
if "%choice%"=="5" goto SHOW_LOG
if "%choice%"=="0" goto END
echo 无效选项，请重新运行本脚本。
pause & exit /b 1

:NORMAL
echo.
%PYEXE% "%cd%\_start_streamlit_server.py" --port 8501
goto FINISH

:DIAGNOSE
echo.
%PYEXE% "%cd%\_start_streamlit_server.py" --port 8501 --diagnose
pause & goto END

:DIAG_THEN_RUN
echo.
%PYEXE% "%cd%\_start_streamlit_server.py" --port 8501 --diagnose
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [诊断失败] 请先处理上面的报错后再启动。
    pause & exit /b %ERRORLEVEL%
)
echo.
echo [诊断通过] 5秒后自动启动服务……
timeout /t 5 /nobreak >nul
%PYEXE% "%cd%\_start_streamlit_server.py" --port 8501
goto FINISH

:CUSTOM_PORT
set /p CUSTOM_PORT="请输入端口号（如 8502）: "
if "%CUSTOM_PORT%"=="" set CUSTOM_PORT=8502
echo.
%PYEXE% "%cd%\_start_streamlit_server.py" --port %CUSTOM_PORT%
goto FINISH

:SHOW_LOG
set LOGFILE="%cd%\logs\streamlit_runtime_latest.log"
if not exist %LOGFILE% (
    echo [提示] 还没有生成日志，请先至少运行一次启动脚本。
) else (
    echo.
    echo ======= 最新日志末尾 =======
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath %LOGFILE% -Tail 80 -Encoding UTF8"
    echo ============================
    echo.
    echo 完整日志文件: %LOGFILE%
)
pause & goto END

:FINISH
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [失败] 异常退出码: %ERRORLEVEL%
    echo [排查] 1) 看上方打印的错误日志末尾
    echo       2) 或重新运行本脚本选 [5] 查看日志
    echo       3) 或把 logs\ 目录下最新的 .log 文件发给开发者
) else (
    echo.
    echo [成功] 浏览器访问: http://localhost:8501/
)
pause

:END
