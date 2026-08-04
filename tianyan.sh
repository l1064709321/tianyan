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
    # 2. 安装 Docker (国内镜像源逐个尝试, 最后才用官方源)
    #    镜像源优先级: 阿里云 → 清华 → 华为云 → 中科大 → 官方
    # ============================================================
    echo "============================================================"
    echo "  安装 Docker Engine"
    echo "  镜像源优先级: 阿里云 → 清华 → 华为云 → 中科大 → 官方源"
    echo "  每个源超时 5 分钟, 失败后自动切换下一个"
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
            /bin/bash -c "$(curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/git/homebrew/install/HEAD/install.sh)" || \
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        fi
        brew install --cask docker || {
            error "Docker Desktop 安装失败"
            echo "请手动下载安装: https://www.docker.com/products/docker-desktop/"
            exit 1
        }
        open -a Docker 2>/dev/null || true
        warn "请等待 Docker Desktop 启动完毕..."

    elif [ "$OS_TYPE" = "ubuntu" ] || [ "$OS_TYPE" = "debian" ]; then
        # Ubuntu/Debian: 多国内镜像源逐个尝试
        echo ""
        step "Ubuntu/Debian: 安装 Docker..."
        CODENAME=$(. /etc/os-release && echo $VERSION_CODENAME)
        ARCH=$(dpkg --print-architecture)
        DOCKER_INSTALLED=false

        # 国内镜像源列表 (GPG key URL, apt repo URL)
        MIRRORS=(
            "阿里云|https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg|https://mirrors.aliyun.com/docker-ce/linux/ubuntu"
            "清华|https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu/gpg|https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu"
            "华为云|https://mirrors.huaweicloud.com/docker-ce/linux/ubuntu/gpg|https://mirrors.huaweicloud.com/docker-ce/linux/ubuntu"
            "中科大|https://mirrors.ustc.edu.cn/docker-ce/linux/ubuntu/gpg|https://mirrors.ustc.edu.cn/docker-ce/linux/ubuntu"
        )

        for MIRROR_ENTRY in "${MIRRORS[@]}"; do
            MIRROR_NAME="${MIRROR_ENTRY%%|*}"
            REST="${MIRROR_ENTRY#*|}"
            GPG_URL="${REST%%|*}"
            REPO_BASE="${REST##*|}"

            echo ""
            echo "  尝试 ${MIRROR_NAME} 镜像..."
            if curl -fsSL --max-time 300 "$GPG_URL" | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg 2>/dev/null; then
                info "${MIRROR_NAME} 镜像可用, 使用该源安装..."
                echo "deb [arch=${ARCH} signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] ${REPO_BASE} ${CODENAME} stable" | \
                    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
                sudo apt-get update
                sudo apt-get install -y docker-ce docker-ce-cli containerd.io
                if docker --version &> /dev/null; then
                    info "${MIRROR_NAME} 镜像安装成功"
                    DOCKER_INSTALLED=true
                    break
                fi
            fi
            warn "${MIRROR_NAME} 镜像不可用或超时, 切换下一个..."
        done

        # 国内源全部失败, 最后尝试官方源
        if [ "$DOCKER_INSTALLED" = "false" ]; then
            echo ""
            warn "国内镜像源全部失败, 尝试官方源 (可能较慢)..."
            curl -fsSL https://get.docker.com | sudo sh
        fi

        sudo systemctl start docker
        sudo systemctl enable docker
        sudo usermod -aG docker $USER 2>/dev/null || true
        warn "已将当前用户加入 docker 组, 可能需要重新登录生效"

    elif [ "$OS_TYPE" = "centos" ] || [ "$OS_TYPE" = "rhel" ] || [ "$OS_TYPE" = "fedora" ]; then
        # CentOS/RHEL/Fedora: 多国内镜像源逐个尝试
        echo ""
        step "CentOS/RHEL: 安装 Docker..."
        DOCKER_INSTALLED=false

        MIRRORS=(
            "阿里云|https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo"
            "清华|https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/centos/docker-ce.repo"
            "华为云|https://mirrors.huaweicloud.com/docker-ce/linux/centos/docker-ce.repo"
            "中科大|https://mirrors.ustc.edu.cn/docker-ce/linux/centos/docker-ce.repo"
        )

        for MIRROR_ENTRY in "${MIRRORS[@]}"; do
            MIRROR_NAME="${MIRROR_ENTRY%%|*}"
            REPO_URL="${MIRROR_ENTRY##*|}"

            echo ""
            echo "  尝试 ${MIRROR_NAME} 镜像..."
            if curl -fsSL --max-time 300 "$REPO_URL" -o /etc/yum.repos.d/docker-ce.repo 2>/dev/null; then
                info "${MIRROR_NAME} 镜像可用, 使用该源安装..."
                sudo yum install -y docker-ce docker-ce-cli containerd.io
                if docker --version &> /dev/null; then
                    info "${MIRROR_NAME} 镜像安装成功"
                    DOCKER_INSTALLED=true
                    break
                fi
            fi
            warn "${MIRROR_NAME} 镜像不可用或超时, 切换下一个..."
        done

        # 国内源全部失败, 最后尝试官方源
        if [ "$DOCKER_INSTALLED" = "false" ]; then
            echo ""
            warn "国内镜像源全部失败, 尝试官方源 (可能较慢)..."
            curl -fsSL https://get.docker.com | sudo sh
        fi

        sudo systemctl start docker
        sudo systemctl enable docker
        sudo usermod -aG docker $USER 2>/dev/null || true
        warn "已将当前用户加入 docker 组, 可能需要重新登录生效"

    else
        # 通用安装: 多国内镜像源逐个尝试
        echo ""
        step "未知系统, 使用通用脚本安装..."
        DOCKER_INSTALLED=false

        # 国内镜像源列表
        MIRRORS=(
            "阿里云|https://mirrors.aliyun.com/docker-engine/install.sh"
            "清华|https://mirrors.tuna.tsinghua.edu.cn/docker-engine/install.sh"
            "华为云|https://mirrors.huaweicloud.com/docker-engine/install.sh"
        )

        for MIRROR_ENTRY in "${MIRRORS[@]}"; do
            MIRROR_NAME="${MIRROR_ENTRY%%|*}"
            SCRIPT_URL="${MIRROR_ENTRY##*|}"

            echo ""
            echo "  尝试 ${MIRROR_NAME} 镜像..."
            if curl -fsSL --max-time 300 "$SCRIPT_URL" | sudo sh 2>/dev/null; then
                if docker --version &> /dev/null; then
                    info "${MIRROR_NAME} 镜像安装成功"
                    DOCKER_INSTALLED=true
                    break
                fi
            fi
            warn "${MIRROR_NAME} 镜像不可用或超时, 切换下一个..."
        done

        # 国内源全部失败, 最后尝试官方源
        if [ "$DOCKER_INSTALLED" = "false" ]; then
            echo ""
            warn "国内镜像源全部失败, 尝试官方源 (可能较慢)..."
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
# 2.5. 配置 Docker 镜像加速 (无论新装还是已有都检查)
# ============================================================
echo ""
echo "============================================================"
echo "  配置 Docker 镜像加速"
echo "============================================================"

DAEMON_JSON="/etc/docker/daemon.json"
if [ "$(uname)" = "Darwin" ]; then
    # macOS: Docker Desktop 通过 settings 配置, 不需要 daemon.json
    info "macOS: Docker Desktop 自带镜像配置, 跳过"
elif [ ! -f "$DAEMON_JSON" ]; then
    sudo mkdir -p /etc/docker
    echo '{"registry-mirrors":["https://mirror.ccs.tencentyun.com","https://docker.mirrors.ustc.edu.cn","https://docker.m.daocloud.io"]}' | sudo tee "$DAEMON_JSON" > /dev/null
    sudo systemctl daemon-reload 2>/dev/null || true
    sudo systemctl restart docker 2>/dev/null || true
    info "已配置 Docker 镜像加速 (腾讯云/中科大/DaoCloud)"
else
    info "daemon.json 已存在, 跳过配置"
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

    COMPOSE_BIN=/usr/local/bin/docker-compose
    COMPOSE_FILE="docker-compose-$(uname -s)-${COMPOSE_ARCH}"

    # 国内镜像源逐个尝试
    MIRRORS_COMPOSE=(
        "阿里云|https://mirrors.aliyun.com/docker-toolbox/linux/compose/${COMPOSE_VERSION}/${COMPOSE_FILE}"
        "清华|https://mirrors.tuna.tsinghua.edu.cn/docker-toolbox/linux/compose/${COMPOSE_VERSION}/${COMPOSE_FILE}"
        "华为云|https://mirrors.huaweicloud.com/docker-toolbox/linux/compose/${COMPOSE_VERSION}/${COMPOSE_FILE}"
    )
    OFFICIAL_COMPOSE_URL="https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/${COMPOSE_FILE}"

    COMPOSE_DOWNLOADED=false
    for MIRROR_ENTRY in "${MIRRORS_COMPOSE[@]}"; do
        MIRROR_NAME="${MIRROR_ENTRY%%|*}"
        DOWNLOAD_URL="${MIRROR_ENTRY##*|}"

        echo "  尝试 ${MIRROR_NAME} 镜像下载..."
        if sudo curl -fsSL --max-time 300 -o "$COMPOSE_BIN" "$DOWNLOAD_URL" 2>/dev/null; then
            info "${MIRROR_NAME} 镜像下载成功"
            COMPOSE_DOWNLOADED=true
            break
        fi
        warn "${MIRROR_NAME} 镜像不可用或超时, 切换下一个..."
    done

    # 国内源全部失败, 最后尝试官方源
    if [ "$COMPOSE_DOWNLOADED" = "false" ]; then
        echo ""
        warn "国内镜像源全部失败, 尝试官方源 (可能较慢)..."
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
        echo "  提示: 可以启动后在网页界面右上角 设置 → 添加模型 里填 API Key"
        echo "  .env 文件是可选的, 前端填的密钥优先级更高"
        echo ""
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
    echo "  2. 端口 8000 是否被占用"
    exit 1
fi

echo ""
echo "============================================================"
echo "  天衍启动成功!"
echo "============================================================"
echo ""
echo "  访问地址: http://localhost:8000"
echo "  首次使用: 在网页右上角 设置 → 添加模型 → 填入 API Key"
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
