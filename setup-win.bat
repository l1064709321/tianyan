@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: 设置环境变量避免 litellm 超时
set LITELLM_LOCAL_MODEL_COST_MAP=True

echo.
echo ============================================================
echo   天衍 - Windows 一键安装
echo ============================================================
echo.

:: 检查 Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    where python3 >nul 2>&1
    if %errorlevel% neq 0 (
        echo [错误] 未找到 Python，请先安装 Python 3.10+
        echo 下载地址: https://www.python.org/downloads/
        echo 安装时勾选 "Add Python to PATH"
        pause
        exit /b 1
    )
    set "PY=python3"
) else (
    set "PY=python"
)

:: 检查 Python 版本
%PY% --version 2>nul
if %errorlevel% neq 0 (
    echo [错误] Python 无法运行，请重新安装
    pause
    exit /b 1
)

echo [1/5] 升级 pip...
%PY% -m pip install --upgrade pip -q

echo [2/5] 安装核心依赖...
%PY% -m pip install fastapi uvicorn litellm openai pydantic pydantic-settings PyYAML python-multipart httpx python-dotenv -q
if %errorlevel% neq 0 (
    echo [警告] 核心依赖安装失败，尝试继续...
)

echo [3/5] 安装文件格式支持...
%PY% -m pip install python-docx pypdf ebooklib beautifulsoup4 Markdown -q

echo [4/5] 安装网页抓取 + 向量检索 + 记忆系统...
%PY% -m pip install readability-lxml lxml chromadb redis psycopg2-binary RestrictedPython -q
if %errorlevel% neq 0 (
    echo [警告] 部分依赖安装失败，核心功能仍可用
    echo   - chromadb 失败: 向量检索降级到关键词检索
    echo   - redis 失败: 对话记忆降级到内存
    echo   - psycopg2 失败: 使用 SQLite (已内置)
)

echo [5/5] 安装浏览器抓取 (可选)...
%PY% -m pip install playwright -q
if %errorlevel% equ 0 (
    echo   正在下载 Chromium 内核 (约 180MB)...
    %PY% -m playwright install chromium
    if %errorlevel% neq 0 (
        echo [警告] Chromium 下载失败，浏览器抓取功能不可用
        echo   手动安装: python -m playwright install chromium
    )
) else (
    echo [跳过] playwright 安装失败，浏览器抓取功能不可用
)

echo.
echo ============================================================
echo   安装完成！
echo.
echo   启动命令:
echo     python run.py
echo.
echo   然后浏览器打开: http://localhost:8000
echo.
echo   首次使用需要在设置面板配置 API Key:
echo     DeepSeek: https://platform.deepseek.com
echo ============================================================
echo.

pause
