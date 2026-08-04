"""LLM 统一接入层。基于 litellm, 自动兼容 OpenAI / Anthropic / Gemini /
DeepSeek / 通义 / 智谱 / 月之暗面 / Ollama 等几乎所有在线与本地模型。

只要在模型配置里给出 model 前缀与对应 api_key / api_base,即可自动路由。
"""
from __future__ import annotations

import logging
import os
from typing import Any, AsyncIterator, Optional

logger = logging.getLogger("novel_agent.llm")

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

try:
    import litellm  # type: ignore
    # 避免部分 provider 误判抛错
    litellm.set_verbose = False
    litellm.drop_params = True
    litellm.modify_params = True
    # ===== 关键修复: litellm 全局超时设为 None (永不超时) =====
    # 原版 _HTTP_TIMEOUT=60 导致 LLM 思考期间(>60s)请求被掐断 → ERR_STREAM_PREMATURE_CLOSE
    litellm.request_timeout = None
    # litellm 内部重试 (指数退避), 覆盖 429/5xx/连接错误
    # 设为 2: 快速恢复 429 限流 (尊重 Retry-After 头), 不与应用层 5 次重试叠加过多
    litellm.num_retries = 2
    litellm.retry_after = True  # 尊重 Retry-After 头
except Exception:  # pragma: no cover
    litellm = None  # type: ignore

# ===== 关键修复: 全局共享 httpx 连接池 =====
# 原版: litellm 每次调用内部新建 AsyncHTTPHandler → 连接数不可控 + keep-alive 失效
# 修复: 全局单例 httpx.AsyncClient, timeout=None(永不超时), 连接池 20/50
_global_http_client: Optional[Any] = None

def _get_http_client():
    """获取全局共享的 httpx.AsyncClient 单例。
    
    timeout=None: 彻底关闭 HTTP 超时, 让 LLM 思考多久都不被掐断
    max_keepalive_connections=20: 复用连接, 避免每次 new
    max_connections=50: 并行工具调用不抢占主连接池
    proxy: 从 config 读取, 解决国内访问 OpenAI/Gemini 连接不上
    """
    global _global_http_client
    if _global_http_client is not None:
        return _global_http_client
    if httpx is None:
        return None
    # 读取代理配置 (config.yaml 或环境变量)
    proxy_url = None
    try:
        from .config import get_settings
        s = get_settings()
        proxy_url = s.proxy
    except Exception:
        pass
    # 环境变量兜底
    if not proxy_url:
        proxy_url = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or None
    
    client_kwargs = {
        "timeout": httpx.Timeout(None),  # 彻底关闭超时
        "limits": httpx.Limits(
            max_keepalive_connections=20,
            max_connections=50,
            keepalive_expiry=120,  # 连接保活 2 分钟
        ),
    }
    if proxy_url:
        client_kwargs["proxy"] = proxy_url
        logger.info(f"[LLM] 全局 httpx 连接池已初始化 (timeout=None, keepalive=20, max=50, proxy={proxy_url})")
    else:
        logger.info("[LLM] 全局 httpx 连接池已初始化 (timeout=None, keepalive=20, max=50, 无代理)")
    _global_http_client = httpx.AsyncClient(**client_kwargs)
    return _global_http_client

from .config import ModelConfig


class LLMError(Exception):
    pass


# litellm 已知支持的 provider 前缀 (大小写不敏感)
# 用户在配置里写的厂商前缀若不在此集合内, 视为业务名, 会被去掉后重新识别
_KNOWN_PROVIDERS = {
    "openai", "azure", "anthropic", "gemini", "vertex_ai", "mistral",
    "deepseek", "dashscope", "dashpipe", "zai", "zhipu", "glm",
    "moonshot", "ollama", "siliconflow", "openrouter",
    "together_ai", "together", "fireworks_ai", "fireworks",
    "xai", "volcengine", "ark", "baidu", "ernie", "qianfan",
    "huawei", "xiaomi", "huggingface", "bedrock", "ai21", "cohere",
    "perplexity", "anyscale", "predibase", "vllm", "custom",
}

# 模型名 → provider 启发式匹配 (用户只填业务名且无 api_base 时用)
_MODEL_HEURISTICS = [
    ("gpt", "openai"),
    ("o1-", "openai"), ("o3-", "openai"), ("o4-", "openai"),
    ("claude", "anthropic"),
    ("gemini", "gemini"),
    ("deepseek", "deepseek"),
    ("glm-", "zai"),
    ("qwen", "dashscope"),
    ("qwq", "dashscope"),
    ("kimi", "moonshot"),
    ("doubao", "volcengine"),
    ("ernie", "baidu"),
    ("mimo", "xiaomi"),
    ("pangu", "huawei"), ("openpangu", "huawei"),
    ("grok", "xai"),
    ("mistral", "mistral"), ("codestral", "mistral"),
    ("magistral", "mistral"), ("ministral", "mistral"),
]


def _normalize_model(model: str, api_base: Optional[str] = None) -> str:
    """把任意模型名规范成 litellm 能识别的 'provider/model' 格式。

    前端用户可只填业务名 (如 'agnes-2.5-flash'),
    本函数在调用 litellm 之前自动补上正确的 provider 前缀。

    规则:
    1. 已带已知 provider 前缀 (openai/anthropic/gemini...) → 原样返回
    2. 带未知前缀 (如 'agnes/xxx', 'my-deploy/xxx') → 视为业务名,
       去掉前缀后按规则 3/4 处理
    3. 不带前缀且有 api_base → 默认 OpenAI 兼容接口, 返回 'openai/<model>'
    4. 不带前缀且无 api_base → 按模型名启发式匹配 (gpt→openai, claude→anthropic...),
       匹配不到默认 'openai/<model>'
    """
    if not model:
        return model
    # 规则 1 & 2: 已带前缀
    if "/" in model:
        provider, rest = model.split("/", 1)
        if provider.lower() in _KNOWN_PROVIDERS:
            return model  # 已知 provider, 原样
        # 未知 provider (用户误填的厂商名, 如 agnes/my-deploy): 去掉前缀当业务名
        model = rest
    # 走到这里 model 一定不带前缀
    # 规则 3: 有 api_base → 默认 OpenAI 兼容 (绝大多数第三方厂商走这个)
    if api_base:
        return f"openai/{model}"
    # 规则 4: 启发式匹配模型名前缀
    name_lower = model.lower()
    for prefix, provider in _MODEL_HEURISTICS:
        if name_lower.startswith(prefix):
            return f"{provider}/{model}"
    # 兜底: openai
    return f"openai/{model}"


def _prepare_env(cfg: ModelConfig) -> str:
    """根据 provider 前缀把 api_key/base 写入对应环境变量,
    litellm 会自动读取。返回规范后的 model 名 (传给 litellm 用)。"""
    norm_model = _normalize_model(cfg.model, cfg.api_base)
    provider = norm_model.split("/", 1)[0].lower() if "/" in norm_model else "openai"
    if cfg.api_key:
        if provider in ("openai",):
            os.environ["OPENAI_API_KEY"] = cfg.api_key
        elif provider in ("anthropic",):
            os.environ["ANTHROPIC_API_KEY"] = cfg.api_key
        elif provider in ("gemini",):
            os.environ["GEMINI_API_KEY"] = cfg.api_key
        elif provider in ("deepseek",):
            os.environ["DEEPSEEK_API_KEY"] = cfg.api_key
        elif provider in ("dashpipe", "dashscope"):
            os.environ["DASHSCOPE_API_KEY"] = cfg.api_key
        elif provider in ("zhipu", "glm"):
            os.environ["ZHIPUAI_API_KEY"] = cfg.api_key
        elif provider in ("moonshot",):
            os.environ["MOONSHOT_API_KEY"] = cfg.api_key
        elif provider in ("ollama",):
            os.environ["OLLAMA_API_BASE"] = cfg.api_base or "http://localhost:11434"
        elif provider in ("siliconflow",):
            os.environ["SILICONFLOW_API_KEY"] = cfg.api_key
        elif provider in ("openrouter",):
            os.environ["OPENROUTER_API_KEY"] = cfg.api_key
        elif provider in ("together_ai", "together"):
            os.environ["TOGETHERAI_API_KEY"] = cfg.api_key
        elif provider in ("fireworks_ai", "fireworks"):
            os.environ["FIREWORKS_API_KEY"] = cfg.api_key
        # openai 兼容的第三方 (如各厂兼容站) 用 openai/ 前缀 + api_base
    if cfg.api_base and provider in ("openai",):
        os.environ["OPENAI_API_BASE"] = cfg.api_base
    return norm_model


async def chat(
    messages: list[dict],
    cfg: ModelConfig,
    *,
    tools: Optional[list[dict]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    response_format: Optional[dict] = None,
    assistant_prefill: Optional[str] = None,
    stop: Optional[list[str]] = None,
) -> dict:
    """非流式补全。返回 {"content": str, "reasoning": str, "tool_calls": list}。

    assistant_prefill: 预填充 assistant 开头 (原理6 Prefill),强制模型从指定前缀续写,
        常用于强制只输出 JSON (填 "{")。返回的 content 已拼回 prefill。
    stop: 停止序列 (原理7 Stop Sequence),命中即停并交回控制权,常用于 ReAct loop。
    """
    if litellm is None:
        raise LLMError("litellm 未安装,请先 pip install -r requirements.txt")
    norm_model = _prepare_env(cfg)
    msgs = list(messages)
    use_prefill = bool(assistant_prefill) and not response_format
    if use_prefill:
        msgs.append({"role": "assistant", "content": assistant_prefill})
    kwargs: dict[str, Any] = {
        "model": norm_model,
        "messages": msgs,
        "temperature": temperature if temperature is not None else cfg.temperature,
        "max_tokens": max_tokens if max_tokens is not None else cfg.max_tokens,
        # 关键: 不设 timeout, 让 litellm 用全局 None (永不超时)
        "timeout": None,
    }
    # 注入全局共享 httpx 连接池 (避免每次新建连接)
    _client = _get_http_client()
    if _client is not None:
        kwargs["client"] = _client
    if tools:
        kwargs["tools"] = tools
        kwargs["parallel_tool_calls"] = False
    if response_format:
        kwargs["response_format"] = response_format
    if stop:
        kwargs["stop"] = stop
    try:
        resp = await litellm.acompletion(**kwargs)
    except Exception as e:
        # 详细异常日志 (含 traceback), 方便复盘
        logger.error(f"[LLM chat] 调用失败: {e}", exc_info=True)
        raise LLMError(f"LLM 调用失败: {e}") from e
    choice = resp.choices[0].message
    # prefill 模式: 模型续写在 prefill 之后,返回的 content 不含 prefill。
    # 这里不把 prefill 拼回 content —— 调用方若需要拼回(如纯文本续写场景)可自行拼。
    # 工具调用场景需要模型吐完整 JSON,拼回反而会产生 {{ 双括号杂质。
    content = choice.content or ""
    # 提取 reasoning_content (思考过程), 某些模型 (如 deepseek-r1, gpt-4o) 会返回
    reasoning = getattr(choice, "reasoning_content", None) or ""
    return {
        "content": content,
        "reasoning": reasoning,
        "tool_calls": getattr(choice, "tool_calls", None) or [],
        "raw": resp,
    }


async def stream(
    messages: list[dict],
    cfg: ModelConfig,
    *,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stop: Optional[list[str]] = None,
) -> AsyncIterator[str]:
    """流式补全,逐 token 产出文本 (仅文本流;工具调用走非流式)。

    stop: 停止序列 (原理7),命中即停,常用于正文续写到章节结尾标记处。
    """
    if litellm is None:
        raise LLMError("litellm 未安装")
    norm_model = _prepare_env(cfg)
    # reasoning 模型 (如 agnes-2.0-flash) 会先吐 reasoning_content (思考过程, content=None),
    # 再吐 content (正文)。思考也消耗 max_tokens, 故 stream 时放大额度避免正文被截断。
    eff_max = max_tokens
    if max_tokens is not None and max_tokens < 4000:
        eff_max = max_tokens * 4
    kwargs: dict[str, Any] = {
        "model": norm_model,
        "messages": messages,
        "temperature": temperature if temperature is not None else cfg.temperature,
        "max_tokens": eff_max if eff_max is not None else cfg.max_tokens,
        "stream": True,
        "timeout": None,  # 关键: 永不超时
    }
    # 注入全局共享 httpx 连接池
    _client = _get_http_client()
    if _client is not None:
        kwargs["client"] = _client
    if stop:
        kwargs["stop"] = stop
    try:
        stream_obj = await litellm.acompletion(**kwargs)
    except Exception as e:
        logger.error(f"[LLM stream] 流式调用失败: {e}", exc_info=True)
        raise LLMError(f"LLM 流式调用失败: {e}") from e
    # ===== 流式中断保护: 网络抖动/API 断连导致 stream 中途断开时记录日志 =====
    try:
        async for chunk in stream_obj:
            try:
                delta = chunk.choices[0].delta
            except Exception:
                continue
            # 只 yield 正文 content; reasoning_content (思考过程) 不输出给用户
            content = getattr(delta, "content", None)
            if content:
                yield content
    except Exception as e:
        logger.error(f"[LLM stream] 流式中途断开: {e}", exc_info=True)
        raise LLMError(f"LLM 流式中途断开: {e}") from e


def _friendly_error(e: Exception, cfg: ModelConfig) -> str:
    """把底层异常翻译成用户能看懂的中文提示。

    分类:
    - 认证错误 (401/403): API Key 问题
    - 网络错误 (连接超时/拒绝/代理): 网络问题, 提示检查代理/防火墙
    - 限流 (429): 稍后重试
    - 模型不存在 (404): 模型名拼写错误或该 provider 无此模型
    - 其他: 原始错误
    """
    err_str = str(e).lower()
    provider = cfg.model.split("/")[0] if "/" in cfg.model else ""

    if any(kw in err_str for kw in ("401", "403", "invalid api key", "unauthorized", "forbidden")):
        return (
            f"API Key 无效或权限不足。请检查:\n"
            f"  1. {provider} 的 API Key 是否正确\n"
            f"  2. Key 是否过期或被禁用\n"
            f"  3. 账户是否有余额/配额\n"
            f"原始错误: {e}"
        )
    if any(kw in err_str for kw in ("429", "rate limit", "quota", "too many requests")):
        return (
            f"请求频率超限或配额用完。请:\n"
            f"  1. 等几秒后重试\n"
            f"  2. 检查 {provider} 账户余额/配额\n"
            f"原始错误: {e}"
        )
    if any(kw in err_str for kw in ("404", "model_not_found", "not found", "does not exist")):
        return (
            f"模型不存在: {cfg.model}\n"
            f"  1. 检查模型名拼写是否正确\n"
            f"  2. 该 provider 是否有此模型\n"
            f"  3. 常见正确格式: deepseek/deepseek-v4-flash, openai/gpt-5.5\n"
            f"原始错误: {e}"
        )
    if any(kw in err_str for kw in ("connection", "timeout", "timed out", "refused",
                                      "unreachable", "proxy", "ssl", "certificate")):
        proxy_hint = ""
        try:
            from .config import get_settings
            s = get_settings()
            if s.proxy:
                proxy_hint = f"\n  当前代理: {s.proxy} (如代理不可用请关闭)"
            else:
                proxy_hint = (
                    "\n  国内访问 OpenAI/Gemini/Anthropic 需要代理。\n"
                    "  解决方法: 在 config.yaml 中设置 proxy: http://127.0.0.1:7890\n"
                    "  或设置环境变量: set HTTPS_PROXY=http://127.0.0.1:7890\n"
                    "  推荐用 DeepSeek/通义/智谱等国内模型, 无需代理。"
                )
        except Exception:
            pass
        return (
            f"网络连接失败，无法访问 {provider} API。\n"
            f"  1. 检查网络是否正常\n"
            f"  2. 检查防火墙是否拦截{proxy_hint}\n"
            f"原始错误: {e}"
        )
    return str(e)


async def test_connection(cfg: ModelConfig) -> dict:
    """测试模型连接是否正常。返回 {"ok": bool, "message": str}。

    发一个最简单的请求 (1 token), 快速验证 Key + 网络 + 模型名是否正确。
    """
    if litellm is None:
        return {"ok": False, "message": "litellm 未安装, 请运行 pip install litellm"}
    try:
        norm_model = _prepare_env(cfg)
        kwargs: dict[str, Any] = {
            "model": norm_model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
            "timeout": 15,  # 测试用短超时, 15s 够了
        }
        _client = _get_http_client()
        if _client is not None:
            kwargs["client"] = _client
        resp = await litellm.acompletion(**kwargs)
        # 能拿到响应就说明连接正常
        model_name = getattr(resp, "model", cfg.model)
        return {"ok": True, "message": f"连接成功! 模型: {model_name}"}
    except Exception as e:
        friendly = _friendly_error(e, cfg)
        return {"ok": False, "message": friendly}
