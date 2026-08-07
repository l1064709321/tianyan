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

    :: 国内镜像源优先, 国外源兜底
    set "M1=https://pypi.tuna.tsinghua.edu.cn/simple"
    set "M2=https://mirrors.aliyun.com/pypi/simple/"
    set "M3=https://repo.huaweicloud.com/repository/pypi/simple/"
    set "M4=https://pypi.mirrors.ustc.edu.cn/simple/"
    set "M5=https://mirrors.cloud.tencent.com/pypi/simple/"
    set "M6=https://pypi.douban.com/simple/"
    set "M7=https://mirrors.163.com/pypi/simple/"
    set "M8=https://pypi.org/simple/"

    :: 逐源尝试安装核心依赖 (每个源 10 分钟超时)
    echo   安装核心依赖 (每个源最多 10 分钟, 自动切换)...
    set "CORE_OK=0"
    for %%M in (!M1! !M2! !M3! !M4! !M5! !M6! !M7! !M8!) do (
        if !CORE_OK! equ 0 (
            echo     尝试 %%M ...
            !PY! -m pip install fastapi uvicorn litellm openai pydantic pydantic-settings PyYAML python-multipart httpx python-dotenv -i %%M --timeout 5 --retries 2 -q
            if !errorlevel! equ 0 (
                echo   [OK] 核心依赖安装成功
                set "CORE_OK=1"
            ) else (
                echo     [!] 失败, 切换下一个源...
            )
        )
    )
    if !CORE_OK! equ 0 (
        echo   [错误] 核心依赖安装失败: 所有源均不可用
        echo   请手动运行: pip install -r requirements.txt
        pause
        exit /b 1
    )

    :: 逐源尝试安装扩展依赖 (每个源 10 分钟超时, 失败不阻断)
    echo   安装扩展依赖 (每个源最多 10 分钟, 自动切换)...
    set "EXT_OK=0"
    for %%M in (!M1! !M2! !M3! !M4! !M5! !M6! !M7! !M8!) do (
        if !EXT_OK! equ 0 (
            echo     尝试 %%M ...
            !PY! -m pip install python-docx pypdf ebooklib beautifulsoup4 Markdown readability-lxml lxml chromadb redis psycopg2-binary RestrictedPython -i %%M --timeout 5 --retries 2 -q
            if !errorlevel! equ 0 (
                echo   [OK] 扩展依赖安装成功
                set "EXT_OK=1"
            ) else (
                echo     [!] 失败, 切换下一个源...
            )
        )
    )
    if !EXT_OK! equ 0 (
        echo   [警告] 部分扩展依赖安装失败，核心功能仍可用
    )

    :: 安装浏览器抓取 (可选)
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
