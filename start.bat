@echo off
:: Novel Agent 一键启动 (Windows)
:: 双击运行即可

setlocal
set "DIR=%~dp0"
cd /d "%DIR%"

:: 检查 Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    where python3 >nul 2>&1
    if %errorlevel% neq 0 (
        echo [错误] 未找到 Python，请先安装 Python 3.10+
        echo 下载地址: https://www.python.org/downloads/
        echo.
        pause
        exit /b 1
    )
    set "PY=python3"
) else (
    set "PY=python"
)

:: 检查依赖
echo [1/2] 检查依赖...
%PY% -c "import fastapi" >nul 2>&1
if %errorlevel% neq 0 (
    echo [安装] 首次运行，正在安装依赖...
    %PY% -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [错误] 依赖安装失败，请手动运行: pip install -r requirements.txt
        pause
        exit /b 1
    )
)

:: 启动
echo [2/2] 启动服务...
echo.
echo ========================================
echo   Novel Agent 启动中...
echo   访问地址: http://localhost:8000/
echo   按 Ctrl+C 停止服务
echo ========================================
echo.

%PY% run.py
pause
