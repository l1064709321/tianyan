#!/usr/bin/env bash
# 天衍 一键启动
# 双击或 ./start.sh 运行
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo ""
echo "============================================================"
echo "  天衍 - 一键启动"
echo "============================================================"
echo ""

# ===== 检测 Python =====
echo -e "${CYAN}[1/3] 检测 Python 环境...${NC}"
PY=""
if command -v python3 &>/dev/null; then
    PY="python3"
elif command -v python &>/dev/null; then
    PY="python"
else
    echo -e "${RED}  [错误] 未找到 Python 3.10+${NC}"
    echo "  下载地址: https://www.python.org/downloads/"
    exit 1
fi

PYVER=$($PY --version 2>&1 | awk '{print $2}')
echo -e "${GREEN}  ✓ Python ${PYVER}${NC}"

# ===== 检测依赖 =====
echo -e "${CYAN}[2/3] 检测依赖...${NC}"
if ! $PY -c "import fastapi" 2>/dev/null; then
    echo -e "${YELLOW}  [提示] 依赖未安装，正在自动安装...${NC}"
    
    # 配置 pip 镜像源
    $PY -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple 2>/dev/null || true
    
    # 安装核心依赖
    echo "  安装核心依赖 (约1-2分钟)..."
    if ! $PY -m pip install fastapi uvicorn litellm openai pydantic pydantic-settings PyYAML python-multipart httpx python-dotenv -q; then
        echo -e "${RED}  [错误] 核心依赖安装失败${NC}"
        echo "  请手动运行: pip install -r requirements-win.txt"
        exit 1
    fi
    echo -e "${GREEN}  ✓ 核心依赖已安装${NC}"
    
    # 安装扩展依赖
    echo "  安装扩展依赖 (约2-3分钟)..."
    if $PY -m pip install python-docx pypdf ebooklib beautifulsoup4 Markdown readability-lxml lxml chromadb redis psycopg2-binary RestrictedPython -q; then
        echo -e "${GREEN}  ✓ 扩展依赖已安装${NC}"
    else
        echo -e "${YELLOW}  [警告] 部分扩展依赖安装失败，核心功能仍可用${NC}"
    fi
else
    echo -e "${GREEN}  ✓ 依赖已安装${NC}"
fi

# ===== 启动服务 =====
echo -e "${CYAN}[3/3] 启动服务...${NC}"
echo ""
echo "============================================================"
echo "  天衍 启动中..."
echo "  访问地址: http://localhost:8000/"
echo "  按 Ctrl+C 停止服务"
echo "============================================================"
echo ""

export LITELLM_LOCAL_MODEL_COST_MAP=True
$PY run.py
