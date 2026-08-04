#!/usr/bin/env bash
# ============================================================
# 天衍 - Linux/macOS 一键启动脚本
# 功能: 检测 Docker → 未安装则自动安装 → 检测 .env → 启动
# ============================================================
set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
step()  { echo -e "${BLUE}==>${NC} $1"; }

echo ""
echo "============================================================"
echo "  天衍 - 多 Agent 协同创作系统"
echo "  一键启动脚本 (Linux/macOS)"
echo "============================================================"
echo ""

# ---------- 1. 检测 Docker ----------
step "检测 Docker..."
if command -v docker &> /dev/null; then
    docker --version
    info "Docker 已安装"
else
    echo ""
    warn "未检测到 Docker, 开始自动安装..."
    echo ""
    goto_install_docker=true
fi

if [ "${goto_install_docker:-false}" = "true" ]; then
    # ============================================================
    # 2. 安装 Docker (优先阿里云镜像, 10分钟超时切国外源)
    # ============================================================
    echo "============================================================"
    echo "  安装 Docker Engine"
    echo "  优先阿里云镜像, 10 分钟超时后切换官方源"
    echo "============================================================"

    OS_TYPE=""
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_TYPE=$ID
    elif [ "$(uname)" = "Darwin" ]; then
        OS_TYPE="macos"
    fi

    if [ "$OS_TYPE" = "macos" ]; then
        # macOS: 用 Homebrew 安装 Docker Desktop
        echo ""
        step "macOS: 通过 Homebrew 安装 Docker Desktop..."
        if ! command -v brew &> /dev/null; then
            warn "Homebrew 未安装, 正在安装..."
            /bin/bash -c "$(curl -fsSL https://mirrors.aliyun.com/homebrew/install/HEAD/install.sh)" || \
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        fi
        brew install --cask docker || {
            error "Docker Desktop 安装失败"
            echo "请手动下载安装: https://www.docker.com/products/docker-desktop/"
            exit 1
        }
        # 启动 Docker Desktop
        open -a Docker 2>/dev/null || true
        warn "请等待 Docker Desktop 启动完毕..."
    elif [ "$OS_TYPE" = "ubuntu" ] || [ "$OS_TYPE" = "debian" ]; then
        # Ubuntu/Debian: 优先阿里云镜像安装
        echo ""
        step "Ubuntu/Debian: 通过阿里云镜像安装 Docker..."

        # 尝试阿里云镜像 (10分钟超时)
        INSTALL_SCRIPT_ALIYUN="https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg"
        if curl -fsSL --max-time 600 https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg 2>/dev/null; then
            info "阿里云镜像可用, 使用国内源安装..."

            # 添加 Docker 仓库 (阿里云镜像)
            CODENAME=$(. /etc/os-release && echo $VERSION_CODENAME)
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://mirrors.aliyun.com/docker-ce/linux/ubuntu $CODENAME stable" | \
                sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

            sudo apt-get update
            sudo apt-get install -y docker-ce docker-ce-cli containerd.io
        else
            warn "阿里云镜像不可用或超时, 切换到官方源..."
            curl -fsSL https://get.docker.com | sudo sh
        fi

        # 启动 Docker 服务
        sudo systemctl start docker
        sudo systemctl enable docker

        # 将当前用户加入 docker 组 (免 sudo)
        sudo usermod -aG docker $USER 2>/dev/null || true
        warn "已将当前用户加入 docker 组, 可能需要重新登录生效"

    elif [ "$OS_TYPE" = "centos" ] || [ "$OS_TYPE" = "rhel" ] || [ "$OS_TYPE" = "fedora" ]; then
        # CentOS/RHEL/Fedora: 优先阿里云镜像安装
        echo ""
        step "CentOS/RHEL: 通过阿里云镜像安装 Docker..."

        if curl -fsSL --max-time 600 https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo -o /etc/yum.repos.d/docker-ce.repo 2>/dev/null; then
            info "阿里云镜像可用, 使用国内源安装..."
            sudo yum install -y docker-ce docker-ce-cli containerd.io
        else
            warn "阿里云镜像不可用或超时, 切换到官方源..."
            curl -fsSL https://get.docker.com | sudo sh
        fi

        sudo systemctl start docker
        sudo systemctl enable docker
        sudo usermod -aG docker $USER 2>/dev/null || true
        warn "已将当前用户加入 docker 组, 可能需要重新登录生效"

    else
        # 通用安装 (get.docker.com)
        echo ""
        step "未知系统, 使用官方脚本安装..."
        # 先尝试阿里云镜像
        if curl -fsSL --max-time 600 https://mirrors.aliyun.com/docker-engine/install.sh | sudo sh 2>/dev/null; then
            info "阿里云镜像安装成功"
        else
            warn "阿里云镜像不可用或超时, 切换到官方源..."
            curl -fsSL https://get.docker.com | sudo sh
        fi
        sudo systemctl start docker 2>/dev/null || true
        sudo systemctl enable docker 2>/dev/null || true
    fi

    # 验证安装
    echo ""
    step "验证 Docker 安装..."
    if docker --version &> /dev/null; then
        docker --version
        info "Docker 安装成功"
    else
        error "Docker 安装失败"
        echo "请手动安装: https://docs.docker.com/engine/install/"
        exit 1
    fi

    # 等待 Docker 引擎就绪
    echo ""
    step "等待 Docker 引擎就绪..."
    WAIT_COUNT=0
    while ! docker info &> /dev/null; do
        WAIT_COUNT=$((WAIT_COUNT + 1))
        if [ $WAIT_COUNT -ge 60 ]; then
            error "Docker 引擎启动超时 (120秒)"
            echo "请手动启动 Docker 后重新运行: bash tianyan.sh"
            exit 1
        fi
        echo "  等待中... ($WAIT_COUNT/60)"
        sleep 2
    done
    info "Docker 引擎已就绪"
fi

# ============================================================
# 3. 检测 docker-compose
# ============================================================
echo ""
step "检测 docker-compose..."

HAS_COMPOSE=false

# 新版: docker compose (Docker Desktop / 新版 Engine 自带)
if docker compose version &> /dev/null; then
    docker compose version
    info "docker compose 已可用 (内置插件)"
    HAS_COMPOSE=true
    COMPOSE_CMD="docker compose"
fi

# 旧版: docker-compose (独立二进制)
if [ "$HAS_COMPOSE" = "false" ]; then
    if command -v docker-compose &> /dev/null; then
        docker-compose --version
        info "docker-compose 已安装"
        HAS_COMPOSE=true
        COMPOSE_CMD="docker-compose"
    fi
fi

# 未安装则自动安装 docker-compose
if [ "$HAS_COMPOSE" = "false" ]; then
    echo ""
    warn "docker-compose 未安装, 正在安装..."

    # 获取最新版本号
    COMPOSE_VERSION=$(curl -fsSL --max-time 30 https://api.github.com/repos/docker/compose/releases/latest 2>/dev/null | grep '"tag_name"' | sed -E 's/.*"([^"]+)".*/\1/' || echo "v2.24.0")

    # 判断系统架构
    ARCH=$(uname -m)
    case $ARCH in
        x86_64)  COMPOSE_ARCH="x86_64" ;;
        aarch64) COMPOSE_ARCH="aarch64" ;;
        armv7l)  COMPOSE_ARCH="armv7" ;;
        *)       COMPOSE_ARCH="x86_64" ;;
    esac

    # 优先阿里云镜像下载
    ALIYUN_COMPOSE_URL="https://mirrors.aliyun.com/docker-toolbox/linux/compose/${COMPOSE_VERSION}/docker-compose-$(uname -s)-${COMPOSE_ARCH}"
    OFFICIAL_COMPOSE_URL="https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-$(uname -s)-${COMPOSE_ARCH}"

    COMPOSE_BIN=/usr/local/bin/docker-compose

    echo "  尝试阿里云镜像下载..."
    if sudo curl -fsSL --max-time 600 -o "$COMPOSE_BIN" "$ALIYUN_COMPOSE_URL" 2>/dev/null; then
        info "阿里云镜像下载成功"
    else
        warn "阿里云镜像不可用或超时, 切换到官方源..."
        if sudo curl -fsSL --max-time 600 -o "$COMPOSE_BIN" "$OFFICIAL_COMPOSE_URL"; then
            info "官方源下载成功"
        else
            error "docker-compose 下载失败"
            echo "请手动安装: https://docs.docker.com/compose/install/"
            exit 1
        fi
    fi

    sudo chmod +x "$COMPOSE_BIN"
    docker-compose --version
    info "docker-compose 安装成功"
    COMPOSE_CMD="docker-compose"
fi

# ============================================================
# 4. 检测 .env 文件
# ============================================================
echo ""
echo "============================================================"
echo "  检测环境配置文件"
echo "============================================================"

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        warn ".env 文件不存在, 从 .env.example 复制..."
        cp .env.example .env
        info "已创建 .env 文件"
        echo ""
        echo "============================================================"
        echo "  重要: 请编辑 .env 文件, 填入你的 API Key!"
        echo "  至少配置一个:"
        echo "    OPENAI_API_KEY=sk-你的DeepSeek密钥"
        echo "    或 DEEPSEEK_API_KEY=sk-你的DeepSeek密钥"
        echo "============================================================"
        echo ""
        read -p "是否现在编辑 .env? (y/N): " EDIT_ENV
        if [[ "$EDIT_ENV" =~ ^[Yy]$ ]]; then
            ${EDITOR:-vi} .env
        fi
    else
        error ".env 和 .env.example 均不存在"
        exit 1
    fi
else
    info ".env 文件已存在"
fi

# ============================================================
# 5. 启动服务
# ============================================================
echo ""
echo "============================================================"
echo "  启动天衍服务"
echo "============================================================"
echo ""

echo "正在构建并启动容器 (首次构建约 2-3 分钟)..."
echo ""

$COMPOSE_CMD up -d --build

if [ $? -ne 0 ]; then
    echo ""
    error "启动失败!"
    echo "请检查:"
    echo "  1. Docker 是否已启动"
    echo "  2. .env 文件中的 API Key 是否正确"
    echo "  3. 端口 8000 是否被占用"
    exit 1
fi

echo ""
echo "============================================================"
echo "  天衍启动成功!"
echo "============================================================"
echo ""
echo "  访问地址: http://localhost:8000"
echo ""
echo "  常用命令:"
echo "    查看日志:   docker logs -f tianyan"
echo "    停止服务:   $COMPOSE_CMD down"
echo "    重启服务:   $COMPOSE_CMD restart"
echo "    重新构建:   $COMPOSE_CMD up -d --build"
echo ""
echo "============================================================"

# 尝试自动打开浏览器
if command -v xdg-open &> /dev/null; then
    xdg-open http://localhost:8000 2>/dev/null || true
elif command -v open &> /dev/null; then
    open http://localhost:8000 2>/dev/null || true
fi

exit 0
