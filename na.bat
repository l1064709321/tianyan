@echo off
:: 天衍 一键管理命令 (Windows)
:: 用法:
::   na          启动服务 (默认)
::   na start    启动
::   na stop     停止
::   na restart  重启
::   na status   查看状态
::   na log      查看日志
::   na url      打印访问地址

setlocal enabledelayedexpansion
set "CMD=%~1"
if "%CMD%"=="" set "CMD=start"

set "DIR=%~dp0"
set "PID_FILE=%TEMP%\tianyan.pid"
set "LOG_FILE=%TEMP%\tianyan.log"
set "PORT=8000"
set "URL=http://localhost:%PORT%/"

:: 检查 Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    where python3 >nul 2>&1
    if %errorlevel% neq 0 (
        echo [错误] 未找到 Python，请先安装 Python 3.10+
        echo 下载地址: https://www.python.org/downloads/
        exit /b 1
    )
    set "PY=python3"
) else (
    set "PY=python"
)

if "%CMD%"=="start" goto :start
if "%CMD%"=="stop" goto :stop
if "%CMD%"=="restart" goto :restart
if "%CMD%"=="status" goto :status
if "%CMD%"=="log" goto :log
if "%CMD%"=="url" goto :url
echo 未知命令: %CMD%
echo 用法: na [start^|stop^|restart^|status^|log^|url]
exit /b 1

:start
if exist "%PID_FILE%" (
    set /p OLD_PID=<"%PID_FILE%"
    tasklist /fi "PID eq !OLD_PID!" 2>nul | find "!OLD_PID!" >nul
    if !errorlevel! equ 0 (
        echo 服务已在运行中 [PID=!OLD_PID!]
        echo 访问: %URL%
        exit /b 0
    )
)
echo 启动 天衍...
cd /d "%DIR%"
start /b %PY% run.py >"%LOG_FILE%" 2>&1
:: 等待服务启动
for /l %%i in (1,1,20) do (
    timeout /t 1 /nobreak >nul
    curl -s http://localhost:%PORT%/api/health >nul 2>&1
    if !errorlevel! equ 0 (
        :: 获取 PID (取最近启动的 python 进程)
        for /f "tokens=2" %%p in ('tasklist /fi "imagename eq python.exe" /fo list 2^>nul ^| find "PID"') do set "NEW_PID=%%p"
        echo !NEW_PID!>"%PID_FILE%"
        echo 服务启动成功 [PID=!NEW_PID!]
        echo 访问: %URL%
        exit /b 0
    )
)
echo [警告] 服务启动超时，请查看日志: na log
exit /b 1

:stop
if not exist "%PID_FILE%" (
    echo 服务未在运行
    exit /b 0
)
set /p PID=<"%PID_FILE%"
taskkill /PID %PID% /F >nul 2>&1
if %errorlevel% equ 0 (
    echo 服务已停止 [PID=%PID%]
) else (
    echo 进程不存在或已退出
)
del "%PID_FILE%" >nul 2>&1
exit /b 0

:restart
call :stop
timeout /t 1 /nobreak >nul
goto :start

:status
if not exist "%PID_FILE%" (
    echo 状态: 未运行
    exit /b 0
)
set /p PID=<"%PID_FILE%"
tasklist /fi "PID eq %PID%" 2>nul | find "%PID%" >nul
if %errorlevel% equ 0 (
    echo 状态: 运行中 [PID=%PID%]
    echo 访问: %URL%
) else (
    echo 状态: 未运行 (PID 文件存在但进程不存在)
    del "%PID_FILE%" >nul 2>&1
)
exit /b 0

:log
if exist "%LOG_FILE%" (
    type "%LOG_FILE%"
) else (
    echo 日志文件不存在: %LOG_FILE%
)
exit /b 0

:url
echo 访问地址: %URL%
exit /b 0
