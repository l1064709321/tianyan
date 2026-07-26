@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==========================================
echo   小说 Agent - GitHub 一键推送脚本
echo   目标仓库: l1064709321/xiaoshuo-agent
echo ==========================================
echo.

REM 1. 初始化仓库（如未初始化）
if not exist .git (
    echo [1/5] 正在初始化 Git 仓库...
    git init
) else (
    echo [1/5] Git 仓库已存在，跳过初始化
)

REM 2. 添加所有文件
echo [2/5] 正在添加所有文件到暂存区...
git add .

REM 3. 提交
echo [3/5] 正在提交...
git commit -m "Initial commit" >nul 2>&1
if errorlevel 1 (
    echo         暂无新变更需要提交，跳过
) else (
    echo         提交成功
)

REM 4. 设置分支和远程仓库
echo [4/5] 设置分支为 main 并关联远程仓库...
git branch -M main 2>nul
git remote remove origin 2>nul
git remote add origin https://github.com/l1064709321/xiaoshuo-agent.git

REM 5. 安全读取 Token 并推送
echo.
echo [5/5] 准备推送到 GitHub...
echo         请在下方的 PowerShell 窗口中粘贴你的 GitHub Token，然后按回车
echo         （Token 不会显示在屏幕上，请放心输入）
echo.

powershell -NoProfile -Command "try { $sec = Read-Host 'GitHub Token' -AsSecureString; $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec); $tok = [Runtime.InteropServices.Marshal]::PtrToStringAuto($ptr); [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr); $tok | Out-File -Encoding utf8 -FilePath '.gh_token_tmp' -NoNewline } catch { exit 1 }"

if not exist .gh_token_tmp (
    echo.
    echo [错误] 未能读取 Token，操作已取消。
    pause
    exit /b 1
)

set /p TOKEN=<.gh_token_tmp
del /f /q .gh_token_tmp >nul 2>&1

REM 使用 Token 推送
git remote set-url origin https://%TOKEN%@github.com/l1064709321/xiaoshuo-agent.git

echo.
echo 正在推送，请稍候...
git push -u origin main

if errorlevel 1 (
    echo.
    echo [推送失败] 可能的原因：
    echo   - Token 无效或已过期
echo   - 仓库地址错误
echo   - 远程仓库已存在冲突的文件
echo.
    git remote set-url origin https://github.com/l1064709321/xiaoshuo-agent.git
    pause
    exit /b 1
)

REM 清理：移除 URL 中的 Token
git remote set-url origin https://github.com/l1064709321/xiaoshuo-agent.git

echo.
echo ==========================================
echo   推送成功！
echo   仓库地址: https://github.com/l1064709321/xiaoshuo-agent
echo ==========================================
pause
