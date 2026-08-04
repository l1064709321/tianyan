@echo off
chcp 65001 >nul 2>&1
:: ============================================================
:: 天衍 - Windows 一键安装 (国内镜像源, 自动跳过编译依赖)
:: 双击运行即可, 无需手动配置 anything
:: ============================================================
setlocal enabledelayedexpansion
set "DIR=%~dp0"
cd /d "%DIR%"

echo ============================================================
echo   天衍 - Windows 一键安装
echo   使用国内镜像源, 自动跳过需要 C 编译器的依赖
echo ============================================================
echo.

:: 1. 检查 Python
where python >nul 2>&1
if !errorlevel! neq 0 (
    where python3 >nul 2>&1
    if !errorlevel! neq 0 (
        echo [错误] 未找到 Python, 请先安装 Python 3.10+
        echo.
        echo   下载地址: https://www.python.org/downloads/
        echo   安装时请勾选 "Add Python to PATH"
        echo.
        pause
        exit /b 1
    )
    set "PY=python3"
) else (
    set "PY=python"
)

echo [1/5] Python: 
!PY! --version
echo.

:: 2. 配置国内 pip 镜像源 (清华源, 国内最快)
echo [2/5] 配置国内 pip 镜像源 (清华源)...
!PY! -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>&1
!PY! -m pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn >nul 2>&1
echo       已配置清华镜像源
echo.

:: 3. 升级 pip
echo [3/5] 升级 pip...
!PY! -m pip install --upgrade pip -q 2>nul
echo.

:: 4. 安装核心依赖 (跳过需要 C 编译器的包)
echo [4/5] 安装核心依赖...
echo       (自动跳过 lxml/playwright 等需要 C 编译器的包)
echo.

:: 先装核心依赖 (纯 Python, 必成功)
!PY! -m pip install fastapi uvicorn litellm pydantic pydantic-settings PyYAML python-multipart httpx
if !errorlevel! neq 0 (
    echo [警告] 部分核心依赖安装失败, 尝试继续...
)

:: 再装文件格式依赖 (纯 Python)
!PY! -m pip install python-docx pypdf beautifulsoup4 Markdown
if !errorlevel! neq 0 (
    echo [警告] 部分文件格式依赖安装失败, 对应格式上传将不可用
)

:: ebooklib 有时依赖有问题, 单独装, 失败不阻塞
!PY! -m pip install ebooklib 2>nul
if !errorlevel! neq 0 (
    echo [提示] ebooklib 安装失败, .epub 上传将不可用 (不影响核心功能)
)

echo.
echo       核心依赖安装完成!
echo.

:: 5. 尝试安装可选依赖 (失败不阻塞)
echo [5/5] 尝试安装可选依赖 (失败不影响核心功能)...

:: lxml + readability: Windows 上可能需要 C 编译器, 失败则跳过
!PY! -m pip install lxml readability-lxml 2>nul
if !errorlevel! neq 0 (
    echo       [跳过] lxml/readability-lxml 安装失败 (需要 C 编译器)
    echo              不影响核心功能, web_fetch 会用内置解析器降级
) else (
    echo       [OK] lxml + readability-lxml 已安装 (网页正文提取更好)
)

:: playwright: 体积大, 单独装, 失败不阻塞
!PY! -m pip install playwright 2>nul
if !errorlevel! neq 0 (
    echo       [跳过] playwright 安装失败
    echo              不影响核心功能, 仅扫榜功能不可用
) else (
    echo       [OK] playwright 已安装 (如需扫榜, 再运行 setup_browser.bat 下载内核)
)

echo.
echo ============================================================
echo   安装完成!
echo.
echo   启动方式:
echo     1. 双击 start.bat
echo     2. 或运行: python run.py
echo     3. 浏览器打开: http://localhost:8000/
echo.
echo   首次使用:
echo     1. 打开页面后点右上角 设置 齿轮
echo     2. 添加模型 (推荐 DeepSeek, 国内直连)
echo     3. 填入 API Key 即可开始创作
echo ============================================================
echo.
pause
