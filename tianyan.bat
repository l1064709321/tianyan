@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion

REM ============================================================
REM 天衍 - Windows 一键启动脚本
REM 功能: 检测 Docker → 未安装则下载安装 → 检测 .env → 启动
REM ============================================================

title 天衍一键启动

echo.
echo ============================================================
echo   天衍 - 多 Agent 协同创作系统
echo   一键启动脚本 (Windows)
echo ============================================================
echo.

REM ---------- 1. 检测 Docker ----------
echo [1/5] 检测 Docker...
docker --version >nul 2>&1
if !errorlevel! neq 0 (
    echo.
    echo [!] 未检测到 Docker, 开始自动安装...
    echo.
    goto :install_docker
)
docker --version
echo [OK] Docker 已安装
goto :check_compose

REM ---------- 2. 安装 Docker ----------
:install_docker
echo ============================================================
echo   开始下载 Docker Desktop for Windows
echo   优先使用清华镜像源, 10 分钟超时后切换国外源
echo ============================================================

set "DOCKER_INSTALLER=%TEMP%\DockerDesktopInstaller.exe"

REM 优先: 清华镜像
echo.
echo [1/2] 尝试清华镜像下载...
powershell -Command "$ProgressPreference='SilentlyContinue'; try { $task=Start-Job -ScriptBlock { param($u,$o) Invoke-WebRequest -Uri $u -OutFile $o -UseBasicParsing } -ArgumentList 'https://mirrors.tuna.tsinghua.edu.cn/docker-ce/win/static/stable/x86_64/Docker%%20Desktop%%20Installer.exe','%DOCKER_INSTALLER%'; if(Wait-Job $task -Timeout 600){Receive-Job $task;Remove-Job $task;exit 0}else{Stop-Job $task;Remove-Job $task;exit 1} } catch { exit 1 }"
if !errorlevel! equ 0 (
    if exist "%DOCKER_INSTALLER%" (
        echo [OK] 清华镜像下载完成
        goto :do_install
    )
)
echo [!] 清华镜像下载失败或超时, 切换到官方源...

REM 备选: 官方源
echo.
echo [2/2] 尝试官方源下载...
powershell -Command "$ProgressPreference='SilentlyContinue'; try { Invoke-WebRequest -Uri 'https://desktop.docker.com/win/main/amd64/Docker%%20Desktop%%20Installer.exe' -OutFile '%DOCKER_INSTALLER%' -UseBasicParsing; exit 0 } catch { exit 1 }"
if !errorlevel! neq 0 (
    echo.
    echo [ERROR] Docker 下载失败!
    echo.
    echo 请手动下载安装 Docker Desktop:
    echo   https://www.docker.com/products/docker-desktop/
    echo.
    echo 安装完成后重新运行此脚本。
    pause
    exit /b 1
)
echo [OK] 官方源下载完成

:do_install
echo.
echo [3/5] 安装 Docker Desktop...
"%DOCKER_INSTALLER%" install --quiet --accept-license
if !errorlevel! neq 0 (
    echo [WARNING] 静默安装可能需要管理员权限, 尝试普通安装...
    "%DOCKER_INSTALLER%" install
)

REM 清理安装包
del "%DOCKER_INSTALLER%" >nul 2>&1

echo.
echo [OK] Docker Desktop 安装完成
echo [!] 请等待 Docker Desktop 启动完毕 (系统托盘出现鲸鱼图标)

REM 启动 Docker Desktop
echo.
echo [4/5] 启动 Docker Desktop...
set "DOCKER_DESKTOP=C:\Program Files\Docker\Docker\Docker Desktop.exe"
if not exist "%DOCKER_DESKTOP%" (
    set "DOCKER_DESKTOP=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
)
if exist "!DOCKER_DESKTOP!" (
    start "" "!DOCKER_DESKTOP!"
) else (
    echo [WARNING] 未找到 Docker Desktop, 请手动启动
)

REM 等待 Docker 就绪 (最多等待 120 秒)
echo.
echo 等待 Docker 引擎启动...
set /a WAIT_COUNT=0

:wait_docker
docker info >nul 2>&1
if !errorlevel! equ 0 (
    echo [OK] Docker 引擎已就绪
    goto :check_compose
)
set /a WAIT_COUNT+=1
if !WAIT_COUNT! geq 60 (
    echo [ERROR] Docker 启动超时 (120秒)
    echo 请手动启动 Docker Desktop 后重新运行此脚本
    pause
    exit /b 1
)
echo   等待中... (!WAIT_COUNT!/60)
timeout /t 2 /nobreak >nul
goto :wait_docker

REM ---------- 3. 检测 docker-compose ----------
:check_compose
echo.
echo [5/5] 检测 docker-compose...

docker compose version >nul 2>&1
if !errorlevel! equ 0 (
    docker compose version
    echo [OK] docker compose 已可用
    goto :check_env
)

docker-compose --version >nul 2>&1
if !errorlevel! equ 0 (
    docker-compose --version
    echo [OK] docker-compose 已安装
    goto :check_env
)

echo [!] docker-compose 未安装
echo [ERROR] docker-compose 不可用
echo 请确保 Docker Desktop 已正确安装并启动
echo Docker Desktop 自带 docker compose, 如果不可用说明安装有问题
pause
exit /b 1

REM ---------- 4. 检测 .env ----------
:check_env
echo.
echo ============================================================
echo   检测环境配置文件
echo ============================================================

if not exist ".env" (
    if exist ".env.example" (
        echo [!] .env 文件不存在, 从 .env.example 复制...
        copy .env.example .env >nul
        echo [OK] 已创建 .env 文件
        echo.
        echo ============================================================
        echo   重要: 请编辑 .env 文件, 填入你的 API Key!
        echo   至少配置一个:
        echo     OPENAI_API_KEY=sk-你的DeepSeek密钥
        echo     或 DEEPSEEK_API_KEY=sk-你的DeepSeek密钥
        echo ============================================================
        echo.
        set /p EDIT_ENV=是否现在编辑 .env? (Y/N):
        if /i "!EDIT_ENV!"=="Y" (
            notepad .env
        )
    ) else (
        echo [ERROR] .env 和 .env.example 均不存在
        pause
        exit /b 1
    )
) else (
    echo [OK] .env 文件已存在
)

REM ---------- 5. 启动服务 ----------
echo.
echo ============================================================
echo   启动天衍服务
echo ============================================================
echo.

echo 正在构建并启动容器 (首次构建约 2-3 分钟)...
echo.

REM 尝试新版 docker compose 命令, 失败则回退到 docker-compose
docker compose up -d --build 2>nul
if !errorlevel! neq 0 (
    docker-compose up -d --build
)
if !errorlevel! neq 0 (
    echo.
    echo [ERROR] 启动失败!
    echo 请检查:
    echo   1. Docker Desktop 是否已启动
    echo   2. .env 文件中的 API Key 是否正确
    echo   3. 端口 8000 是否被占用
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   天衍启动成功!
echo ============================================================
echo.
echo   访问地址: http://localhost:8000
echo.
echo   常用命令:
echo     查看日志:   docker logs -f tianyan
echo     停止服务:   docker-compose down
echo     重启服务:   docker-compose restart
echo     重新构建:   docker-compose up -d --build
echo.
echo ============================================================

REM 自动打开浏览器
start http://localhost:8000

pause
exit /b 0
