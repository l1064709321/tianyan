@echo off
chcp 65001 >nul 2>&1
:: 天衍 一键启动 (Windows)
:: 双击运行即可, 自动检查依赖 + 国内镜像源

setlocal enabledelayedexpansion
set "DIR=%~dp0"
cd /d "%DIR%"

:: 检查 Python
where python >nul 2>&1
if !errorlevel! neq 0 (
    where python3 >nul 2>&1
    if !errorlevel! neq 0 (
        echo [错误] 未找到 Python，请先安装 Python 3.10+
        echo   下载地址: https://www.python.org/downloads/
        echo   安装时勾选 "Add Python to PATH"
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
!PY! -c "import fastapi" >nul 2>&1
if !errorlevel! neq 0 (
    echo [安装] 首次运行，正在安装依赖 (使用清华镜像源)...
    :: 配置清华镜像源加速
    !PY! -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>&1
    !PY! -m pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn >nul 2>&1
    :: 先装核心依赖 (纯 Python, 必成功)
    !PY! -m pip install fastapi uvicorn litellm pydantic pydantic-settings PyYAML python-multipart httpx
    if !errorlevel! neq 0 (
        echo [错误] 核心依赖安装失败
        echo   请手动运行: install_windows.bat
        echo   或手动安装: pip install fastapi uvicorn litellm
        pause
        exit /b 1
    )
    :: 文件格式依赖 (失败不阻塞)
    !PY! -m pip install python-docx pypdf beautifulsoup4 Markdown ebooklib 2>nul
    echo [OK] 核心依赖安装完成
)

:: 启动
set LITELLM_LOCAL_MODEL_COST_MAP=True
echo [2/2] 启动服务...
echo.
echo ========================================
echo   天衍 启动中...
echo   访问地址: http://localhost:8000/
echo   按 Ctrl+C 停止服务
echo ========================================
echo.

!PY! run.py
pause
