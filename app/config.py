"""novel-agent 配置:多模型 provider 自动选择与统一管理。

配置来源优先级:
1. config.yaml (可选, 用户可放项目根目录)
2. 环境变量 (NOVEL_AGENT_ 前缀, 或各 provider 的标准 key 如 OPENAI_API_KEY)
3. 默认值
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import yaml

try:
    from pydantic_settings import BaseSettings  # type: ignore
except Exception:  # pragma: no cover - pydantic v1 fallback
    BaseSettings = object  # type: ignore


@dataclass
class ModelConfig:
    """单个模型配置。model 字段使用 litellm 的 provider 前缀格式,
    例如:
      - openai/gpt-4o
      - anthropic/claude-3-5-sonnet
      - gemini/gemini-2.0-flash
      - ollama/qwen2.5:14b
      - deepseek/deepseek-v4-flash
      - openai/qwen-max   (兼容 OpenAI 协议的第三方,配合 api_base)
    """
    model: str = "deepseek/deepseek-v4-flash"
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    temperature: float = 0.8
    max_tokens: int = 4096


@dataclass
class Settings:
    data_dir: str = os.path.expanduser("~/.novel-agent")
    db_path: str = ""
    upload_dir: str = ""
    default_model: ModelConfig = field(default_factory=ModelConfig)
    # 预设的多 provider 模型列表,前端可切换
    models: list[ModelConfig] = field(default_factory=list)
    # agent loop 最大迭代次数
    max_steps: int = 8
    # 风险防护: 单次 run 累计 token 上限 (防 LLM 失控烧钱,默认 200k)
    run_max_tokens: int = 200_000
    # 风险防护: 单次 run 累计成本上限 USD (防意外飙升,默认 $1)
    run_max_cost: float = 1.0
    # 风险防护: 同一工具+同一参数连续调用次数上限 (防循环)
    loop_detect_count: int = 5
    # 风险防护: 单次 run 总时长上限 (秒). 超过则强制停止并告知用户.
    # 默认 600 秒 = 10 分钟 (用户要求: 10 分钟没解决就不行)
    run_max_duration: int = 600
    # SSE 心跳间隔 (秒). 主 agent 在 LLM/工具调用阻塞期间定期 yield 心跳,
    # 防止前端长时间无数据误判为断连. 0 = 关闭.
    # ===== 关键修复: 从 15s 降到 5s =====
    # 原因: Nginx 默认 proxy_read_timeout=60s, Cloudflare 100s, uvicorn timeout_keep_alive=300s
    # 心跳必须远低于所有中间层超时, 5s 确保任何代理都不会因 idle 掐断
    sse_heartbeat_interval: float = 5.0
    # 上传小说分块大小 (字符数)
    chunk_size: int = 2000
    chunk_overlap: int = 200
    # 续写时检索相关上下文块数
    retrieve_k: int = 6
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    # ===== 新增: HTTP 代理配置 (解决国内访问 OpenAI/Gemini 连接不上) =====
    # 留空 = 不用代理 (直连)
    # 填 "http://127.0.0.1:7890" = 走本地代理 (Clash/V2Ray 常用端口)
    # 也可通过环境变量 HTTP_PROXY / HTTPS_PROXY 设置
    proxy: Optional[str] = None


def _load_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _model_from_dict(d: dict) -> ModelConfig:
    return ModelConfig(
        model=d.get("model", "deepseek/deepseek-v4-flash"),
        api_key=d.get("api_key"),
        api_base=d.get("api_base"),
        temperature=float(d.get("temperature", 0.8)),
        max_tokens=int(d.get("max_tokens", 4096)),
    )


def load_settings(config_path: Optional[str] = None) -> Settings:
    """加载配置。config_path 缺省依次查找:
    $NOVEL_AGENT_CONFIG, ./config.yaml, ~/.novel-agent/config.yaml
    """
    global _config_path
    path = config_path or os.environ.get(
        "NOVEL_AGENT_CONFIG",
        os.path.join(os.path.expanduser("~/.novel-agent"), "config.yaml"),
    )
    if not os.path.exists(path):
        path = "config.yaml"
    _config_path = path
    raw = _load_yaml(path)

    data_dir = raw.get("data_dir") or os.environ.get(
        "NOVEL_AGENT_DATA_DIR", os.path.expanduser("~/.novel-agent")
    )
    os.makedirs(data_dir, exist_ok=True)
    upload_dir = os.path.join(data_dir, "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    default_model = _model_from_dict(raw.get("default_model", {}))
    models = [_model_from_dict(m) for m in raw.get("models", [])]
    if not models:
        models = [default_model]

    # proxy: 优先 yaml 配置, 其次环境变量
    proxy = raw.get("proxy") or os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or None

    s = Settings(
        data_dir=data_dir,
        db_path=os.path.join(data_dir, "novel.db"),
        upload_dir=upload_dir,
        default_model=default_model,
        models=models,
        max_steps=int(raw.get("max_steps", 8)),
        run_max_duration=int(raw.get("run_max_duration", 600)),
        chunk_size=int(raw.get("chunk_size", 2000)),
        chunk_overlap=int(raw.get("chunk_overlap", 200)),
        retrieve_k=int(raw.get("retrieve_k", 6)),
        server_host=raw.get("server_host", "0.0.0.0"),
        server_port=int(raw.get("server_port", 8000)),
        proxy=proxy,
    )
    return s

# 全局单例 (首次 import 时加载;运行期可用 reload_settings 刷新)
_settings: Optional[Settings] = None
# 记录当前加载的 config.yaml 路径,save_settings 落盘时用
_config_path: str = ""


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def reload_settings(config_path: Optional[str] = None) -> Settings:
    global _settings
    _settings = load_settings(config_path)
    return _settings


def _model_to_dict(m: ModelConfig) -> dict:
    """把 ModelConfig 序列化为可写入 yaml 的 dict。"""
    d: dict = {"model": m.model}
    if m.api_key:
        d["api_key"] = m.api_key
    if m.api_base:
        d["api_base"] = m.api_base
    d["temperature"] = m.temperature
    d["max_tokens"] = m.max_tokens
    return d


def save_settings() -> None:
    """把当前 settings 落盘到 config.yaml,持久化自定义模型/密钥/参数。

    注意:api_key 会明文写入 yaml 文件,文件位于 data_dir (默认 ~/.novel-agent/)。
    由用户自行管控文件权限。这是用户明确要求的自定义模型持久化功能。
    """
    global _config_path
    s = get_settings()
    # 加载时若 config.yaml 不存在,fallback 到相对路径 "config.yaml"。
    # 落盘时统一规范到 data_dir/config.yaml,确保首次创建也写到固定位置。
    if not _config_path or _config_path == "config.yaml":
        _config_path = os.path.join(s.data_dir, "config.yaml")
    parent = os.path.dirname(_config_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    data = {
        "data_dir": s.data_dir,
        "default_model": _model_to_dict(s.default_model),
        "models": [_model_to_dict(m) for m in s.models],
        "max_steps": s.max_steps,
        "run_max_duration": s.run_max_duration,
        "chunk_size": s.chunk_size,
        "chunk_overlap": s.chunk_overlap,
        "retrieve_k": s.retrieve_k,
        "server_host": s.server_host,
        "server_port": s.server_port,
    }
    with open(_config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def set_default_model(model: str) -> None:
    """切换默认模型并落盘到 config.yaml。"""
    s = get_settings()
    s.default_model.model = model
    # 若切换的模型在预设列表中,沿用其 key/base
    for m in s.models:
        if m.model == model:
            s.default_model.api_key = m.api_key
            s.default_model.api_base = m.api_base
            break
    # 否则保留现有 key/base
    save_settings()


# 厂商预设(前端"添加模型"快捷选项)
# 数据核对日期: 2026-08-02(各家官方文档/价格页/release notes)
PROVIDER_PRESETS = [
    # ---------- OpenAI (https://platform.openai.com/docs/models + help.openai.com release notes) ----------
    # litellm 前缀: openai/   env: OPENAI_API_KEY
    # 2026-08-02 核实官方: GPT-5.6 系列 2026-07-09 发布(限量预览), 07-30 降价
    #   - 三档: Sol 旗舰($5/$30) / Terra 均衡($2.50/$15) / Luna 极速($0.20/$1.20, 降80%)
    #   - 官方无 sol-pro 变体, 仅 sol/terra/luna 三档
    #   - GPT-5.5 (旗舰, $5/$30, 1M上下文) / GPT-5.4 / GPT-5.4-mini 仍在售 (models 页面主力)
    {"provider": "openai", "label": "OpenAI", "models": [
        # —— GPT-5.6 当前旗舰(三档: Sol 旗舰 / Terra 均衡 / Luna 极速) ——
        "openai/gpt-5.6-sol",
        "openai/gpt-5.6-terra",
        "openai/gpt-5.6-luna",
        # —— GPT-5.5 系列(仍在售) ——
        "openai/gpt-5.5",
        "openai/gpt-5.5-pro",
        # —— GPT-5.4 系列(仍在售) ——
        "openai/gpt-5.4",
        "openai/gpt-5.4-mini",
        "openai/gpt-5.4-pro",
    ], "env": "OPENAI_API_KEY", "api_base": "https://api.openai.com/v1"},
    # ---------- Google Gemini (https://ai.google.dev/gemini-api/docs/models) ----------
    # litellm 前缀: gemini/   env: GEMINI_API_KEY
    # 2026-08-02 核实官方: Gemini 3.6 Flash (2026-07-21, Stable, 1M输入/64K输出) 为当前最新主力
    #   - 同期发布 3.5 Flash-Lite (高吞吐, 350 token/s, $0.30/$2.50)
    #   - 3.5 Flash / 3.1 系列仍在售; 2.0 系列已于 2026-06-01 下线
    {"provider": "gemini", "label": "Google Gemini", "models": [
        # —— Gemini 3.6 Flash 当前最新主力(2026-07-21, Stable) ——
        "gemini/gemini-3.6-flash",
        # —— Gemini 3.5 系列 ——
        "gemini/gemini-3.5-flash",
        "gemini/gemini-3.5-flash-lite",
        # —— Gemini 3.1 系列(仍在售) ——
        "gemini/gemini-3.1-pro",
        "gemini/gemini-3.1-flash",
        "gemini/gemini-3.1-flash-lite",
    ], "env": "GEMINI_API_KEY", "api_base": "https://generativelanguage.googleapis.com/v1beta"},
    # ---------- xAI Grok (https://docs.x.ai/docs/models + docs.x.ai/developers/release-notes) ----------
    # litellm 前缀: xai/   env: XAI_API_KEY
    # 2026-08-02 核实官方 release notes: Grok 4.5 (2026-07-08) 为当前最新
    #   - 1.5万亿参数 MoE, 50万上下文, $2/$6, 与 Cursor 联合训练(编程/Agent 旗舰)
    #   - Grok 4.3 (2026-05, 1M上下文) 仍在售; grok-4/grok-3 等老模型已于 2026-05-15 下线
    {"provider": "xai", "label": "xAI Grok", "models": [
        # —— Grok 4.5 当前旗舰(2026-07-08, 编程/Agent 专用) ——
        "xai/grok-4.5",
        # —— Grok 4.3 (仍在售, 1M上下文) ——
        "xai/grok-4.3",
        "xai/grok-4.1",
        "xai/grok-4.1-mini",
    ], "env": "XAI_API_KEY", "api_base": "https://api.x.ai/v1"},
    # ---------- Mistral AI (https://docs.mistral.ai/getting-started/models/models_overview/) ----------
    # litellm 前缀: mistral/   env: MISTRAL_API_KEY
    # 2026-04 Medium 3.5 发布;2026-03 Small 4 发布;2025-12 Large 3 / Devstral 2 发布
    {"provider": "mistral", "label": "Mistral AI", "models": [
        "mistral/mistral-large-latest",
        "mistral/mistral-medium-3-5",
        "mistral/mistral-medium-latest",
        "mistral/mistral-small-latest",
        "mistral/magistral-medium-latest",
        "mistral/magistral-small-latest",
        "mistral/codestral-latest",
        "mistral/devstral-latest",
        "mistral/ministral-14b-latest",
        "mistral/ministral-8b-latest",
        "mistral/ministral-3b-latest",
        "mistral/mistral-nemo",
    ], "env": "MISTRAL_API_KEY", "api_base": "https://api.mistral.ai/v1"},
    # ---------- DeepSeek (https://api-docs.deepseek.com/quick_start/pricing) ----------
    # litellm 前缀: deepseek/   env: DEEPSEEK_API_KEY
    # 2026-08-02 核实官方文档: 当前在售仅 V4 系列(Pro/Flash)
    #   - deepseek-v4-flash 已更新至 V4-Flash-0731 (1M上下文/384K输出, 默认思考模式)
    #   - 旧别名 deepseek-chat / deepseek-reasoner 已于 2026-07-24 退役
    #   - 网传 "DeepSeek-V5" 系第三方博彩站洗稿软文, 官方无此版本, 勿采信
    {"provider": "deepseek", "label": "DeepSeek 深度求索", "models": [
        "deepseek/deepseek-v4-pro",
        "deepseek/deepseek-v4-flash",
    ], "env": "DEEPSEEK_API_KEY", "api_base": "https://api.deepseek.com"},
    # ---------- 阿里云通义千问 DashScope (https://help.aliyun.com/zh/model-studio/models) ----------
    # litellm 前缀: dashscope/   env: DASHSCOPE_API_KEY
    # 2026-08-02 核实官方: Qwen3.8-Max-Preview (2026-07-19) 为当前最新旗舰
    #   - 2.4万亿参数 MoE, 100万上下文, 双推理模式(思考/快速), $1.50/$5.00
    #   - Qwen3.7 (2026-05) / Qwen3.6 系列仍在售; qwen3-max 正式版(快照 2026-01-23)在售
    {"provider": "dashscope", "label": "阿里云通义千问 (DashScope)", "models": [
        # —— Qwen3.8-Max-Preview 当前最新旗舰(2026-07-19, 2.4万亿MoE) ——
        "dashscope/qwen3.8-max-preview",
        # —— Qwen3.7 当前主力(2026-05) ——
        "dashscope/qwen3.7-max",
        "dashscope/qwen3.7-plus",
        # —— Qwen3.6 系列(仍在售) ——
        "dashscope/qwen3.6-max-preview",
        "dashscope/qwen3.6-plus",
        "dashscope/qwen3.6-flash",
        # —— QwQ 推理模型 ——
        "dashscope/qwq-32b",
    ], "env": "DASHSCOPE_API_KEY", "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    # ---------- 智谱 GLM (https://docs.bigmodel.cn/api-reference/模型-api/对话补全异步) ----------
    # litellm 前缀: zai/   env: ZAI_API_KEY (或 ZHIPUAI_API_KEY)
    # 2026-06-17 GLM-5.2 发布(当前旗舰);GLM-5.1/5 仍在售
    {"provider": "zai", "label": "智谱 GLM (Z.AI)", "models": [
        # —— GLM-5.x 当前主力 ——
        "zai/glm-5.2",
        "zai/glm-5.1",
        "zai/glm-5-turbo",
        "zai/glm-5",
        # —— GLM-4.x 仍在售 ——
        "zai/glm-4.7",
        "zai/glm-4.6",
        "zai/glm-4.5-air",
        "zai/glm-4.5-airx",
        "zai/glm-4.5-flash",
    ], "env": "ZAI_API_KEY", "api_base": "https://open.bigmodel.cn/api/paas/v4"},
    # ---------- 月之暗面 Kimi (https://platform.kimi.com/docs/models) ----------
    # litellm 前缀: moonshot/   env: MOONSHOT_API_KEY
    # 2026-07-16 K3 发布 (2.8万亿参数开源旗舰,100万上下文,KDA架构+MoE 896专家激活16)
    #         K3 默认开启 Max 极致思考模式,后续将新增 Low/High 模式
    #         K3 在 Frontend Code Arena 1679 分超越 Claude Fable 5,Agentic 任务 91.2 分
    # 2026-06-16 K2.7-Code 发布;K2.6 为多模态旗舰;K2.5 仍在售
    # 注: K2/K2-0711/K2-thinking 等旧版已于 2026-05-25 下线
    {"provider": "moonshot", "label": "月之暗面 Kimi", "models": [
        # —— K3 当前旗舰(2026-07-16, 2.8万亿参数开源模型,100万上下文,原生视觉+Agentic) ——
        "moonshot/kimi-k3",
        # —— K2.7 当前编程专用旗舰 ——
        "moonshot/kimi-k2.7-code",
        # —— K2.6 多模态旗舰(文本+图片+视频,256k) ——
        "moonshot/kimi-k2.6",
        # —— K2.5 上一代旗舰(仍在售,性价比高) ——
        "moonshot/kimi-k2.5",
    ], "env": "MOONSHOT_API_KEY", "api_base": "https://api.moonshot.cn/v1"},
    # ---------- 火山引擎 豆包 (https://www.volcengine.com/docs/82379/1263482) ----------
    # litellm 前缀: volcengine/   env: VOLCENGINE_API_KEY / ARK_API_KEY
    # 2026-06-28 Doubao Seed 2.1 发布;2.0 系列仍在售;doubao-seed-evolving 周级迭代
    {"provider": "volcengine", "label": "火山引擎 豆包 Doubao", "models": [
        # —— Seed 2.1 当前旗舰(2026-06-28) ——
        "volcengine/doubao-seed-2-1-pro-260628",
        "volcengine/doubao-seed-2-1-turbo-260628",
        # —— Seed Evolving 周级迭代模型 ——
        "volcengine/doubao-seed-evolving",
        # —— Seed 2.0 系列(仍在售) ——
        "volcengine/doubao-seed-2-0-pro-260215",
        "volcengine/doubao-seed-2-0-lite-260428",
        "volcengine/doubao-seed-2-0-mini-260428",
        "volcengine/doubao-seed-2-0-code-preview-260215",
        # —— 角色扮演模型 ——
        "volcengine/doubao-seed-character-260628",
    ], "env": "VOLCENGINE_API_KEY", "api_base": "https://ark.cn-beijing.volces.com/api/v3"},
    # ---------- 百度 文心 ERNIE (https://cloud.baidu.com/doc/WENXINWORKSHOP/) ----------
    # 通过千帆 v2 OpenAI 兼容接口调用,需用 openai/ 前缀 + 自定义 base
    # 2026 ERNIE 4.5 系列为当前主力
    {"provider": "baidu", "label": "百度 文心 ERNIE (千帆)", "models": [
        "openai/ernie-4.5-turbo-128k",
        "openai/ernie-4.5-turbo-8k",
        "openai/ernie-4.5-21b-a3b",
        "openai/ernie-4.0-turbo-128k",
        "openai/ernie-4.0-8k",
        "openai/ernie-speed-pro-128k",
        "openai/ernie-x1-turbo-128k",
    ], "env": "ERNIE_API_KEY", "api_base": "https://qianfan.baidubce.com/v2"},
    # ---------- Ollama 本地 (https://ollama.com/library) ----------
    # litellm 前缀: ollama/   无需 env
    {"provider": "ollama", "label": "Ollama 本地", "models": [
        "ollama/qwen3-coder:30b",
        "ollama/qwen3:32b",
        "ollama/qwen3:14b",
        "ollama/qwen3:8b",
        "ollama/qwen3:4b",
        "ollama/qwen2.5:72b",
        "ollama/qwen2.5:32b",
        "ollama/qwen2.5:14b",
        "ollama/qwen2.5:7b",
        "ollama/qwen2.5:3b",
        "ollama/qwen2.5-coder:32b",
        "ollama/qwen2.5-coder:14b",
        "ollama/qwen2.5-coder:7b",
        "ollama/deepseek-r1:70b",
        "ollama/deepseek-r1:32b",
        "ollama/deepseek-r1:14b",
        "ollama/deepseek-r1:8b",
        "ollama/deepseek-r1:7b",
        "ollama/deepseek-r1:1.5b",
        "ollama/deepseek-v3:671b",
        "ollama/llama3.3:70b",
        "ollama/llama3.3:8b",
        "ollama/llama3.2:8b",
        "ollama/llama3.2:3b",
        "ollama/llama3.2:1b",
        "ollama/mistral:7b",
        "ollama/mistral-nemo",
        "ollama/gemma3:27b",
        "ollama/gemma3:12b",
        "ollama/gemma3:4b",
        "ollama/phi4:14b",
        "ollama/codestral:22b",
    ], "env": "", "api_base": "http://localhost:11434"},
    # ---------- 硅基流动 SiliconFlow (https://siliconflow.cn/models) ----------
    # litellm 前缀: siliconflow/   env: SILICONFLOW_API_KEY
    # 国内聚合开源模型:DeepSeek / Qwen / GLM / Kimi / LongCat 等
    # 数据核对: siliconflow.cn/pricing 2026-07
    {"provider": "siliconflow", "label": "硅基流动 SiliconFlow (聚合)", "models": [
        # —— DeepSeek V4 系列 ——
        "siliconflow/deepseek-ai/DeepSeek-V4-Pro",
        "siliconflow/deepseek-ai/DeepSeek-V4-Flash",
        "siliconflow/deepseek-ai/DeepSeek-V3.2",
        # —— GLM (zai-org) ——
        "siliconflow/zai-org/GLM-5.2",
        "siliconflow/zai-org/GLM-5.1",
        # —— Kimi (moonshotai) ——
        "siliconflow/moonshotai/Kimi-K3",
        "siliconflow/moonshotai/Kimi-K2.7-Code",
        "siliconflow/moonshotai/Kimi-K2.6",
        # —— Qwen ——
        "siliconflow/Qwen/Qwen3.6-35B-A3B",
        "siliconflow/Qwen/Qwen3.6-27B",
        "siliconflow/Qwen/Qwen3.5-397B-A17B",
        # —— LongCat (美团) ——
        "siliconflow/meituan-longcat/LongCat-2.0",
        # —— MiniMax ——
        "siliconflow/MiniMaxAI/MiniMax-M3",
    ], "env": "SILICONFLOW_API_KEY", "api_base": "https://api.siliconflow.cn/v1"},
    # ---------- OpenRouter (https://openrouter.ai/models) ----------
    # litellm 前缀: openrouter/   env: OPENROUTER_API_KEY
    # 全球聚合:OpenAI / Google / Meta / Mistral 等几乎全部模型(已剔除 Anthropic)
    {"provider": "openrouter", "label": "OpenRouter (全球聚合)", "models": [
        "openrouter/openai/gpt-5.6-sol",
        "openrouter/openai/gpt-5.6-terra",
        "openrouter/openai/gpt-5.6-luna",
        "openrouter/openai/gpt-5.5",
        "openrouter/google/gemini-3.6-flash",
        "openrouter/google/gemini-3.1-pro",
        "openrouter/google/gemini-3.1-flash",
        "openrouter/xai/grok-4.5",
        "openrouter/meta-llama/llama-3.3-70b-instruct",
        "openrouter/deepseek/deepseek-v4-flash",
        "openrouter/qwen/qwen-3.6",
        "openrouter/mistralai/mistral-large",
    ], "env": "OPENROUTER_API_KEY", "api_base": "https://openrouter.ai/api/v1"},
    # ---------- Together AI (https://docs.together.ai/docs/inference-models) ----------
    # litellm 前缀: together_ai/   env: TOGETHERAI_API_KEY
    {"provider": "together_ai", "label": "Together AI (聚合)", "models": [
        "together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "together_ai/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
        "together_ai/Qwen/Qwen3-235B-A22B-Instruct-Turbo",
        "together_ai/deepseek-ai/DeepSeek-V4-Flash",
        "together_ai/mistralai/Mistral-7B-Instruct-v0.3",
    ], "env": "TOGETHERAI_API_KEY", "api_base": "https://api.together.xyz/v1"},
    # ---------- Fireworks AI (https://fireworks.ai/models) ----------
    # litellm 前缀: fireworks_ai/   env: FIREWORKS_API_KEY
    {"provider": "fireworks_ai", "label": "Fireworks AI (聚合)", "models": [
        "fireworks_ai/accounts/fireworks/models/llama-v3p3-70b-instruct",
        "fireworks_ai/accounts/fireworks/models/qwen3-235b-a22b-instruct",
        "fireworks_ai/accounts/fireworks/models/deepseek-v3",
    ], "env": "FIREWORKS_API_KEY", "api_base": "https://api.fireworks.ai/inference/v1"},
    # ---------- 小米 MiMo (https://mimo.mi.com) ----------
    # OpenAI 兼容接口,需用 openai/ 前缀 + 自定义 base
    # env: XIAOMI_API_KEY (或 MIMO_API_KEY)
    # 2026-04-23 MiMo-V2.5 系列公测;V2 系列已于 2026-06-30 下线
    # V2.5-Pro: 1M 上下文/128K 输出,Agent 优化旗舰,默认开启深度思考
    # V2.5: 全模态(文本/图像/视频/音频),1M 上下文/128K 输出
    # Token Plan 专用 base: https://token-plan-cn.xiaomimimo.com/v1
    {"provider": "xiaomi", "label": "小米 MiMo", "models": [
        # —— V2.5 当前主力(2026-04) ——
        "openai/mimo-v2.5-pro",
        "openai/mimo-v2.5",
    ], "env": "XIAOMI_API_KEY", "api_base": "https://api.xiaomimimo.com/v1"},
    # ---------- 华为盘古 MaaS (https://www.huaweicloud.com/product/modelarts-maas.html) ----------
    # OpenAI 兼容 V2 接口,需用 openai/ 前缀 + 自定义 base
    # env: MAAS_API_KEY
    # 2026-06-30 openPangu-2.0-Flash 发布;512K 上下文/128K 输出,支持深度思考(可开关)
    # MaaS 还聚合了 GLM / Kimi / DeepSeek / Qwen3 等三方模型
    # Token Plan 专用 base: https://api.modelarts-maas.com/plan/v2
    {"provider": "huawei", "label": "华为盘古 MaaS (ModelArts)", "models": [
        # —— 盘古原生 ——
        "openai/openpangu-2.0-flash",
        # —— MaaS 聚合的三方模型(西南-贵阳一) ——
        "openai/glm-5.2",
        "openai/kimi-k2.6",
        "openai/deepseek-v4-pro",
        "openai/deepseek-v4-flash",
        "openai/deepseek-v3.2",
    ], "env": "MAAS_API_KEY", "api_base": "https://api.modelarts-maas.com/v2"},
]


def update_model_config(model: str, *, api_key: str | None = None, api_base: str | None = None,
                        temperature: float | None = None, max_tokens: int | None = None) -> None:
    """更新某个模型的配置(若不存在则新增到 models 列表)。同时同步到 default_model 并落盘。"""
    s = get_settings()
    target = None
    for m in s.models:
        if m.model == model:
            target = m
            break
    is_new = target is None
    if is_new:
        target = ModelConfig(model=model)
        s.models.append(target)
    if api_key is not None:
        target.api_key = api_key or None
    if api_base is not None:
        target.api_base = api_base or None
    if temperature is not None:
        target.temperature = temperature
    if max_tokens is not None:
        target.max_tokens = max_tokens
    # 添加新模型时,若 default 还指向占位/已删模型,自动把新模型设为 default
    # (场景: 用户删空后加新模型, default 应该自动跟过来)
    if is_new and (not s.models or s.default_model.model not in [m.model for m in s.models]):
        s.default_model = target
    # 若是当前默认模型,同步
    if s.default_model.model == model:
        s.default_model = target
    save_settings()


def remove_model_config(model: str) -> None:
    s = get_settings()
    s.models = [m for m in s.models if m.model != model]
    # 删的是 default 时:
    #   - 还有其他模型,自动切到列表第一个
    #   - 列表空了,重置 default 为空白占位 (前端显示"未配置模型",可继续添加新模型)
    if s.default_model.model == model:
        s.default_model = s.models[0] if s.models else ModelConfig()
    save_settings()


def update_agent_params(*, max_steps: int | None = None, chunk_size: int | None = None,
                        retrieve_k: int | None = None) -> None:
    s = get_settings()
    if max_steps is not None:
        s.max_steps = max_steps
    if chunk_size is not None:
        s.chunk_size = chunk_size
    if retrieve_k is not None:
        s.retrieve_k = retrieve_k
    save_settings()

