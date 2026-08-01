#!/usr/bin/env bash
# ============================================================
# Playwright Chromium 内核一键安装 (Linux / macOS)
# 固化国内镜像源, 解决官方源下载慢/卡住的问题
# 用法: ./setup_browser.sh
# ============================================================
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# 国内镜像源 (npmmirror, 阿里云CDN, 国内最快)
export PLAYWRIGHT_DOWNLOAD_HOST="https://cdn.npmmirror.com/binaries/playwright"

echo "============================================================"
echo "  Playwright Chromium 内核安装 (国内镜像加速)"
echo "  镜像源: $PLAYWRIGHT_DOWNLOAD_HOST"
echo "  内核装到系统默认位置 (各平台各自管理, 跨机器不冲突)"
echo "============================================================"
echo

# 1. 确保 playwright python 包已装
if ! python3 -c "import playwright" 2>/dev/null; then
    echo "[1/4] 安装 playwright python 包..."
    python3 -m pip install playwright
else
    echo "[1/4] playwright python 包已就绪"
fi

# 2. 下载 Chromium 内核 (国内镜像)
echo
echo "[2/4] 下载 Chromium 内核 (国内镜像, 约 180MB)..."
python3 -m playwright install chromium

# 3. Linux: 安装系统依赖库 (libatk 等, 需 sudo)
if [ "$(uname -s)" = "Linux" ]; then
    echo
    echo "[3/4] 安装 Linux 系统依赖库 (libatk/libnss3 等, 需 sudo)..."
    if command -v sudo >/dev/null 2>&1; then
        sudo python3 -m playwright install-deps chromium || \
            echo "  [警告] 系统依赖安装失败, 若启动报 'libxxx.so not found' 请手动执行:"
            echo "         sudo python3 -m playwright install-deps chromium"
    else
        python3 -m playwright install-deps chromium || \
            echo "  [警告] 系统依赖安装失败, 请手动执行: python3 -m playwright install-deps chromium"
    fi
else
    echo "[3/4] macOS 无需额外系统依赖, 跳过"
fi

# 4. 验证内核能否启动
echo
echo "[4/4] 验证 Chromium 启动..."
python3 -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=['--no-sandbox','--disable-gpu'])
    pg = b.new_page()
    pg.goto('about:blank')
    print('  内核启动 OK, UA:', pg.evaluate('navigator.userAgent')[:50])
    b.close()
print('  验证通过!')
" && echo "
============================================================
  安装完成! browser_fetch (扫榜/JS渲染抓取) 现在可用
============================================================" || echo "
[错误] 验证失败, 请检查上方日志"
