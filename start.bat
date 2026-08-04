@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

echo.
echo ============================================================
echo   天衍 - Windows 一键启动
echo ============================================================
echo.

:: ===== 检测 Python =====
echo [1/3] 检测 Python 环境...
where python >nul 2>&1
if !errorlevel! neq 0 (
    where python3 >nul 2>&1
    if !errorlevel! neq 0 (
        echo   [错误] 未找到 Python 3.10+
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

:: 检查 Python 版本
for /f "tokens=2" %%i in ('!PY! --version 2^>^&1') do set PYVER=%%i
echo   [OK] Python !PYVER!

:: ===== 检测依赖 =====
echo [2/3] 检测依赖...
!PY! -c "import fastapi" >nul 2>&1
if !errorlevel! neq 0 (
    echo   [提示] 依赖未安装，正在自动安装...
    echo.
    
    :: 配置 pip 镜像源
    !PY! -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>&1
    
    :: 安装核心依赖
    echo   安装核心依赖 (约1-2分钟)...
    !PY! -m pip install fastapi uvicorn litellm openai pydantic pydantic-settings PyYAML python-multipart httpx python-dotenv -q
    if !errorlevel! neq 0 (
        echo   [错误] 核心依赖安装失败
        echo   请手动运行: pip install -r requirements-win.txt
        pause
        exit /b 1
    )
    echo   [OK] 核心依赖已安装
    
    :: 安装扩展依赖
    echo   安装扩展依赖 (约2-3分钟)...
    !PY! -m pip install python-docx pypdf ebooklib beautifulsoup4 Markdown readability-lxml lxml chromadb redis psycopg2-binary RestrictedPython -q
    if !errorlevel! neq 0 (
        echo   [警告] 部分扩展依赖安装失败，核心功能仍可用
    ) else {
        echo   [OK] 扩展依赖已安装
    )
    
    :: 安装浏览器抓取
    echo   安装浏览器抓取 (可选)...
    !PY! -m pip install playwright -q >nul 2>&1
    if !errorlevel! equ 0 (
        !PY! -m playwright install chromium >nul 2>&1
        if !errorlevel! equ 0 (
            echo   [OK] 浏览器抓取已安装
        ) else (
            echo   [跳过] 浏览器内核下载失败
        )
    ) else (
        echo   [跳过] playwright 安装失败
    )
) else (
    echo   [OK] 依赖已安装
)

:: ===== 启动服务 =====
echo [3/3] 启动服务...
echo.
echo ============================================================
echo   天衍 启动中...
echo   访问地址: http://localhost:8000/
echo   按 Ctrl+C 停止服务
echo ============================================================
echo.

set LITELLM_LOCAL_MODEL_COST_MAP=True
!PY! run.py
pause
