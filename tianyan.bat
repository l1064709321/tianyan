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

REM 配置 Docker 镜像加速 (已安装的情况)
set "DOCKER_USERCONFIG=%USERPROFILE%\.docker"
if not exist "%DOCKER_USERCONFIG%" mkdir "%DOCKER_USERCONFIG%"
powershell -NoProfile -Command "if (-not (Test-Path '%DOCKER_USERCONFIG%\daemon.json')) { $d = @{'registry-mirrors'=@('https://mirror.ccs.tencentyun.com','https://docker.mirrors.ustc.edu.cn','https://docker.m.daocloud.io')}; $json = ConvertTo-Json -InputObject $d -Depth 5 -Compress; Set-Content -Path '%DOCKER_USERCONFIG%\daemon.json' -Value $json -Encoding UTF8; Write-Host '[OK] 已配置 Docker 镜像加速' } else { Write-Host '[OK] daemon.json 已存在' }"

goto :check_compose

REM ---------- 2. 安装 Docker ----------
:install_docker
echo ============================================================
echo   开始下载 Docker Desktop for Windows
echo   镜像源优先级: 清华 → 华为云 → 中科大 → 官方源
echo   每个源超时 5 分钟, 失败后自动切换下一个
echo ============================================================

set "DOCKER_INSTALLER=%TEMP%\DockerDesktopInstaller.exe"

REM --- 国内镜像源逐个尝试 ---

REM [1] 清华镜像
echo.
echo [1/4] 尝试清华镜像下载...
powershell -Command "$ProgressPreference='SilentlyContinue'; try { $task=Start-Job -ScriptBlock { param($u,$o) Invoke-WebRequest -Uri $u -OutFile $o -UseBasicParsing } -ArgumentList 'https://mirrors.tuna.tsinghua.edu.cn/docker-ce/win/static/stable/x86_64/Docker%%20Desktop%%20Installer.exe','%DOCKER_INSTALLER%'; if(Wait-Job $task -Timeout 300){Receive-Job $task;Remove-Job $task;exit 0}else{Stop-Job $task;Remove-Job $task;exit 1} } catch { exit 1 }"
if !errorlevel! equ 0 if exist "%DOCKER_INSTALLER%" (
    for %%A in ("%DOCKER_INSTALLER%") do set "FILE_SIZE=%%~zA"
    if !FILE_SIZE! gtr 1000000 (
        echo [OK] 清华镜像下载完成 ^(!FILE_SIZE! 字节^)
        goto :do_install
    )
)
echo [!] 清华镜像下载失败或超时
del "%DOCKER_INSTALLER%" >nul 2>&1

REM [2] 华为云镜像
echo.
echo [2/4] 尝试华为云镜像下载...
powershell -Command "$ProgressPreference='SilentlyContinue'; try { $task=Start-Job -ScriptBlock { param($u,$o) Invoke-WebRequest -Uri $u -OutFile $o -UseBasicParsing } -ArgumentList 'https://mirrors.huaweicloud.com/docker-ce/win/static/stable/x86_64/Docker%%20Desktop%%20Installer.exe','%DOCKER_INSTALLER%'; if(Wait-Job $task -Timeout 300){Receive-Job $task;Remove-Job $task;exit 0}else{Stop-Job $task;Remove-Job $task;exit 1} } catch { exit 1 }"
if !errorlevel! equ 0 if exist "%DOCKER_INSTALLER%" (
    for %%A in ("%DOCKER_INSTALLER%") do set "FILE_SIZE=%%~zA"
    if !FILE_SIZE! gtr 1000000 (
        echo [OK] 华为云镜像下载完成 ^(!FILE_SIZE! 字节^)
        goto :do_install
    )
)
echo [!] 华为云镜像下载失败或超时
del "%DOCKER_INSTALLER%" >nul 2>&1

REM [3] 中科大镜像
echo.
echo [3/4] 尝试中科大镜像下载...
powershell -Command "$ProgressPreference='SilentlyContinue'; try { $task=Start-Job -ScriptBlock { param($u,$o) Invoke-WebRequest -Uri $u -OutFile $o -UseBasicParsing } -ArgumentList 'https://mirrors.ustc.edu.cn/docker-ce/win/static/stable/x86_64/Docker%%20Desktop%%20Installer.exe','%DOCKER_INSTALLER%'; if(Wait-Job $task -Timeout 300){Receive-Job $task;Remove-Job $task;exit 0}else{Stop-Job $task;Remove-Job $task;exit 1} } catch { exit 1 }"
if !errorlevel! equ 0 if exist "%DOCKER_INSTALLER%" (
    for %%A in ("%DOCKER_INSTALLER%") do set "FILE_SIZE=%%~zA"
    if !FILE_SIZE! gtr 1000000 (
        echo [OK] 中科大镜像下载完成 ^(!FILE_SIZE! 字节^)
        goto :do_install
    )
)
echo [!] 中科大镜像下载失败或超时
del "%DOCKER_INSTALLER%" >nul 2>&1

REM [4] 官方源 (国内源全部失败后最后尝试)
echo.
echo [!] 国内镜像源全部失败, 尝试官方源 (可能较慢)...
echo [4/4] 尝试官方源下载...
powershell -Command "$ProgressPreference='SilentlyContinue'; try { $task=Start-Job -ScriptBlock { param($u,$o) Invoke-WebRequest -Uri $u -OutFile $o -UseBasicParsing } -ArgumentList 'https://desktop.docker.com/win/main/amd64/Docker%%20Desktop%%20Installer.exe','%DOCKER_INSTALLER%'; if(Wait-Job $task -Timeout 600){Receive-Job $task;Remove-Job $task;exit 0}else{Stop-Job $task;Remove-Job $task;exit 1} } catch { exit 1 }"
if !errorlevel! equ 0 if exist "%DOCKER_INSTALLER%" (
    for %%A in ("%DOCKER_INSTALLER%") do set "FILE_SIZE=%%~zA"
    if !FILE_SIZE! gtr 1000000 (
        echo [OK] 官方源下载完成 ^(!FILE_SIZE! 字节^)
        goto :do_install
    )
)
echo [!] 官方源下载失败或超时
del "%DOCKER_INSTALLER%" >nul 2>&1

echo.
echo [ERROR] 所有镜像源下载失败!
echo.
echo 请手动下载安装 Docker Desktop:
echo   清华:  https://mirrors.tuna.tsinghua.edu.cn/docker-ce/win/static/stable/x86_64/
echo   官方:  https://www.docker.com/products/docker-desktop/
echo.
echo 安装完成后重新运行此脚本。
pause
exit /b 1

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

REM ============================================================
REM 强制配置: 跳过 Docker Desktop 登录界面 + 镜像加速
REM ============================================================
echo.
echo [*] 配置 Docker Desktop: 强制跳过登录界面...

REM --- 1. 预创建 settings 文件 (标记 onboarding 已完成, 跳过登录弹窗) ---
set "DOCKER_APPDATA=%APPDATA%\Docker"
if not exist "%DOCKER_APPDATA%" mkdir "%DOCKER_APPDATA%"

powershell -NoProfile -Command "if (-not (Test-Path '%DOCKER_APPDATA%\settings-store.json')) { $s = @{displayedOnboarding=$true;analyticsEnabled=$false;openUIOnStartupDisabled=$true;useResourceSaver=$true;useWSL2=$true;theme='system';showTip=$false}; $json = ConvertTo-Json -InputObject $s -Compress; Set-Content -Path '%DOCKER_APPDATA%\settings-store.json' -Value $json -Encoding UTF8; Write-Host '[OK] settings-store.json' }"

powershell -NoProfile -Command "if (-not (Test-Path '%DOCKER_APPDATA%\settings.json')) { $s = @{displayedOnboarding=$true;analyticsEnabled=$false;openUIOnStartupDisabled=$true;useResourceSaver=$true;useWSL2=$true;theme='system';showTip=$false}; $json = ConvertTo-Json -InputObject $s -Compress; Set-Content -Path '%DOCKER_APPDATA%\settings.json' -Value $json -Encoding UTF8; Write-Host '[OK] settings.json' }"

echo [OK] 已预配置 settings (跳过 onboarding/登录)

REM --- 2. 预配置 Docker daemon 镜像加速 ---
set "DOCKER_USERCONFIG=%USERPROFILE%\.docker"
if not exist "%DOCKER_USERCONFIG%" mkdir "%DOCKER_USERCONFIG%"

powershell -NoProfile -Command "if (-not (Test-Path '%DOCKER_USERCONFIG%\daemon.json')) { $d = @{'registry-mirrors'=@('https://mirror.ccs.tencentyun.com','https://docker.mirrors.ustc.edu.cn','https://docker.m.daocloud.io')}; $json = ConvertTo-Json -InputObject $d -Depth 5 -Compress; Set-Content -Path '%DOCKER_USERCONFIG%\daemon.json' -Value $json -Encoding UTF8; Write-Host '[OK] daemon.json' }"

echo [OK] 已配置 Docker 镜像加速

REM --- 3. 创建并启动后台 PowerShell 脚本: 自动关闭登录弹窗 ---
echo [*] 启动后台任务: 自动跳过 Docker Desktop 登录弹窗...
set "SKIP_PS1=%TEMP%\skip_docker_login.ps1"
echo $ErrorActionPreference = 'SilentlyContinue' > "%SKIP_PS1%"
echo Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes >> "%SKIP_PS1%"
echo $elapsed = 0 >> "%SKIP_PS1%"
echo $found = $false >> "%SKIP_PS1%"
echo while (($elapsed -lt 180) -and (-not $found)) { >> "%SKIP_PS1%"
echo   $root = [System.Windows.Automation.AutomationElement]::RootElement >> "%SKIP_PS1%"
echo   $btnNames = @('Skip', 'Continue without signing in', 'Continue', 'Skip tutorial', 'Got it') >> "%SKIP_PS1%"
echo   foreach ($btnName in $btnNames) { >> "%SKIP_PS1%"
echo     $cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $btnName) >> "%SKIP_PS1%"
echo     $btn = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond) >> "%SKIP_PS1%"
echo     if ($btn) { >> "%SKIP_PS1%"
echo       try { >> "%SKIP_PS1%"
echo         $inv = $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern) >> "%SKIP_PS1%"
echo         $inv.Invoke() >> "%SKIP_PS1%"
echo         $found = $true >> "%SKIP_PS1%"
echo       } catch {} >> "%SKIP_PS1%"
echo       break >> "%SKIP_PS1%"
echo     } >> "%SKIP_PS1%"
echo   } >> "%SKIP_PS1%"
echo   Start-Sleep -Seconds 2 >> "%SKIP_PS1%"
echo   $elapsed += 2 >> "%SKIP_PS1%"
echo } >> "%SKIP_PS1%"
start /b powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%SKIP_PS1%"
echo [OK] 后台任务已启动 (自动点击 Skip/Continue, 最多运行 3 分钟)

REM --- 4. 启动 Docker Desktop ---
echo.
echo [!] 请等待 Docker Desktop 启动完毕 (系统托盘出现鲸鱼图标)
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
        echo   提示: 可以启动后在网页界面右上角 设置 → 添加模型 里填 API Key
        echo   .env 文件是可选的, 前端填的密钥优先级更高
        echo.
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
    echo   2. 端口 8000 是否被占用
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   天衍启动成功!
echo ============================================================
echo.
echo   访问地址: http://localhost:8000
echo   首次使用: 在网页右上角 设置 → 添加模型 → 填入 API Key
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
