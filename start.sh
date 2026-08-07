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

    # 国内镜像源列表 (优先), 国外源兜底
    MIRRORS=(
        "https://pypi.tuna.tsinghua.edu.cn/simple"
        "https://mirrors.aliyun.com/pypi/simple/"
        "https://repo.huaweicloud.com/repository/pypi/simple/"
        "https://pypi.mirrors.ustc.edu.cn/simple/"
        "https://mirrors.cloud.tencent.com/pypi/simple/"
        "https://pypi.douban.com/simple/"
        "https://mirrors.163.com/pypi/simple/"
        "https://pypi.org/simple/"
    )

    # 逐源尝试安装, 每个源 10 分钟超时
    install_with_mirrors() {
        local pkg_desc="$1"
        shift
        local packages=("$@")
        for MIRROR_URL in "${MIRRORS[@]}"; do
            local HOSTNAME=$(echo "$MIRROR_URL" | sed -E 's|https?://([^/]+).*|\1|')
            echo "    尝试 $HOSTNAME ..."
            # timeout 600s = 10 分钟; 用 --timeout 5 给 pip 单次请求超时
            if timeout 600 $PY -m pip install "${packages[@]}" -i "$MIRROR_URL" --trusted-host "$HOSTNAME" --timeout 5 --retries 2 -q 2>&1; then
                echo -e "${GREEN}    ✓ $pkg_desc 安装成功${NC}"
                return 0
            fi
            echo -e "${YELLOW}    ! $HOSTNAME 失败或超时, 切换下一个源...${NC}"
        done
        echo -e "${RED}    ✗ $pkg_desc 安装失败: 所有源均不可用${NC}"
        return 1
    }

    # 安装核心依赖
    echo "  安装核心依赖 (每个源最多 10 分钟, 自动切换)..."
    CORE_PKGS=(fastapi uvicorn litellm openai pydantic pydantic-settings PyYAML python-multipart httpx python-dotenv)
    if ! install_with_mirrors "核心依赖" "${CORE_PKGS[@]}"; then
        echo -e "${RED}  [错误] 核心依赖安装失败${NC}"
        echo "  请手动运行: pip install -r requirements.txt"
        exit 1
    fi

    # 安装扩展依赖
    echo "  安装扩展依赖 (每个源最多 10 分钟, 自动切换)..."
    EXT_PKGS=(python-docx pypdf ebooklib beautifulsoup4 Markdown readability-lxml lxml chromadb redis psycopg2-binary RestrictedPython)
    if ! install_with_mirrors "扩展依赖" "${EXT_PKGS[@]}"; then
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
