#!/usr/bin/env bash
# 小说Agent 一键启动 — 桌面对话框
# 双击或 ./start.sh 运行
# 弹出原生对话框 → 点「启动并进入」→ 拉起主服务 → 自动打开浏览器
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# 优先用 tkinter 桌面对话框 (原生窗口,不依赖浏览器)
if python3 -c "import tkinter" 2>/dev/null; then
    exec python3 launcher_tk.py
else
    # 系统无 tkinter → 回退到 web 卡片 (launcher.py)
    echo "[警告] 系统未安装 tkinter,回退到 web 卡片模式"
    echo "       Ubuntu/Debian 安装: sudo apt install python3-tk"
    exec python3 launcher.py
fi
