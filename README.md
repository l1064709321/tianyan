# 天衍

一个多Agent协同的**天衍**(Web 界面)。7 个 agent 协同,按 8 阶段工作流完成从扫榜调研到定稿入库的完整长篇创作闭环,内置「毒舌总编」审稿机制与质检打回循环。

![Python](https://img.shields.io/badge/Python-3.8+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green)
![Docker](https://img.shields.io/badge/Docker-支持-2496ED)
[![自定义协议](https://img.shields.io/badge/📄-自定义协议-0052d9)](USER_AGREEMENT.md)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL--3.0-blueviolet)](https://www.gnu.org/licenses/agpl-3.0.html)
[![License: GPL v3](https://img.shields.io/badge/License-GPL--3.0-red)](https://www.gnu.org/licenses/gpl-3.0.html)
---
# ｛版本还在迭代中测试，如果有问题，请第一时间进行反馈。｝
## ✨ 核心特性

### 5 阶段写作流水线(强制全流程)

| 阶段 | 名称 | 负责 agent | 说明 |
|------|------|-----------|------|
| 1 | 汇总需求 | orchestrator | 总编理解用户意图,query_project 了解现状,拆解写作指令 |
| 2 | 主笔写作 | narrative-writer | **强制5步收集**:项目状态→角色档案→上下文→风格缓存→参考few-shot,全部收集完才动笔 |
| 3 | 风格分析 | consistency-checker | analyze_style + cache_style 对比前文,判断:沿用旧风格/新增风格(用户要求)/写歪了 |
| 4 | 质检审核 | consistency-checker | four_check + detect_ai + full_audit,**不通过打回主笔重写**(附具体修改指令) |
| 5 | 总编验收 | orchestrator | review_chapter 毒舌审稿,评分<7打回,≥7输出给用户 |

**驳回标准**:S1/S2冲突 / 风格写歪 / AI味≥30 / 角色OOC / 字数不足,最多打回3次。

### 7-agent 架构

| Agent | 角色 | 沙盒权限 |
|-------|------|---------|
| orchestrator | 总编(全局调度 + 毒舌审稿) | read-write |
| story-architect | 架构师(扫榜/拆书/大纲) | read-write |
| narrative-writer | 主笔(正文 + 去 AI 味) | read-write |
| character-designer | 角色师(人设 + 对话) | read-write |
| consistency-checker | 质检员(一致性审查) | **read-only** |
| story-explorer | 资料员(上下文加载) | **read-only** |
| worldbuilder | 设定管理员(世界观/地点/时间线) | read-write |

### 多项目记忆隔离

每个项目(小说)拥有独立的记忆空间:
- **风格偏好**:语气、节奏、参考作者
- **角色档案**:姓名、角色定位、性格特征
- **世界观设定**:时代、力量体系、地点
- **剧情进度**:总章数、已完成章数、当前冲突
- **对话历史**:短期记忆(Redis),切换项目时自动清空
- **向量索引**:语义检索(ChromaDB),按 `project_id` 分区

切换项目时,Agent 的行为风格完全切换,切回原项目时之前的角色和风格完整保留。

### 沙箱安全

代码执行隔离采用双重防护:
- **系统级**:firejail(`--net=none --noroot`,断网+降权) + pypy3(独立解释器)
- **Python 级**:RestrictedPython 预检(拦截危险 import/eval/exec)

---

## 🚀 快速开始(3 步搞定)

### 第 1 步:克隆仓库

```bash
git clone https://github.com/l1064709321/tianyan.git
cd tianyan
```

### 第 2 步:启动服务

**Windows**: 双击 `tianyan.bat`

**Linux / macOS**:
```bash
chmod +x tianyan.sh
./tianyan.sh
```

脚本会自动检测 Docker → 未安装则自动安装 → 构建镜像 → 启动容器。

> **代码已经打包进 Docker 镜像里了。** `docker-compose up -d --build` 构建时,所有源码会被 `COPY . .` 复制进镜像,编译为 `.pyc` 后删除 `.py` 源文件(代码保护)。你不需要在容器外保留源码,也不需要挂载代码目录,镜像里自带完整应用。

### 第 3 步:在前端界面填入 API Key

**不需要手动编辑 `.env` 文件!** 前端界面自带密钥配置功能:

1. 打开浏览器访问 `http://localhost:8000`
2. 点击页面右上角 **设置** 按钮
3. 选择 **添加模型**
4. 填入 API Key 和模型信息
5. 保存即可,密钥会自动持久化,重启不丢失

前端填入的密钥优先级高于 `.env` 文件,两者配一个就行。

---

## 📋 目录

1. [Docker 安装指导](#1-docker-安装指导)
2. [环境变量配置说明](#2-环境变量配置说明)
3. [一键启动说明](#3-一键启动说明)
4. [项目创建与切换](#4-项目创建与切换)
5. [多项目记忆隔离说明](#5-多项目记忆隔离说明)
6. [沙箱安全说明](#6-沙箱安全说明)
7. [常用命令](#7-常用命令)
8. [Docker 镜像内部结构](#8-docker-镜像内部结构)

---

## 1. Docker 安装指导

### 方式 A:一键脚本自动安装(推荐)

天衍提供 `tianyan.bat`(Windows)和 `tianyan.sh`(Linux/macOS)一键脚本,自动检测并安装 Docker:

- **Windows**:优先清华镜像下载,10分钟超时后切换国外源
- **Linux**:优先阿里云镜像安装,10分钟超时后切换官方源
- **macOS**:通过 Homebrew 安装 Docker Desktop

### 方式 B:手动安装 Docker

#### Windows

1. 下载 Docker Desktop:
   - 官方:https://www.docker.com/products/docker-desktop/
   - 清华镜像:https://mirrors.tuna.tsinghua.edu.cn/docker-ce/win/static/stable/x86_64/
2. 双击安装,重启电脑
3. 启动 Docker Desktop,等待鲸鱼图标变为稳定状态

#### Linux (Ubuntu/Debian)

```bash
# 阿里云镜像安装 (国内推荐)
curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://mirrors.aliyun.com/docker-ce/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io

# 官方源安装 (国外)
curl -fsSL https://get.docker.com | sudo sh

# 启动并设置开机自启
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker $USER  # 免 sudo
```

#### Linux (CentOS/RHEL)

```bash
# 阿里云镜像
sudo yum-config-manager --add-repo https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo
sudo yum install -y docker-ce docker-ce-cli containerd.io

# 启动
sudo systemctl start docker
sudo systemctl enable docker
```

#### macOS

```bash
# Homebrew 安装
brew install --cask docker
# 启动 Docker Desktop
open -a Docker
```

### 配置 Docker 国内镜像加速

安装完成后,配置国内镜像加速拉取 Docker Hub 镜像:

```json
// /etc/docker/daemon.json (Linux) 或 Docker Desktop → Settings → Docker Engine
{
  "registry-mirrors": [
    "https://mirror.ccs.tencentyun.com",
    "https://docker.mirrors.ustc.edu.cn"
  ]
}
```

配置后重启 Docker:
```bash
sudo systemctl restart docker  # Linux
# macOS/Windows: 重启 Docker Desktop
```

---

## 2. 环境变量配置说明

### 两种配置方式(任选其一)

| 方式 | 适用场景 | 操作 |
|------|---------|------|
| **前端界面填入(推荐)** | 大多数用户 | 启动后在网页右上角 设置 → 添加模型 |
| **.env 文件配置** | 批量预配 / CI/CD | `cp .env.example .env` 后编辑 |

两种方式可以共存,前端填入的密钥优先级更高。

### .env 文件配置(可选)

如果偏好用 `.env` 文件:

```bash
cp .env.example .env
# 编辑 .env, 填入至少一个 API Key
```

### 完整变量说明

| 变量名 | 说明 | 默认值 | 必填 |
|--------|------|--------|------|
| **模型配置** | | | |
| `OPENAI_API_KEY` | DeepSeek/OpenAI API Key | - | 否(前端可填) |
| `OPENAI_BASE_URL` | API 基础 URL | `https://api.deepseek.com/v1` | 否 |
| `OPENAI_MODEL` | 默认模型名 | `deepseek-chat` | 否 |
| `CUSTOM_API_KEY` | 自定义模型 Key(优先级更高) | - | 否 |
| `CUSTOM_BASE_URL` | 自定义模型 Base URL | - | 否 |
| `CUSTOM_MODEL_NAME` | 自定义模型名 | - | 否 |
| **其他模型** | | | |
| `DEEPSEEK_API_KEY` | DeepSeek 专用 Key | - | 否 |
| `GEMINI_API_KEY` | Google Gemini Key | - | 否 |
| `ANTHROPIC_API_KEY` | Anthropic Claude Key | - | 否 |
| `DASHSCOPE_API_KEY` | 通义千问 Key | - | 否 |
| `ZAI_API_KEY` | 智谱 GLM Key | - | 否 |
| `MOONSHOT_API_KEY` | Kimi Key | - | 否 |
| `XAI_API_KEY` | xAI Grok Key | - | 否 |
| `MISTRAL_API_KEY` | Mistral Key | - | 否 |
| `VOLCENGINE_API_KEY` | 豆包/火山引擎 Key | - | 否 |
| **沙箱配置** | | | |
| `SANDBOX_ENABLED` | 是否启用沙箱 | `true` | 否 |
| `SANDBOX_TIMEOUT` | 沙箱执行超时(秒) | `30` | 否 |
| **记忆系统** | | | |
| `MEMORY_ENABLED` | 是否启用多项目记忆 | `true` | 否 |
| `REDIS_URL` | Redis 连接地址 | `redis://localhost:6379/0` | 否(Docker 自动配置) |
| `DATABASE_URL` | PostgreSQL 连接地址 | - | 否(Docker 自动配置) |
| `CHROMA_PATH` | Chroma 向量库存储路径 | `./data/chroma` | 否 |
| `DATA_DIR` | 数据目录 | `./data` | 否 |
| **通用** | | | |
| `PROXY` | HTTP 代理(访问国外模型用) | - | 否 |
| `TIMEOUT_SECONDS` | 请求超时(秒) | `600` | 否 |
| `LOG_LEVEL` | 日志级别 | `info` | 否 |

### 代理配置(国内访问国外模型)

如果使用 OpenAI/Gemini/Anthropic 等国外模型,需要配置代理:

```bash
# 本地开发: 填本地代理地址
PROXY=http://127.0.0.1:7890

# Docker 容器内: 用 host.docker.internal 访问宿主机
PROXY=http://host.docker.internal:7890
```

推荐使用 **DeepSeek** (国内直连,无需代理):https://platform.deepseek.com

---

## 3. 一键启动说明

### Windows

```cmd
# 双击 tianyan.bat 或在命令行运行
tianyan.bat
```

脚本执行流程:
1. 检测 Docker → 未安装则自动下载(清华镜像优先,10分钟超时切国外源)
2. 安装 Docker Desktop → 启动 → 等待引擎就绪
3. 检测 `.env` → 不存在则从 `.env.example` 复制(可后续在前端界面填密钥)
4. 执行 `docker-compose up -d --build`
5. 自动打开浏览器访问 `http://localhost:8000`

### Linux / macOS

```bash
# 赋予执行权限 (首次)
chmod +x tianyan.sh

# 运行
./tianyan.sh
```

脚本执行流程:
1. 检测 Docker → 未安装则自动安装(阿里云镜像优先,10分钟超时切官方源)
2. 检测 docker-compose → 未安装则自动下载
3. 检测 `.env` → 不存在则从 `.env.example` 复制(可后续在前端界面填密钥)
4. 执行 `docker-compose up -d --build`
5. 自动打开浏览器访问 `http://localhost:8000`

### 手动启动(已安装 Docker)

```bash
# 1. 克隆仓库
git clone https://github.com/l1064709321/tianyan.git
cd tianyan

# 2. 启动 (首次约 2-3 分钟构建镜像)
#    .env 不是必须的, 可以启动后在网页界面填密钥
docker-compose up -d --build

# 3. 访问 http://localhost:8000
# 4. 在网页右上角 设置 → 添加模型 → 填入 API Key
```

### 本地开发(不使用 Docker)

```bash
# 1. 克隆仓库
git clone https://github.com/l1064709321/tianyan.git
cd tianyan

# 2. 装依赖
pip install -r requirements.txt

# 3. 启动
python run.py
# 或
python boot.py

# 4. 访问 http://localhost:8000
# 5. 在网页界面填入 API Key
```

---

## 4. 项目创建与切换

### 创建新项目

1. 打开 Web 界面 `http://localhost:8000`
2. 点击左侧边栏「新建项目」按钮
3. 在弹出对话框中输入:
   - 项目名称(如:仙侠长篇)
   - 项目类型(如:仙侠、都市悬疑、历史等)
4. 点击确认创建

也可以通过 API 创建:

```bash
curl -X POST http://localhost:8000/api/projects \
  -H "Content-Type: application/json" \
  -d '{"name": "仙侠长篇", "genre": "仙侠"}'
```

### 切换项目

1. 在左侧边栏项目列表中,点击目标项目
2. 系统自动:
   - 清空当前项目的短期对话记忆
   - 加载目标项目的完整长期记忆(风格、角色、世界观、剧情进度)
   - 生成新的系统提示词给 Agent
3. 顶部显示当前项目名称和类型

### 项目管理

- **重命名**:点击项目管理下拉菜单 → 重命名
- **删除**:点击项目管理下拉菜单 → 删除(会删除该项目的所有数据)
- **切换**:点击项目管理下拉菜单 → 选择目标项目

### API 接口

```bash
# 项目管理
POST   /api/projects              # 创建新项目
GET    /api/projects              # 获取所有项目列表
GET    /api/projects/{project_id} # 获取项目详情
PUT    /api/projects/{project_id} # 更新项目(风格、世界观等)
DELETE /api/projects/{project_id} # 删除项目

# 角色管理
POST   /api/projects/{project_id}/characters  # 添加角色
GET    /api/projects/{project_id}/characters  # 获取角色列表

# 对话
POST   /api/projects/{project_id}/chat       # 发送消息(自动关联记忆)
```

---

## 5. 多项目记忆隔离说明

### 架构设计

```
┌─────────────────────────────────────────────┐
│              MemoryManager                   │
├─────────────┬──────────────┬────────────────┤
│ PostgreSQL  │    Redis     │     Chroma     │
│ (长期记忆)   │ (短期记忆)    │  (向量检索)    │
├─────────────┼──────────────┼────────────────┤
│ 项目表       │ conv:{pid}   │ project_{pid}  │
│ 角色表       │ (最近50条)    │ (语义索引)     │
│ 章节表       │              │                │
└─────────────┴──────────────┴────────────────┘
```

### 隔离机制

每个项目通过 `project_id` 完全隔离:

| 存储层 | 隔离方式 | 数据内容 |
|--------|---------|---------|
| PostgreSQL | `project_id` 字段 | 风格偏好、角色档案、世界观、剧情进度、章节 |
| Redis | `conv:{project_id}` Key | 最近 50 轮对话(短期记忆) |
| ChromaDB | `project_{project_id}` 集合 | 角色描述、世界观设定、章节内容向量 |

### 切换项目流程

1. **清空短期记忆**:当前项目的 Redis 对话不再加载到 Agent 上下文
2. **加载长期记忆**:从 PostgreSQL 读取目标项目的风格、角色、世界观、剧情进度
3. **加载对话历史**:从 Redis 读取目标项目最近的 10 轮对话
4. **生成系统提示词**:将项目上下文注入 Agent 的 system prompt
5. **Agent 行为切换**:Agent 根据新项目的风格偏好调整写作语气、节奏

### 降级策略

系统设计了完善的降级机制,确保核心功能始终可用:

| 组件 | 不可用时降级到 | 影响 |
|------|-------------|------|
| PostgreSQL | SQLite(内置) | 多项目隔离变弱,但功能正常 |
| Redis | 内存字典(单进程) | 对话历史重启后丢失 |
| ChromaDB | 关键词检索(内置) | 语义检索精度降低 |

---

## 6. 沙箱安全说明

### 双重隔离架构

```
用户代码
    │
    ▼
┌──────────────────┐
│ RestrictedPython │  ← Python 级预检:拦截危险 import/eval/exec
│ (代码预检)       │
└────────┬─────────┘
         │ 通过预检
         ▼
┌──────────────────┐
│ firejail         │  ← 系统级隔离:断网(--net=none) + 降权(--noroot)
│ + pypy3          │  ← 独立解释器:不共享主进程 GIL/内存
└──────────────────┘
```

### 沙箱级别

根据可用工具自动选择最强隔离:

| 级别 | 工具组合 | 隔离强度 | 说明 |
|------|---------|---------|------|
| 最强 | firejail + pypy3 | 断网+降权+独立解释器 | Docker 容器内默认 |
| 强 | firejail + python | 断网+降权 | pypy3 不可用时 |
| 中 | pypy3 only | 独立解释器 | firejail 不可用时 |
| 弱 | python + 超时 | 仅超时控制 | 两者都不可用时(记录警告) |

### 配置

```bash
# .env 中配置 (可选)
SANDBOX_ENABLED=true    # 启用沙箱 (false 则完全禁用代码执行)
SANDBOX_TIMEOUT=30      # 超时秒数 (默认 30 秒)
```

### 安全保障

- **断网隔离**:firejail `--net=none` 禁止所有网络访问
- **权限降级**:firejail `--noroot` 确保非 root 执行
- **代码预检**:RestrictedPython 拦截 `import os`、`eval`、`exec`、`open` 等
- **超时控制**:subprocess 强制超时,防止死循环
- **临时文件**:执行完毕自动清理,不残留

---

## 7. 常用命令

### Docker 容器管理

```bash
# 查看运行状态
docker-compose ps

# 查看实时日志
docker logs -f tianyan

# 查看最近 100 行日志
docker logs --tail 100 tianyan

# 停止所有服务
docker-compose down

# 重启主服务
docker-compose restart tianyan

# 重启所有服务
docker-compose restart

# 重新构建并启动 (代码更新后)
docker-compose up -d --build

# 查看容器资源占用
docker stats tianyan
```

### 容器内验证

```bash
# 进入容器
docker exec -it tianyan bash

# 验证 Python 路径 (应显示 /app/venv/bin/python)
which python

# 验证沙箱工具
firejail --version
pypy3 --version

# 验证源码编译 (应只显示 boot.py)
find /app -name "*.py" -not -path "*/venv/*"
```

### 数据库管理

```bash
# 进入 PostgreSQL
docker exec -it tianyan-postgres psql -U tianyan -d tianyan

# 查看项目列表
SELECT project_id, project_name, genre FROM projects;

# 查看角色
SELECT * FROM characters;

# 进入 Redis
docker exec -it tianyan-redis redis-cli

# 查看对话 keys
KEYS conv:*
```

### 日志文件

```bash
# 应用日志
ls -la ./logs/

# 实时查看
tail -f ./logs/*.log
```

### 清理数据(谨慎!)

```bash
# 停止并删除容器 (保留数据)
docker-compose down

# 停止并删除容器 + 数据 (慎用! 会丢失所有项目数据)
docker-compose down -v

# 清理数据目录
rm -rf ./data/*
```

---

## 8. Docker 镜像内部结构

### 代码已经打包进镜像

项目源码在构建镜像时已经打包进去,不需要在宿主机保留源码:

```
Docker 镜像内部 (/app)
├── boot.py              ← 唯一保留的 .py 源文件 (启动入口)
├── venv/                ← Python 虚拟环境 (所有依赖装在这里)
│   └── bin/python
├── app/
│   ├── __init__.py      ← 保留 (包标识)
│   ├── agents.pyc       ← 编译后的字节码 (源码已删除)
│   ├── tools.pyc
│   ├── agent.pyc
│   ├── llm.pyc
│   ├── config.pyc
│   ├── store.pyc
│   ├── server.pyc
│   ├── sandbox.pyc
│   ├── memory_manager.pyc
│   └── ... (其他 .pyc)
├── web/                 ← 前端文件 (HTML/CSS/JS 不编译)
│   ├── index.html
│   ├── app.js
│   └── style.css
└── data/                ← 数据目录 (挂载到宿主机, 持久化)
```

### 构建流程

```
COPY requirements.txt → pip install (清华镜像)
        ↓
COPY . .              → 复制全部源码到 /app
        ↓
compileall -b         → 编译 .py 为 .pyc
        ↓
删除 .py 源文件        → 只保留 boot.py 和 __init__.py
        ↓
useradd appuser       → 创建非 root 用户
        ↓
CMD ["python", "boot.py"]  → 启动服务
```

### 数据持久化

以下数据通过 volume 挂载到宿主机,容器重建不丢失:

| 容器路径 | 宿主机路径 | 内容 |
|---------|-----------|------|
| `/data` | `./data` | 项目数据、向量索引、用户配置 |
| `/app/logs` | `./logs` | 应用日志 |

**注意**:代码本身不在挂载列表里,代码在镜像内,更新代码需要重新构建镜像(`docker-compose up -d --build`)。

---

## 📁 项目结构

```
tianyan/
├── app/
│   ├── agents.py           # 7-agent 定义 + 工作流 + 毒舌审稿
│   ├── tools.py            # 11 个工具(扫榜/拆书/大纲/续写/润色/...)
│   ├── agent.py            # agentic loop + delegate 委派机制
│   ├── llm.py              # litellm 封装(stream/chat) + 连接池
│   ├── config.py           # 配置 + 15 家厂商预设 + 持久化
│   ├── store.py            # SQLite 持久化层
│   ├── server.py           # FastAPI 路由(REST + SSE)
│   ├── exporter.py         # 多格式导出
│   ├── sandbox.py          # 沙箱代码执行器(firejail + pypy3)
│   ├── memory_manager.py   # 多项目记忆隔离管理器
│   └── ...
├── web/
│   ├── index.html          # Web 界面
│   ├── app.js              # 前端逻辑
│   └── style.css           # UI 样式
├── Dockerfile              # Docker 构建文件
├── docker-compose.yml      # 多容器编排(app + postgres + redis)
├── .env.example            # 环境变量模板 (可选, 前端界面也能填)
├── .dockerignore           # Docker 构建排除
├── boot.py                 # 容器启动入口
├── requirements.txt        # Python 依赖
├── tianyan.bat             # Windows 一键启动脚本
├── tianyan.sh              # Linux/macOS 一键启动脚本
├── run.py                  # 本地启动入口
└── README.md               # 本文件
```

---

## 🛠 技术栈

- **后端**:Python 3.8+ / FastAPI / litellm(多模型统一调用)
- **前端**:原生 HTML + CSS + JavaScript(无构建步骤)
- **数据库**:PostgreSQL(项目/角色/章节) + Redis(短期记忆) + ChromaDB(向量检索) + SQLite(降级)
- **沙箱**:firejail(系统级隔离) + pypy3(独立解释器) + RestrictedPython(代码预检)
- **容器**:Docker + docker-compose(多容器编排)
- **LLM 接入**:litellm(支持 100+ 模型,OpenAI 协议兼容)

---

## 📝 使用建议

- **推荐模型**:DeepSeek(国内直连、便宜、中文好);预算充足可用 GPT 或 Gemini
- **密钥配置**:直接在前端界面右上角 设置 → 添加模型 里填,不用碰 .env 文件
- **字数控制**:续写时可在指令里指定字数,如「续写 3000 字,重点写主角心理」
- **多项目管理**:为不同类型的项目创建独立项目,Agent 会自动切换风格
- **本地模型**:装 Ollama 后 `ollama pull qwen3:14b`,无需 API Key
- **代理配置**:国内使用国外模型时,在 `.env` 中配置 `PROXY=http://host.docker.internal:7890`

---

## 📜 License

### 许可证与用户协议

本项目采用 **三开源协议** 结构：

- **[自定义协议（《用户服务协议》）](USER_AGREEMENT.md)**：规定用户在使用本项目时的权利与义务。**使用本项目即表示您已阅读并同意本协议**。
- **[AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html)**：适用于核心代码,要求修改后的版本在分发时公开源代码。
- **[GPL-3.0](https://www.gnu.org/licenses/gpl-3.0.html)**：同样适用于核心代码,要求衍生作品在分发时以相同许可证公开源代码。

您可以选择 AGPL-3.0 或 GPL-3.0 中的任一许可证,**但无论选择哪一个,都必须同时遵守《用户服务协议》**。
