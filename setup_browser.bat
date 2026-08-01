@echo off
:: ============================================================
:: Playwright Chromium 内核一键安装 (Windows)
:: 固化国内镜像源, 解决官方源下载慢/卡住的问题
:: 用法: 双击运行 或 setup_browser.bat
:: ============================================================
setlocal
set "DIR=%~dp0"
cd /d "%DIR%"

:: 国内镜像源 (npmmirror, 阿里云CDN, 国内最快)
set "PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright"

:: 检查 Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    where python3 >nul 2>&1
    if %errorlevel% neq 0 (
        echo [错误] 未找到 Python, 请先安装 Python 3.10+
        echo 下载地址: https://www.python.org/downloads/
        pause
        exit /b 1
    )
    set "PY=python3"
) else (
    set "PY=python"
)

echo ============================================================
echo   Playwright Chromium 内核安装 (国内镜像加速)
echo   镜像源: %PLAYWRIGHT_DOWNLOAD_HOST%
echo   内核装到系统默认位置 (各平台各自管理, 跨机器不冲突)
echo ============================================================
echo.

:: 1. 确保 playwright python 包已装
echo [1/3] 检查 playwright python 包...
%PY% -c "import playwright" >nul 2>&1
if %errorlevel% neq 0 (
    echo       未安装, 正在安装...
    %PY% -m pip install playwright
    if %errorlevel% neq 0 (
        echo [错误] playwright 包安装失败
        pause
        exit /b 1
    )
) else (
    echo       playwright python 包已就绪
)

:: 2. 下载 Chromium 内核 (国内镜像)
echo.
echo [2/3] 下载 Chromium 内核 (国内镜像, 约 180MB)...
%PY% -m playwright install chromium
if %errorlevel% neq 0 (
    echo [错误] Chromium 内核下载失败
    pause
    exit /b 1
)

:: 3. 验证内核能否启动
echo.
echo [3/3] 验证 Chromium 启动...
%PY% -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); pg=b.new_page(); pg.goto('about:blank'); print('  内核启动 OK, UA:', pg.evaluate('navigator.userAgent')[:50]); b.close(); p.stop(); print('  验证通过!')"
if %errorlevel% neq 0 (
    echo.
    echo [错误] 验证失败, 请检查上方日志
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   安装完成! browser_fetch (扫榜/JS渲染抓取) 现在可用
echo ============================================================
pause
