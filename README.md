# 天衍

一个多Agent协同的**小说创作 Agent**(Web 界面)。7 个 agent 协同,按 8 阶段工作流完成从扫榜调研到定稿入库的完整长篇创作闭环,内置「毒舌总编」审稿机制与质检打回循环。

![Python](https://img.shields.io/badge/Python-3.10+-blue) 
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green) 
![Docker](https://img.shields.io/badge/Docker-支持-2496ED)
[![自定义协议](https://img.shields.io/badge/📄-自定义协议-0052d9)](USER_AGREEMENT.md)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL--3.0-blueviolet)](https://www.gnu.org/licenses/agpl-3.0.html)
[![License: GPL v3](https://img.shields.io/badge/License-GPL--3.0-red)](https://www.gnu.org/licenses/gpl-3.0.html)
---

## ✨ 核心特性

### 8 阶段长篇创作闭环(含审稿循环)

| 阶段 | 名称 | 负责 agent | 说明 |
|------|------|-----------|------|
| 1 | 扫榜调研 | story-architect | 扫描 2026 网文市场热门榜单,分析题材趋势/流量赛道/读者画像 |reload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alert
| 2 | 拆书解构 | story-architect | 拆解对标畅销书,提取钩子/节奏/人设/文风/核心梗等可复用模块 |
| 3 | 定文风定位 | story-architect + character-designer | 基于扫榜+拆书结论,确定文风/题材/核心梗/情绪曲线 |reload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alert
| 4 | 大纲搭建 | story-architect | 全书体量→卷纲→细纲→伏笔/时间线/角色状态追踪初始化 |
| 5 | 正文写作 | story-explorer + narrative-writer + character-designer | 细纲优先→加载上下文→三维度揉进→字数验证→更新追踪 |reload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alert
| 6 | 毒舌编辑 | orchestrator | 总编逐章审稿,输出【毒舌评分 1-10】+【致命问题】+【裁决】 |
| 7 | 审核质检 | consistency-checker + narrative-writer | 一致性+伏笔+去 AI 味+格式合规,**不通过打回阶段 5 重写** |reload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alert
| 8 | 定稿入库 | orchestrator | 审核通过→标记定稿→更新追踪→**循环回阶段 5 写下一章** |reload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alert

### 7-agent 架构

| Agent | 角色 | 沙盒权限 |
|-------|------|---------|
| orchestrator | 总编(全局调度 + 毒舌审稿) | read-write |
| story-architect | 架构师(扫榜/拆书/大纲) | read-write |
| narrative-writer | 主笔(正文 + 去 AI 味) | read-write |reload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alertreload-alert
| character-designer | 角色师(人设 + 对话) | read-write |
| consistency-checker | 质检员(一致性审查) | **read-only** |
| story-explorer | 资料员(上下文加载) | **read-only** |
| worldbuilder | 设定管理员(世界观/地点/时间线) | read-write |

- **OpenClaw 模式**:agent 由后端 orchestrator 自动委派调度,前端不暴露选择器;composer 旁有 agent 芯片自动跟踪当前活跃 agent
- **只读沙盒**:consistency-checker / story-explorer 不能调用写入工具,保证审查中立

### UI 特性

- **⌘K / Ctrl+K 命令面板**:12 个快捷命令,模糊搜索 + 方向键导航
- **斜杠命令**:9 个(`/续写 /状态 /质检 /大纲 /设定 /润色 /清空 /设置 /导出`)reload-alert
- **Agent Panel**:右侧滑出,展示当前活跃 agent 的角色/阶段/沙盒/工具列表
- **IDE 式文件树**:左侧章节/设定/素材库树形展示,支持展开收起
- **多格式导出**:TXT / Markdown / Word(.docx) / HTML(可打印 PDF)
- **多格式上传**:txt / md / docx / pdf / epub / csv,自动分块入库供检索

### 模型支持

内置 15 家厂商预设,前端「添加模型」一键添加:

| 分类 | 平台 |
|------|------|
| 国际 | OpenAI(GPT-5.6)、Google Gemini、xAI Grok、Mistral |
| 国内 | DeepSeek、通义千问、智谱 GLM、Kimi、豆包、文心 ERNIE |
| 聚合 | 硅基流动 SiliconFlow、OpenRouter、Together AI、Fireworks AI |
| 本地 | Ollama |

- **自定义模型**:前端可填任意 `provider/model` + api_key + api_base,持久化到 `~/.novel-agent/config.yaml`
- **模型配置持久化**:增删改模型、切换默认模型均自动落盘

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- 任一 LLM API Key(推荐 DeepSeek,国内直连且便宜)

### 安装与启动

#### Windows

```powershell
# 1. 克隆
git clone https://github.com/l1064709321/tianyan.git
cd saga

# 2. 装依赖 (uvloop/httptools 会自动跳过)
pip install -r requirements.txt

# 3. 启动 (任选其一)
python run.py                      # 直接启动
start.bat                          # 双击或命令行运行 (自动检查依赖)
na.bat start                       # 后台运行
```

#### macOS / Linux

```bash
# 1. 克隆
git clone https://github.com/l1064709321/tianyan.git
cd saga

# 2. 装依赖 (自动安装 uvloop/httptools 高性能组件)
pip install -r requirements.txt

# 3. 启动 (任选其一)
python run.py                      # 直接启动
bash start.sh                      # 一键启动 (tkinter 桌面对话框)
./na start                         # 后台运行
```

打开浏览器访问 **http://localhost:8000/**

### 配置模型

启动后在前端「⚙ 设置」面板添加模型并填 API Key 即可,配置会自动保存到 `~/.novel-agent/config.yaml`。

也可用环境变量(任选):

```bash
export DEEPSEEK_API_KEY="sk-xxx"     # DeepSeek
export OPENAI_API_KEY="sk-xxx"        # OpenAI
export GEMINI_API_KEY="xxx"          # Google Gemini
export DASHSCOPE_API_KEY="sk-xxx"    # 通义千问
export ZAI_API_KEY="xxx"             # 智谱 GLM
export MOONSHOT_API_KEY="sk-xxx"     # Kimi
export SILICONFLOW_API_KEY="sk-xxx"   # 硅基流动
```

或编辑配置文件:

```bash
cp config.example.yaml ~/.xiaoshuo-agent/config.yaml
vim ~/.xiaosuo-agent/config.yaml
```

### 使用流程

1. **新建项目**:填名称/类型/文风/核心设定
2. **(可选)上传对标书**:点顶栏「上传」,导入 txt/md/docx/pdf/epub 供拆书解构
3. **开始创作**:在对话框输入指令,或点空状态页的快捷按钮(从扫榜开始 / 拆书解构 / 民俗悬疑大纲 / 规则怪谈大纲)
4. **审稿循环**:阶段 6 总编毒舌审稿 → 阶段 7 质检,不通过自动打回阶段 5 重写,通过则推进下一章
5. **导出**:章节写完后点「导出」选格式

---

## 📁 项目结构

```
saga/
├── app/
│   ├── agents.py       # 7-agent 定义 + 8 阶段工作流 + 毒舌审稿人设
│   ├── tools.py        # 11 个工具(扫榜/拆书/大纲/续写/润色/...)
│   ├── agent.py        # agentic loop + delegate 委派机制
│   ├── llm.py          # litellm 封装(stream/chat)
│   ├── config.py       # 配置 + 15 家厂商预设 + 持久化
│   ├── store.py        # 项目/章节/设定/分块存储
│   ├── exporter.py     # 多格式导出
│   └── server.py       # FastAPI 路由(REST + SSE)
├── web/
│   ├── index.html      # OpenClaw 式布局
│   ├── app.js          # 前端逻辑 + ⌘K + 斜杠命令
│   └── style.css       # 书房羊皮纸风 UI
├── config.example.yaml # 配置示例
├── requirements.txt
├── run.py              # 入口
├── start.sh            # 一键启动 (macOS/Linux)
├── start.bat           # 一键启动 (Windows)
├── na                  # 管理命令 (macOS/Linux)
├── na.bat              # 管理命令 (Windows)
├── launcher.py         # Web 卡片启动器
├── launcher_tk.py      # tkinter 桌面启动器
├── pre-upload.sh       # 上传前安全扫描脚本
└── .gitignore
```

---

## 🛠 技术栈

- **后端**:Python 3.10+ / FastAPI / litellm(多模型统一调用)
- **前端**:原生 HTML + CSS + JavaScript(无构建步骤,无外部依赖)
- **存储**:SQLite(轻量,零配置)
- **LLM 接入**:litellm(支持 100+ 模型,OpenAI 协议兼容)

---

## 📝 使用建议

- **推荐模型**:DeepSeek V4-Flash(国内直连、便宜、中文好);预算充足可用 GPT-5.6-terra 或 Gemini-3.1-pro
- **字数控制**:续写时可在指令里指定字数,如「续写 3000 字,重点写主角心理」
- **审稿严格度**:总编评分 < 7 分会自动打回重写,可在 [agents.py](app/agents.py) 中调整阈值
- **本地模型**:装 Ollama 后 `ollama pull qwen3:14b`,无需 API Key 即可使用

---

# 📜 License

###  许可证与用户协议

本项目采用 **三开源协议** 结构：

- **[自定义协议（《用户服务协议》）](USER_AGREEMENT.md)**：规定用户在使用本项目时的权利与义务（输入/输出归用户所有、禁止抄袭洗稿、AI 仿写语料库等）。**使用本项目即表示您已阅读并同意本协议**。
- **[AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html)**：适用于核心代码，要求修改后的版本在分发时公开源代码，且对网络服务有更强的 Copyleft 约束。
- **[GPL-3.0](https://www.gnu.org/licenses/gpl-3.0.html)**：同样适用于核心代码，要求衍生作品在分发时以相同许可证公开源代码。

您可以选择 AGPL-3.0 或 GPL-3.0 中的任一许可证，**但无论选择哪一个，都必须同时遵守《用户服务协议》**。

---

[![自定义协议](https://img.shields.io/badge/📄-自定义协议-0052d9)](USER_AGREEMENT.md)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL--3.0-blueviolet)](https://www.gnu.org/licenses/agpl-3.0.html)
[![License: GPL v3](https://img.shields.io/badge/License-GPL--3.0-red)](https://www.gnu.org/licenses/gpl-3.0.html)
---
