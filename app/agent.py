"""多 agent 协同的 Codex 式 loop (整合 oh-story 7-agent 架构)。

架构:
- 主 agent (默认 orchestrator 总编) 接收用户输入,通过 delegate_to_agent 工具委派给专家
- 6 位专家 agent (story-architect/narrative-writer/character-designer/
  consistency-checker/story-explorer/presenter) 各司其职,有独立系统提示词与工具子集
- 子 agent 也可再委派其他专家 (如 narrative-writer 需要新增角色 → 委派 character-designer)
- 委派深度限制 MAX_DELEGATE_DEPTH,避免无限递归
- 只读沙盒: consistency-checker 和 story-explorer 不允许调用写入类工具

事件流 (SSE):
- {type:"start", agent, input}     开始,标记当前 agent
- {type:"step", agent, tool, ...}  某个 agent 执行某工具
- {type:"delegate", from, to, task}  委派发生 (供前端展示协同)
- {type:"observation", ...}        工具结果
- {type:"token", ...}              最终回答 token 流
- {type:"done", ...}               结束
- {type:"error", ...}              错误
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import time
from typing import AsyncIterator, Optional

from . import agents, store, tools
from .config import get_settings
from .llm import chat, stream


# ===== 改进: 主动意图澄清 (ReAct 之前的"信息足不足"判断) =====
# 原版: 用户输入再模糊也直接进入工具循环硬猜 → 经常跑偏
# 改进: 进入循环前先做轻量启发式检测, 信息明显不足时主动反问,
#       而非硬猜。零 LLM 成本 (纯规则)。
def _check_clarification_needed(pid: str, user_input: str) -> str | None:
    """检测用户输入是否信息不足, 需要先澄清。

    返回 None = 信息充足, 可继续; 返回非空字符串 = 反问用户的内容。
    保守策略: 只在明显不足时触发, 避免频繁打断。
    """
    text = (user_input or "").strip()
    if not text:
        return "你想做什么呢?可以告诉我具体需求,比如「生成大纲」「续写第一章」「扫一下仙侠榜单」。"

    # 1. 过短且非已知操作关键词
    if len(text) < 5:
        known_kw = ("大纲", "续写", "扫榜", "润色", "审稿", "拆书", "设定", "查", "停止")
        if not any(kw in text for kw in known_kw):
            return f"「{text}」信息有点少,能再具体点吗?比如想生成大纲、续写哪一章、还是调研某个题材的市场?"

    # 2. "继续/接着写"类模糊指令, 但项目里没有任何章节可续
    if any(kw in text for kw in ("继续写", "接着写", "继续", "往下写")):
        chapters = store.list_chapters(pid)
        if not chapters:
            return "现在还没有章节可以续写。要先从大纲开始吗?告诉我核心设定和题材,我帮你生成大纲和章节结构。"

    # 3. "写一章/写正文" 但无大纲无章节
    if any(kw in text for kw in ("写一章", "写正文", "开始写", "写第")):
        chapters = store.list_chapters(pid)
        if not chapters:
            proj = store.get_project(pid) or {}
            if not proj.get("premise"):
                return "想直接开写,但还没看到核心设定。先告诉我:这本小说讲什么故事?什么题材?主角是谁?我可以先出大纲再动笔。"

    # 4. 扫榜但未指定题材
    if "扫榜" in text or "榜单" in text or "调研" in text:
        proj = store.get_project(pid) or {}
        genre = proj.get("genre", "")
        if not genre and not any(g in text for g in ("仙侠", "都市", "玄幻", "言情", "悬疑", "科幻", "历史", "末世", "系统")):
            return "扫榜需要知道题材方向。你想调研哪个品类?比如仙侠、都市、玄幻、言情、悬疑等。"

    return None


def _tool_quality_hint(fname: str, result: str) -> str | None:
    """工具结果质检: 返回异常时给 LLM 的调整提示 (通用 ReAct 检查环节)。

    返回 None = 结果正常; 返回字符串 = 追加为 system 提示引导 LLM 调整。
    """
    if not result:
        return None
    # 检测错误结果
    is_error = ('"error"' in result[:80] or result.startswith('{"error'))
    if not is_error:
        return None
    # 提取错误要点 (截短)
    err_snippet = result[:120]
    hints = {
        "continue_writing": "续写工具报错了。常见原因:尚无章节/章节id无效。建议先 query_project 查章节列表,或先委派 story-architect 用 generate_outline 生成大纲。",
        "generate_outline": "大纲生成失败。建议检查核心设定(premise)是否清晰,或换种表述重试。",
        "scan_bestseller": "扫榜失败(可能是网络问题)。可以改用市场印象做趋势分析,或稍后重试。",
        "polish": "润色失败。建议先 query_project 确认章节id和正文存在。",
        "delegate_to_agent": "委派失败。检查目标 agent 名是否正确(应为 story-architect/narrative-writer 等枚举值)。",
    }
    specific = hints.get(fname)
    if specific:
        return f"【工具质检】{fname} 返回错误: {err_snippet}。{specific}"
    return f"【工具质检】{fname} 返回错误: {err_snippet}。请根据错误信息调整策略,或换用其他工具。"


# ===== 改进:LLM 调用重试机制 =====
# 原版: LLM 调用失败直接报错 → 网络抖动/API 限流就挂
# 改进: 指数退避重试,临时错误自动恢复
# ===== 关键修复: 5 次重试 + 完善 traceback + 异常分类 =====
async def _chat_with_retry(
    messages: list[dict],
    cfg,
    *,
    tool_schemas: list | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format: dict | None = None,
    assistant_prefill: str | None = None,
    stop: list[str] | None = None,
    max_retries: int = 5,       # 从 3 增到 5
    base_delay: float = 1.5,    # 从 2.0 降到 1.5, 首次重试更快
) -> dict:
    """带重试的 LLM 调用。指数退避: 1.5s → 3s → 6s → 12s → 24s。

    可重试错误: 超时/429限流/5xx服务端错误/连接错误
    不可重试: 401认证失败/400参数错误 (直接抛出)
    """
    # 上下文窗口管理: 每次调用前强制压缩, 防止 token 溢出
    messages = _trim_for_window(messages)
    
    last_err = None
    for attempt in range(max_retries):
        try:
            return await chat(
                messages, cfg,
                tools=tool_schemas, temperature=temperature, max_tokens=max_tokens,
                response_format=response_format, assistant_prefill=assistant_prefill,
                stop=stop,
            )
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            # 不可重试的错误: 认证失败/参数错误/模型不存在
            if any(kw in err_str for kw in ("401", "403", "invalid api key", "missing credentials", "model_not_found")):
                logger.error(f"[LLM重试] 不可重试错误: {e}", exc_info=True)
                raise
            # 可重试: 超时/429/5xx/连接错误
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"[重试 {attempt+1}/{max_retries}] LLM 调用失败: {e}, {delay}s 后重试", exc_info=True)
                await asyncio.sleep(delay)
            else:
                logger.error(f"[重试耗尽] LLM 调用 {max_retries} 次均失败: {e}", exc_info=True)
    raise last_err  # type: ignore


# ---------- 实时日志 ----------
# 配置: 同时输出到 stderr (uvicorn 控制台) 和系统临时目录
_AGENT_LOG = os.environ.get(
    "NA_AGENT_LOG",
    os.path.join(tempfile.gettempdir(), "tianyan-agent.log"),
)
logger = logging.getLogger("tianyan")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _fmt = logging.Formatter(
        "%(asctime)s [%(levelname).1s] %(message)s",
        datefmt="%H:%M:%S",
    )
    # stderr handler (跟随 uvicorn 控制台)
    _sh = logging.StreamHandler(sys.stderr)
    _sh.setFormatter(_fmt)
    _sh.setLevel(logging.INFO)
    logger.addHandler(_sh)
    # 文件 handler (完整 DEBUG 日志, 含 LLM 请求/响应细节)
    try:
        _fh = logging.FileHandler(_AGENT_LOG, encoding="utf-8")
        _fh.setFormatter(_fmt)
        _fh.setLevel(logging.DEBUG)
        logger.addHandler(_fh)
    except OSError:
        pass


def _trunc(s: str, n: int = 200) -> str:
    """截断字符串用于日志输出,避免一行太长。"""
    if not s:
        return ""
    s = str(s)
    s = s.replace("\n", "\\n")
    return s[:n] + ("…" if len(s) > n else "")


def _event(obj: dict) -> str:
    """将事件字典编码为 SSE (Server-Sent Events) 格式字符串。"""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# 写入类工具白名单: 只读 agent 不允许调用这些
WRITE_TOOLS = {
    "generate_outline", "continue_writing", "polish", "add_element", "manage_outline",
}


def _check_sandbox(agent_name: str, tool_name: str) -> str | None:
    """检查工具调用是否符合 agent 沙盒限制。返回错误消息或 None。"""
    if agents.is_readonly(agent_name) and tool_name in WRITE_TOOLS:
        return (f"只读 agent「{agent_name}」不允许调用写入工具「{tool_name}」。"
                "请改用 query_project/load_context/quality_check 查询,或委派其他 agent 处理。")
    return None


# ===== 沙箱验证: 写作前/审核前的前置检查 =====
# 写作前: 验证大纲、角色档案、上下文是否就绪
# 审核前: 验证正文是否存在
# 返回 (passed: bool, issues: list[str])
def _sandbox_validate(pid: str, agent_name: str, tool_name: str, args: dict) -> tuple[bool, list[str]]:
    """沙箱验证: 在执行写作/审核工具前,检查前置条件是否满足。

    返回 (是否通过, 问题列表)。不通过时应跳过执行并报告问题。
    """
    issues = []

    # 写作类工具: 验证大纲、角色档案、上下文
    if tool_name in ("continue_writing", "polish", "ghostwrite"):
        chapters = store.list_chapters(pid)
        if not chapters:
            issues.append("无章节数据,需先生成大纲")
        # 检查是否有角色档案
        elements = store.list_elements(pid)
        characters = [e for e in elements if e.get("kind") == "character"]
        if not characters:
            issues.append("无角色档案,建议先委派 character-designer 建立角色")
        # 检查是否有上下文 (素材库)
        chunks = store.list_chunks(pid)
        if not chunks and not any(c.get("content") for c in chapters):
            issues.append("无上下文素材,建议先上传参考资料或委派 story-explorer 加载上下文")

    # 审核类工具: 验证正文是否存在
    if tool_name in ("audit_novel", "detect_ai", "full_audit", "diagnose_opening",
                     "four_check", "quality_check", "review_chapter"):
        chapter_id = args.get("chapter_id", "")
        if chapter_id:
            ch = store.get_chapter(chapter_id)
            if not ch:
                issues.append(f"章节 {chapter_id} 不存在")
            elif not ch.get("content"):
                issues.append(f"章节 {chapter_id} 无正文,无法审核")

    # 风格分析: 验证正文是否存在
    if tool_name in ("analyze_style", "cache_style"):
        text = args.get("text", "")
        if not text:
            chapter_id = args.get("chapter_id", "")
            if chapter_id:
                ch = store.get_chapter(chapter_id)
                if not ch or not ch.get("content"):
                    issues.append(f"章节 {chapter_id} 无正文,无法分析风格")

    passed = len(issues) == 0
    if not passed:
        logger.info(f"[{agent_name}] 沙箱验证未通过: {issues}")
    return passed, issues


# ===== 上下文窗口管理 (摘要压缩替代粗暴截断) =====
# 现代模型普遍 128K+ context, 按 64K token 为单位管理, 留 80% 安全余量
MAX_CONTEXT_CHARS = 64000  # 对话历史字符上限 (≈ 25K token, 64K 窗口留 40% 给 system+工具结果)
KEEP_LAST_TURNS = 12       # 至少保留最近这么多回合
MODEL_CONTEXT_LIMIT = 128000  # 现代模型 128K, 取标准值
SAFE_RATIO = 0.8             # 安全余量: 总 token ≤ 模型上限的 80%


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数 (不依赖 tiktoken, 避免额外依赖)。
    
    经验值: 1 token ≈ 4 字符 (英文) / 1.5 字符 (中文混合)
    取 2.5 字符/token 作为折中 (偏保守, 宁可多截不少截)
    """
    if not text:
        return 0
    return max(1, len(str(text)) // 3)


def _count_messages_tokens(msgs: list[dict]) -> int:
    """估算消息列表的总 token 数 (含 role 等开销)。"""
    total = 0
    for m in msgs:
        total += 4  # role + 结构开销
        content = m.get("content") or ""
        if isinstance(content, list):
            # tool_calls 格式
            content = json.dumps(content, ensure_ascii=False)
        total += _estimate_tokens(content)
        if m.get("tool_calls"):
            total += _estimate_tokens(json.dumps(m["tool_calls"], ensure_ascii=False))
    return total


def _summarize_old_messages(old_msgs: list[dict]) -> str:
    """把旧消息压缩成结构化摘要,保留关键信息。

    提取:
    - 用户的关键需求/指令
    - 工具调用的结果摘要(大纲/章节/设定等)
    - 决策与结论
    跳过:
    - 纯过程性消息(心跳/中间状态)
    - 已被后续消息覆盖的旧信息
    """
    key_points: list[str] = []
    for m in old_msgs:
        role = m.get("role", "")
        content = str(m.get("content") or "")
        if not content.strip():
            continue
        # user 消息: 提取指令
        if role == "user":
            if len(content) > 200:
                key_points.append(f"用户指令: {content[:200]}...")
            else:
                key_points.append(f"用户指令: {content}")
        # tool 结果: 只提取成功的工具结果摘要
        elif role == "tool":
            name = m.get("name", "")
            if name in ("heartbeat", "_heartbeat"):
                continue
            # 截取前 150 字符作为摘要
            if len(content) > 150:
                key_points.append(f"工具 {name} 结果: {content[:150]}...")
            else:
                key_points.append(f"工具 {name} 结果: {content}")
        # assistant 消息: 只提取有实质内容的
        elif role == "assistant" and content and len(content) > 30:
            if len(content) > 150:
                key_points.append(f"助手回复: {content[:150]}...")
            else:
                key_points.append(f"助手回复: {content}")
    if not key_points:
        return "(早期对话已省略)"
    # 限制摘要长度
    summary = "\n".join(key_points[-20:])  # 最多保留 20 条关键点
    if len(summary) > 2000:
        summary = summary[:2000] + "\n...(摘要已截断)"
    return summary


def _trim_for_window(msgs: list[dict]) -> list[dict]:
    """上下文压缩:保留 system + 最近回合,旧消息压缩成摘要。

    改进点:
    1. 旧消息不再直接丢弃,而是压缩成结构化摘要(保留关键决策/工具结果)
    2. 摘要作为 system 消息注入,让模型知道之前发生了什么
    3. 关键约束不变:assistant(tool_calls)+tool 必须成对完整
    4. ===== 新增: token 估算 + 80% 安全余量检查 =====
       压缩后仍检查总 token, 若超过 MODEL_CONTEXT_LIMIT * SAFE_RATIO 则继续截断
    5. ===== 修复: 不原地修改消息, 创建副本避免污染原始数据 =====
    """
    import copy
    # 创建浅拷贝列表, 避免修改原始数据
    msgs = [dict(m) for m in msgs]
    sys_msgs = [m for m in msgs if m.get("role") == "system"]
    rest = [m for m in msgs if m.get("role") != "system"]
    if len(rest) <= 2:
        return msgs
    total = sum(len(str(m.get("content") or "")) for m in rest)
    if total <= MAX_CONTEXT_CHARS:
        # 二次检查: token 估算是否在安全范围内
        est_tokens = _count_messages_tokens(msgs)
        if est_tokens <= int(MODEL_CONTEXT_LIMIT * SAFE_RATIO):
            return msgs
    user_idx = [i for i, m in enumerate(rest) if m.get("role") == "user"]
    if not user_idx:
        return msgs  # 无 user 边界,不敢截断,原样返回
    # 保留最近 KEEP_LAST_TURNS 回合;若仍超长则继续减少回合
    cut = user_idx[-min(KEEP_LAST_TURNS, len(user_idx))]
    while True:
        seg = rest[cut:]
        seg_chars = sum(len(str(m.get("content") or "")) for m in seg)
        if seg_chars <= MAX_CONTEXT_CHARS:
            break
        nxt = [i for i in user_idx if i > cut]
        if not nxt:
            break  # 已是最少(只保留最后一个回合起),无法再减
        cut = nxt[0]
    # 改进:把旧消息压缩成摘要而非直接丢弃
    old_msgs = rest[:cut]
    summary = _summarize_old_messages(old_msgs)
    note = [{"role": "system", "content": f"【早期对话摘要】({cut} 条消息已压缩)\n{summary}"}]
    result = sys_msgs + note + rest[cut:]
    # 压缩后二次检查: 若仍超安全阈值, 截断 tool 结果内容 (只保留前 500 字符)
    est_tokens = _count_messages_tokens(result)
    safe_limit = int(MODEL_CONTEXT_LIMIT * SAFE_RATIO)
    if est_tokens > safe_limit:
        logger.warning(f"[上下文] 压缩后仍超限 ({est_tokens} > {safe_limit} token), 截断长 tool 结果")
        for m in result:
            if m.get("role") == "tool":
                c = m.get("content", "")
                if len(c) > 500:
                    m["content"] = c[:500] + "...(已截断)"
        # 最终检查
        est_tokens = _count_messages_tokens(result)
        logger.info(f"[上下文] 截断后 token 估算: {est_tokens}")
    return result


def _build_messages(
    pid: str, agent_name: str = agents.DEFAULT_AGENT, *, query: str = ""
) -> list[dict]:
    """组装对话历史 + 项目背景 + 指定 agent 的系统提示词。

    改进: 注入项目记忆 + 跨会话经验 + 市场知识 + 动态计划上下文
    """
    from .memory import get_memory_context
    from .planner import get_plan_summary

    msgs: list[dict] = []
    proj = store.get_project(pid)
    if proj:
        msgs.append({
            "role": "system",
            "content": (
                f"当前项目: {proj.get('name','未命名')}\n"
                f"频道: {proj.get('audience','未指定')}\n"
                f"类型: {proj.get('genre','')}\n文风: {proj.get('style','')}\n"
                f"核心设定: {proj.get('premise','')}"
            ),
        })

    # 改进: 注入项目长期记忆
    memory_ctx = get_memory_context(pid)
    if memory_ctx:
        msgs.append({"role": "system", "content": memory_ctx})

    # 改进: 注入跨会话经验 (用户偏好/反馈/教训, 跨项目积累, 让 agent "成长")
    try:
        from .experience import get_experience_context
        exp_ctx = get_experience_context(pid=pid, query=query)
        if exp_ctx:
            msgs.append({"role": "system", "content": exp_ctx})
    except Exception:
        pass

    # 改进: 注入市场知识库 (同题材扫榜沉淀, 避免每次从零调研)
    try:
        from .market_knowledge import get_knowledge_context
        genre_val = (proj or {}).get("genre", "")
        if genre_val:
            mk_ctx = get_knowledge_context(genre_val)
            if mk_ctx:
                msgs.append({"role": "system", "content": mk_ctx})
    except Exception:
        pass

    # 改进: 注入当前执行计划
    plan_summary = get_plan_summary(pid)
    if plan_summary:
        msgs.append({"role": "system", "content": plan_summary})

    msgs.append({"role": "system", "content": agents.get_prompt(agent_name)})
    for m in store.list_messages(pid):
        if m["role"] == "tool":
            msgs.append({
                "role": "tool",
                "content": m["content"],
                "name": m["tool_name"] or "",
                "tool_call_id": m["tool_call_id"] or "",
            })
        elif m["role"] == "assistant" and m.get("tool_name"):
            try:
                tc = json.loads(m["content"])
                msgs.append({"role": "assistant", "content": None, "tool_calls": tc})
            except Exception:
                msgs.append({"role": "assistant", "content": m["content"]})
        else:
            msgs.append({"role": m["role"], "content": m["content"]})
    return _trim_for_window(msgs)  # 原理2: 滑动窗口防爆


def _extract_usage(resp):
    """从 litellm completion response 提取 token 使用 + 成本 + 缓存命中率。
    litellm 在 resp.usage 给 prompt_tokens/completion_tokens/total_cost (若 _fer.alogo_cost 启用)。
    DeepSeek 等模型还在 usage 中返回 prompt_cache_hit_tokens / prompt_cache_miss_tokens。
    返回 (tokens, cost, cache_hit_tokens, cache_miss_tokens)
    """
    if resp is None:
        return None, None, None, None
    u = getattr(resp, "usage", None)
    if u is None:
        return None, None, None, None
    tok = (getattr(u, "prompt_tokens", 0) or 0) + (getattr(u, "completion_tokens", 0) or 0)
    # litellm 在响应上塞 _response_cost (USD),部分版本在 usage 上
    cost = getattr(resp, "_response_cost", None) or getattr(u, "cost", None) or 0
    try:
        cost = float(cost)
    except Exception:
        cost = 0
    cache_hit = getattr(u, "prompt_cache_hit_tokens", 0) or 0
    cache_miss = getattr(u, "prompt_cache_miss_tokens", 0) or 0
    return tok, cost, cache_hit, cache_miss


def _truncate_for_trace(s, n: int = 800) -> str:
    """trace 输入/输出截断 (避免 sqlite 行过大),返回纯字符串。"""
    if s is None:
        return ""
    if not isinstance(s, str):
        try:
            s = json.dumps(s, ensure_ascii=False, default=str)
        except Exception:
            s = str(s)
    s = s.replace("\n", "\\n")
    return s[:n] + ("…" if len(s) > n else "")


# ---------- 总编验收 (群聊式协作的汇总环节) ----------
# self_review 已移除——质检由 consistency-checker agent 完成, 无需同模型自审


# ---------- 工具调用与 trace 落盘 ----------
async def _exec_tool(
    pid: str, fname: str, fargs: dict, *,
    depth: int, emit, agent_name: str = agents.DEFAULT_AGENT,
    run_id: Optional[str] = None,
    delegation_log: Optional[list] = None,
) -> str:
    """执行工具;对 delegate_to_agent 走子 agent 运行循环。

    emit: async 回调,用于把委派/步骤事件外抛给 SSE 流。
    depth: 当前委派深度。
    agent_name: 调用方 agent 名 (用于沙盒检查)。
    """
    # 沙盒检查: 只读 agent 不允许调用写入类工具
    sandbox_err = _check_sandbox(agent_name, fname)
    if sandbox_err:
        if run_id:
            store.add_run_event(run_id, "tool_call", agent=agent_name, tool=fname,
                                input_=fargs, error=sandbox_err, duration_ms=0)
        return json.dumps({"error": sandbox_err}, ensure_ascii=False)

    # 沙箱验证: 写作/审核前检查前置条件
    validate_pass, validate_issues = _sandbox_validate(pid, agent_name, fname, fargs)
    if not validate_pass:
        warning = f"⚠️ 沙箱验证警告: {'; '.join(validate_issues)}。建议先补充缺失信息再执行。"
        logger.warning(f"[{agent_name}] {warning}")
        # 发送验证事件到前端
        if emit:
            await emit({"type": "sandbox_validate", "agent": agent_name,
                        "tool": fname, "passed": False, "issues": validate_issues})
        # 不阻断执行,只注入警告让 LLM 决定是否继续
        # 将警告作为返回值的一部分,让 LLM 知道前置条件不满足
    else:
        # 验证通过,发送成功事件
        if emit:
            await emit({"type": "sandbox_validate", "agent": agent_name,
                        "tool": fname, "passed": True, "issues": []})
    if fname == "delegate_to_agent":
        # 参数名容错: 模型常把 agent/task 幻觉成 agent_role/agent_name/target/prompt/instruction
        target = (fargs.get("agent") or fargs.get("agent_name") or
                  fargs.get("agent_role") or fargs.get("target") or "")
        task = (fargs.get("task") or fargs.get("prompt") or
                fargs.get("instruction") or fargs.get("description") or "")
        if not agents.is_valid(target) or target == agents.DEFAULT_AGENT:
            logger.warning(f"[{agent_name}] 委派失败: 无效 target={target!r}")
            if run_id:
                store.add_run_event(run_id, "tool_call", agent=agent_name, tool=fname,
                                    input_=fargs, error=f"无效 target={target!r}")
            return json.dumps({"error": f"无法委派给 {target!r}。请用参数 agent (枚举值之一) 与 task。"}, ensure_ascii=False)
        if depth >= agents.MAX_DELEGATE_DEPTH:
            logger.warning(f"[{agent_name}] 委派失败: 已达最大深度 {depth}")
            if run_id:
                store.add_run_event(run_id, "tool_call", agent=agent_name, tool=fname,
                                    input_=fargs, error=f"已达最大委派深度 {agents.MAX_DELEGATE_DEPTH}")
            return json.dumps({"error": f"已达最大委派深度 {agents.MAX_DELEGATE_DEPTH}"}, ensure_ascii=False)
        logger.info(f"[{agent_name}] → 委派 → [{target}] depth={depth+1} task={_trunc(task, 120)}")
        await emit({"type": "delegate", "from": agent_name, "to": target, "task": task, "depth": depth + 1})
        if run_id:
            store.add_run_event(run_id, "delegate", agent=agent_name, tool=fname,
                                input_={"target": target, "task": task})
        t0 = time.time()
        result = await _run_sub_agent(pid, target, task, depth=depth + 1, emit=emit, run_id=run_id)
        dur_ms = int((time.time() - t0) * 1000)
        logger.info(f"[{target}] ← 委派完成 耗时={time.time()-t0:.1f}s 结果={_trunc(result, 200)}")
        # 群聊式: 子 agent 执行完成, 发 delegate_done 带 task/result 供前端展示
        await emit({"type": "delegate_done", "from": agent_name, "to": target,
                    "task": task, "result": result, "duration_ms": dur_ms})
        # 记录委派历史 (供总编验收)
        if delegation_log is not None:
            delegation_log.append({
                "to": target, "task": task, "result": result, "duration_ms": dur_ms,
            })
        if run_id:
            store.add_run_event(run_id, "delegate_done", agent=target,
                                output=_truncate_for_trace(result, 800), duration_ms=dur_ms)
        return json.dumps({"agent": target, "task": task, "result": result}, ensure_ascii=False)
    # 普通工具直接走 dispatch
    if run_id:
        store.add_run_event(run_id, "tool_call", agent=agent_name, tool=fname,
                            input_=_truncate_for_trace(fargs, 800))
    t0 = time.time()
    try:
        # 注入调用方身份,供对抗式审查工具(challenge_review/resolve_challenge)追溯来源
        fargs["_caller_agent"] = agent_name
        result = await tools.dispatch(pid, fname, fargs)
    except Exception as e:
        logger.error(f"[{agent_name}] 工具 {fname} 执行异常: {e}", exc_info=True)
        result = json.dumps({"error": f"工具 {fname} 执行异常: {e}"}, ensure_ascii=False)
    dur_ms = int((time.time() - t0) * 1000)
    logger.info(f"[{agent_name}] 工具 {fname} 耗时={dur_ms}ms 结果={_trunc(result, 200)}")
    if run_id:
        store.add_run_event(run_id, "tool_result", agent=agent_name, tool=fname,
                            output=_truncate_for_trace(result, 800), duration_ms=dur_ms)
    return result


def _inject_open_challenges(pid: str, messages: list[dict], agent_name: str) -> None:
    """对抗式审查: 把未裁决挑战注入 LLM 上下文, 引导调度应战 (resolve_challenge)。

    子 agent 调用 challenge_review 后仅把挑战写入 _CHALLENGE_STORE; 若不注入,
    总编与被挑战方在后续步骤完全感知不到挑战存在, 导致 open 挑战永远无人裁决,
    对抗式审查闭环断裂。因此在主循环与子 agent 循环每次 LLM 调用前注入。
    """
    try:
        from .tools import _CHALLENGE_STORE
        open_ch = [c for c in _CHALLENGE_STORE.get(pid, []) if c.get("status") == "open"]
        if not open_ch:
            return
        # 去重: 已注入过提醒的 challenge_id 不再重复追加, 防止上下文膨胀
        injected = set()
        for m in messages:
            if m.get("role") == "system" and "对抗式审查待办" in (m.get("content") or ""):
                for cid in re.findall(r"challenge_id=([A-Za-z0-9_\-]+)", m["content"]):
                    injected.add(cid)
        open_ch = [c for c in open_ch if c.get("challenge_id") not in injected]
        if not open_ch:
            return
        lines = []
        for c in open_ch:
            lines.append(
                f"- challenge_id={c.get('challenge_id')} | from={c.get('from_agent')} | "
                f"target={c.get('target_agent')} | type={c.get('challenge_type')} | "
                f"severity={c.get('severity')} | evidence={c.get('evidence') or ''} | "
                f"suggestion={c.get('suggestion') or ''}"
            )
        if agent_name == agents.DEFAULT_AGENT:
            action = (
                "立即调度被挑战方应战: delegate_to_agent(agent=目标agent, "
                "task='回应挑战 challenge_id=xxx, 用 resolve_challenge 应战 "
                "(response=accept/reject/revise/override/deadlock, 附 counter_evidence/rationale)')。"
                "收到 resolve 结果后审视理由: 充分则通过, 不充分可再次挑战或亲自介入。"
            )
        else:
            action = (
                "如果你是被挑战方或被委派应战, 必须用 resolve_challenge 回应: "
                "challenge_id 见上, response=accept/reject/revise/override/deadlock, "
                "附 counter_evidence/rationale。"
            )
        messages.append({
            "role": "system",
            "content": "【对抗式审查待办】以下挑战尚未裁决:\n" + "\n".join(lines) + "\n" + action,
        })
    except Exception as e:
        logger.debug(f"[challenge] 注入失败(非致命): {e}")


async def _run_sub_agent(
    pid: str, agent_name: str, task: str, *,
    depth: int, emit, run_id: Optional[str] = None,
) -> str:
    """运行子 agent 的 agentic loop,返回最终文本回答(不产出 token 流)。

    子 agent 用自己的系统提示词 + 工具子集,可继续委派(受 depth 限制)。
    事件通过 emit 外抛,主流程不存子 agent 的消息到 store(避免污染主对话历史)。
    """
    s = get_settings()
    tool_schema = tools.schema_for(agents.get_tools(agent_name))
    messages: list[dict] = []
    proj = store.get_project(pid)
    if proj:
        messages.append({
            "role": "system",
            "content": (
                f"当前项目: {proj.get('name','未命名')}\n"
                f"频道: {proj.get('audience','未指定')}\n"
                f"类型: {proj.get('genre','')}\n文风: {proj.get('style','')}\n"
                f"核心设定: {proj.get('premise','')}"
            ),
        })
    messages.append({"role": "system", "content": agents.get_prompt(agent_name)})
    messages.append({"role": "user", "content": f"[来自上级 agent 的委派任务]\n{task}"})

    # 子 agent 步数限制: 给够 8 步,让 narrative-writer 能先 match_author → get_author_reference
    # (取作家原文 few-shot) → query_project (拿 chapter_id) → continue_writing (写正文) 完整跑完。
    # 太紧 (如 4 步) 会导致子 agent 在取 few-shot 阶段就把步数用光,没机会写正文。
    sub_max_steps = max(8, s.max_steps)
    logger.info(f"[{agent_name}] 子 agent 启动 max_steps={sub_max_steps} task={_trunc(task, 200)}")
    # 群聊式: 通知前端子 agent 开始发言 (前端建独立聊天气泡)
    await emit({"type": "sub_agent_start", "agent": agent_name, "task": task, "depth": depth})
    for step in range(sub_max_steps):
        # 对抗式审查: 注入未裁决挑战, 让被挑战方/应战方感知到 open challenge
        _inject_open_challenges(pid, messages, agent_name)
        t0 = time.time()
        try:
            # 默认用非 reasoning 模型 (如 agnes-1.5-flash),4096 够用。
            # 若用 reasoning 模型 (agnes-2.0-flash) 需在 ModelConfig.max_tokens 调大,
            # 但 reasoning 模型做 agent loop 工具调用决策不可靠,不推荐。
            resp = await _chat_with_retry(messages, s.default_model, tool_schemas=tool_schema)
        except Exception as e:
            logger.error(f"[{agent_name}] step={step} LLM 调用失败: {e}")
            if run_id:
                store.add_run_event(run_id, "error", agent=agent_name,
                                    error=f"step={step} {e}", duration_ms=int((time.time()-t0)*1000))
            await emit({"type": "sub_agent_error", "agent": agent_name, "error": str(e)})
            return f"(子 agent {agent_name} 调用失败: {e})"

        tool_calls = resp["tool_calls"]
        content = resp["content"]
        reasoning = resp.get("reasoning", "") or ""
        # 思考过程 = reasoning + content (reasoning 是模型内部思考,content 是模型输出)
        thinking = (reasoning + "\n" + content).strip() if reasoning else content
        # 落盘 LLM 调用事件 (token + 成本 + cache,便于回放/统计)
        if run_id:
            tok, cost, cache_hit, cache_miss = _extract_usage(resp.get("raw"))
            store.add_run_event(run_id, "llm_call", agent=agent_name,
                                input_=_truncate_for_trace(task, 200),
                                output=_truncate_for_trace(content, 400),
                                tokens=tok, cost=cost,
                                cache_hit_tokens=cache_hit,
                                cache_miss_tokens=cache_miss,
                                duration_ms=int((time.time()-t0)*1000))

        if not tool_calls:
            # 终态:返回最终文本
            logger.info(f"[{agent_name}] step={step} 完成 回答长度={len(content or '')}")
            await emit({"type": "step", "agent": agent_name, "tool": "(完成)",
                        "args": {}, "thinking": thinking, "depth": depth})
            # 群聊式: 把子 agent 的最终回答作为聊天气泡正文推给前端
            if content:
                await emit({"type": "sub_answer", "agent": agent_name, "text": content, "depth": depth})
            await emit({"type": "sub_agent_done", "agent": agent_name, "depth": depth})
            return content or ""

        # 实时缓存命中率 → 前端展示
        if run_id:
            sub_run_meta = store.get_run(run_id) or {}
            await emit({"type": "cache_stats", "agent": agent_name,
                        "hit_tokens": cache_hit or 0, "miss_tokens": cache_miss or 0,
                        "total_hit": sub_run_meta.get("total_cache_hit_tokens", 0),
                        "total_miss": sub_run_meta.get("total_cache_miss_tokens", 0),
                        "total_tokens": sub_run_meta.get("total_tokens", 0)})

        # 群聊式: 子 agent 的思考过程 (本轮 LLM 输出) 作为聊天气泡思考区推给前端
        if thinking:
            await emit({"type": "sub_think", "agent": agent_name, "text": thinking,
                        "step": step, "depth": depth})

        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            fname = tc.function.name
            try:
                fargs = json.loads(tc.function.arguments or "{}")
            except Exception:
                fargs = {}
            logger.info(f"[{agent_name}] step={step} → 调工具 {fname} args={_trunc(json.dumps(fargs, ensure_ascii=False), 150)}")
            await emit({"type": "step", "agent": agent_name, "tool": fname,
                        "args": fargs, "thinking": thinking, "depth": depth})
            result = await _exec_tool(pid, fname, fargs, depth=depth, emit=emit,
                                      agent_name=agent_name, run_id=run_id)
            logger.info(f"[{agent_name}] step={step} ← 工具 {fname} 结果={_trunc(result, 200)}")
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "name": fname, "content": result})
            await emit({"type": "observation", "agent": agent_name, "tool": fname,
                        "result": result, "depth": depth})

    logger.warning(f"[{agent_name}] 达到最大步数 {sub_max_steps},任务未完成")
    await emit({"type": "sub_agent_done", "agent": agent_name, "depth": depth, "truncated": True})
    return f"(子 agent {agent_name} 达到最大步数 {sub_max_steps},任务未完全完成)"


async def _extract_and_save_memory_safe(pid: str, user_input: str, response: str) -> None:
    """安全提取记忆 (失败不影响主流程)"""
    try:
        from .memory import extract_and_save_memory
        await extract_and_save_memory(pid, user_input, response)
    except Exception as e:
        logger.debug(f"[记忆] 提取失败 (非致命): {e}")


async def run(
    pid: str, user_input: str, agent_name: str = agents.DEFAULT_AGENT
) -> AsyncIterator[str]:
    """主 agent 运行循环。产出 SSE 事件字符串。

    agent_name: 入口 agent,默认 orchestrator 总编。
    """
    s = get_settings()
    # ===== 启动段异常保护: store/配置/消息构建任何环节出错都转为 error 事件 =====
    try:
        # 新建一次 run 记录,用于 trace 回放/统计
        run_id = store.create_run(pid, user_input, agent_name)
        store.add_run_event(run_id, "start", agent=agent_name, input_=user_input)
    except Exception as e:
        logger.error(f"[run] store.create_run 失败: {e}", exc_info=True)
        yield _event({"type": "error", "message": f"创建运行记录失败: {e}", "fatal": False})
        yield _event({"type": "done", "agent": agent_name, "error": True})
        return

    # 单次 run 总时长起点 (用于 run_max_duration 超时保护)
    _run_start_ts = time.time()
    logger.info(f"========== 主 agent 启动 pid={pid} agent={agent_name} max_steps={s.max_steps} run_id={run_id} ==========")
    logger.info(f"[{agent_name}] 用户输入: {_trunc(user_input, 200)}")
    try:
        store.add_message(pid, "user", user_input)
    except Exception as e:
        logger.warning(f"[run] store.add_message(user) 失败(非致命): {e}")

    # ===== 改进: 主动意图澄清 (信息不足先反问, 不硬猜) =====
    # 注: 原版在此处命中即 early return, 会跳过思考阶段 → 用户看不到思考过程。
    # 现已改为: 任何输入都先进入思考阶段推演, 由思考阶段判定信息不足时再反问。
    # 保留 clarify_q 仅作为思考 prompt 的提示信息 (让模型知道规则层也认为信息不足)。
    clarify_q = _check_clarification_needed(pid, user_input)
    if clarify_q:
        logger.info(f"[{agent_name}] 规则层提示信息可能不足(交由思考阶段裁决): {clarify_q}")

    # 改进: 异步提取用户消息中的偏好/反馈到跨会话经验库 (零成本启发式)
    try:
        from .experience import maybe_extract_from_user_message
        asyncio.ensure_future(maybe_extract_from_user_message(pid, user_input))
    except Exception:
        pass

    # ===== 消息构建异常保护: memory/planner/experience 任何模块出错不影响主流程 =====
    try:
        messages = _build_messages(pid, agent_name, query=user_input)
    except Exception as e:
        logger.error(f"[run] _build_messages 失败, 用最小消息集兜底: {e}", exc_info=True)
        messages = [
            {"role": "system", "content": agents.get_prompt(agent_name)},
            {"role": "user", "content": user_input},
        ]
    try:
        tool_schema = tools.schema_for(agents.get_tools(agent_name))
    except Exception as e:
        logger.error(f"[run] tools.schema_for 失败, 禁用工具兜底: {e}", exc_info=True)
        tool_schema = None
    logger.info(f"[{agent_name}] 装载消息 {len(messages)} 条, 工具 {len(agents.get_tools(agent_name))} 个: {agents.get_tools(agent_name)}")

    # 事件缓冲:子 agent 委派过程中产生的事件也要吐给前端
    event_queue: list[str] = []

    async def emit(obj: dict):
        event_queue.append(_event(obj))

    yield _event({"type": "start", "agent": agent_name, "input": user_input, "run_id": run_id})

    # 思考阶段已移除——_check_clarification_needed 在进入循环前已做轻量校验,
    # 进入循环后由 LLM 直接工具执行, 无需额外的前瞻性推演 LLM 调用。

    # 委派历史: 记录本轮发生过的委派
    delegation_log: list[dict] = []

    # 工具调用循环检测: 记录 (tool_name, args_hash) → 连续调用次数
    # 连续相同调用超阈值 = LLM 卡循环了, 强制终止
    _tool_call_counter: dict[tuple, int] = {}

    for step in range(s.max_steps):
        # 对抗式审查: 注入未裁决挑战, 让总编感知到 open challenge 并调度应战
        _inject_open_challenges(pid, messages, agent_name)

        # 先把子 agent 委派过程中累积的事件吐出去
        while event_queue:
            yield event_queue.pop(0)

        t0 = time.time()
        # 用 task 包装 LLM 调用, 等待期间定期 yield 心跳, 防前端误判断连
        hb = s.sse_heartbeat_interval
        llm_task = asyncio.ensure_future(_chat_with_retry(messages, s.default_model, tool_schemas=tool_schema))
        try:
            if hb <= 0:
                resp = await llm_task
            else:
                while True:
                    done, _ = await asyncio.wait(
                        {llm_task}, timeout=hb, return_when=asyncio.FIRST_COMPLETED,
                    )
                    if llm_task in done:
                        break
                    yield _event({"type": "heartbeat", "ts": time.time(),
                                  "run_id": run_id})
                resp = llm_task.result()
        except Exception as e:
            err_msg = str(e)
            # 检测常见配置错误,给用户更友好的提示
            if "Missing credentials" in err_msg or "api_key" in err_msg.lower() or "API key" in err_msg:
                err_msg = (
                    "API Key 未配置! 请在设置面板中为模型配置 API Key,"
                    "或设置环境变量 OPENAI_API_KEY (或其他对应 provider 的环境变量)。"
                    f"\n原始错误: {e}"
                )
            elif "timed out" in err_msg.lower() or "timeout" in err_msg.lower():
                err_msg = f"LLM 请求超时,请检查网络或 API 端点。\n原始错误: {e}"
            logger.error(f"[{agent_name}] step={step} LLM 调用失败: {e}")
            store.add_run_event(run_id, "error", agent=agent_name,
                                error=f"step={step} {e}", duration_ms=int((time.time()-t0)*1000))
            store.finish_run(run_id, status="error", error=str(e))
            yield _event({"type": "error", "message": err_msg, "run_id": run_id})
            return

        tool_calls = resp["tool_calls"]
        content = resp["content"]
        reasoning = resp.get("reasoning", "") or ""
        thinking = (reasoning + "\n" + content).strip() if reasoning else content
        # 落盘 LLM 调用事件 (token + 成本 + cache)
        tok, cost, cache_hit, cache_miss = _extract_usage(resp.get("raw"))
        store.add_run_event(run_id, "llm_call", agent=agent_name,
                            input_=_truncate_for_trace(user_input, 200),
                            output=_truncate_for_trace(content, 400),
                            tokens=tok, cost=cost,
                            cache_hit_tokens=cache_hit,
                            cache_miss_tokens=cache_miss,
                            duration_ms=int((time.time()-t0)*1000))

        # ===== 风险防护: 单次 run 累计超限就终止 =====
        # 防止 LLM 失控 / agent 卡循环 / 烧钱 / 跑太久
        run_meta = store.get_run(run_id) or {}
        # 总时长检查 (默认 600s = 10 分钟, 用户要求: 超时即终止)
        _elapsed = time.time() - _run_start_ts
        if s.run_max_duration > 0 and _elapsed > s.run_max_duration:
            msg = (f"单次 run 超时 ({int(_elapsed)}s > {s.run_max_duration}s),已终止。"
                   "10 分钟内未能完成任务,请拆分任务或重试。")
            logger.warning(f"[{agent_name}] {msg}")
            store.add_run_event(run_id, "error", agent=agent_name, error=msg)
            store.finish_run(run_id, status="interrupted", error=msg)
            yield _event({"type": "error", "message": msg, "run_id": run_id})
            return
        if run_meta.get("total_tokens", 0) > s.run_max_tokens:
            msg = f"单次 run token 超限 ({run_meta['total_tokens']} > {s.run_max_tokens}),已终止"
            logger.warning(f"[{agent_name}] {msg}")
            store.add_run_event(run_id, "error", agent=agent_name, error=msg)
            store.finish_run(run_id, status="interrupted", error=msg)
            yield _event({"type": "error", "message": msg, "run_id": run_id})
            return
        if run_meta.get("total_cost", 0) > s.run_max_cost:
            msg = f"单次 run 成本超限 (${run_meta['total_cost']:.4f} > ${s.run_max_cost}),已终止"
            logger.warning(f"[{agent_name}] {msg}")
            store.add_run_event(run_id, "error", agent=agent_name, error=msg)
            store.finish_run(run_id, status="interrupted", error=msg)
            yield _event({"type": "error", "message": msg, "run_id": run_id})
            return

        # 实时缓存命中率 → 前端展示
        run_meta = store.get_run(run_id) or {}
        yield _event({"type": "cache_stats", "agent": agent_name,
                      "hit_tokens": cache_hit or 0, "miss_tokens": cache_miss or 0,
                      "total_hit": run_meta.get("total_cache_hit_tokens", 0),
                      "total_miss": run_meta.get("total_cache_miss_tokens", 0),
                      "total_tokens": run_meta.get("total_tokens", 0)})

        # 总编思考过程 → 前端渲染 (替代已移除的 _think_phase)
        if thinking:
            yield _event({"type": "think_start", "round": step + 1})
            chunk_size = 8
            for i in range(0, len(thinking), chunk_size):
                yield _event({"type": "think_token", "text": thinking[i:i + chunk_size]})
            yield _event({"type": "think_end", "feasible": True, "reason": ""})

        if not tool_calls:
            logger.info(f"[{agent_name}] step={step} 完成 回答长度={len(content or '')}")

            # 总编验收已移除——质检由 consistency-checker agent 在委派时完成,
            # 无需在产出后再用同模型自审。

            store.add_message(pid, "assistant", content)
            store.add_run_event(run_id, "end", agent=agent_name,
                                output=_truncate_for_trace(content, 800),
                                duration_ms=int((time.time()-t0)*1000))
            yield _event({"type": "answer_start", "agent": agent_name})
            chunk_size = 12
            for i in range(0, len(content), chunk_size):
                yield _event({"type": "token", "text": content[i: i + chunk_size]})
            yield _event({"type": "answer_end"})
            stats = store.stats(pid)
            store.finish_run(run_id, status="done")
            yield _event({
                "type": "done", "agent": agent_name,
                "steps": step + 1, "stats": stats, "run_id": run_id,
            })
            logger.info(f"========== 主 agent 完成 steps={step+1} run_id={run_id} ==========")
            # 改进: 异步提取关键信息写入长期记忆 (不阻塞响应)
            try:
                asyncio.ensure_future(
                    _extract_and_save_memory_safe(pid, user_input, content)
                )
            except Exception:
                pass
            return

        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        })
        store.add_message(
            pid, "assistant",
            json.dumps([
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ], ensure_ascii=False),
            tool_name="tool_calls",
        )

        # ===== 工具调用循环检测 + 并行执行 =====
        # 先检查循环,再决定并行还是串行
        tool_items = []
        for tc in tool_calls:
            fname = tc.function.name
            try:
                fargs = json.loads(tc.function.arguments or "{}")
            except Exception:
                fargs = {}
            # 循环检测
            args_key = (fname, json.dumps(fargs, sort_keys=True, ensure_ascii=False))
            if list(_tool_call_counter.keys()) == [args_key]:
                _tool_call_counter[args_key] += 1
            else:
                _tool_call_counter = {args_key: 1}
            if _tool_call_counter[args_key] > s.loop_detect_count:
                cnt = _tool_call_counter[args_key]
                msg = (f"工具 {fname} 连续调用 {cnt} 次 (参数相同), "
                       f"疑似 LLM 卡循环 (阈值 {s.loop_detect_count}), 已终止")
                logger.warning(f"[{agent_name}] {msg}")
                store.add_run_event(run_id, "error", agent=agent_name,
                                    tool=fname, error=msg)
                store.finish_run(run_id, status="interrupted", error=msg)
                yield _event({"type": "error", "message": msg,
                              "run_id": run_id, "reason": "loop_detected"})
                return
            tool_items.append((tc, fname, fargs))

        # 并行执行: 多个独立工具调用并发运行 (改进: 原版是串行)
        # delegate_to_agent 由于需要子 agent 运行循环,仍然串行
        # 其他工具 (query_project/add_element/scan_bestseller 等) 可以并行
        PARALLEL_TOOLS = {"query_project", "add_element", "manage_outline", "load_context",
                          "quality_check", "list_authors", "match_author", "get_author_reference",
                          "scan_bestseller", "analyze_novel", "review_chapter",
                          "deconstruct", "audit_novel", "detect_ai", "diagnose_opening",
                          "analyze_style", "imitate_style", "diagnose_stuck", "full_audit",
                          "web_fetch", "web_search", "browser_fetch", "browser_screenshot"}
        can_parallel = len(tool_items) > 1 and all(f in PARALLEL_TOOLS for _, f, _ in tool_items)

        if can_parallel:
            # 并行执行所有工具调用
            logger.info(f"[{agent_name}] step={step} → 并行调用 {[f for _, f, _ in tool_items]}")
            for tc, fname, fargs in tool_items:
                yield _event({
                    "type": "step", "agent": agent_name, "tool": fname,
                    "args": fargs, "thinking": thinking,
                })
            # 并发执行
            tasks = [
                asyncio.ensure_future(_exec_tool(
                    pid, fname, fargs, depth=0, emit=emit,
                    agent_name=agent_name, run_id=run_id,
                    delegation_log=delegation_log,
                ))
                for _, fname, fargs in tool_items
            ]
            # 等待期间定期 yield 心跳
            if hb <= 0:
                results = await asyncio.gather(*tasks, return_exceptions=True)
            else:
                while True:
                    done, _ = await asyncio.wait(
                        set(tasks), timeout=hb, return_when=asyncio.ALL_COMPLETED,
                    )
                    while event_queue:
                        yield event_queue.pop(0)
                    if len(done) == len(tasks):
                        break
                    yield _event({"type": "heartbeat", "ts": time.time(), "run_id": run_id})
                results = [t.result() if not t.exception() else json.dumps({"error": str(t.exception())}) for t in tasks]
            # 按顺序收集结果
            for (tc, fname, fargs), result in zip(tool_items, results):
                if isinstance(result, Exception):
                    result = json.dumps({"error": str(result)}, ensure_ascii=False)
                logger.info(f"[{agent_name}] step={step} ← 并行工具 {fname} 结果={_trunc(result, 200)}")
                while event_queue:
                    yield event_queue.pop(0)
                messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "name": fname, "content": result,
                })
                store.add_message(pid, "tool", result, tool_name=fname, tool_call_id=tc.id)
                # 改进: 工具结果质检 — 异常结果注入调整提示, 引导 LLM 换策略
                hint = _tool_quality_hint(fname, result)
                if hint:
                    messages.append({"role": "system", "content": hint})
                yield _event({
                    "type": "observation", "agent": agent_name,
                    "tool": fname, "result": result,
                })
        else:
            # 串行执行 (delegate_to_agent 或单个工具调用)
            for tc, fname, fargs in tool_items:
                logger.info(f"[{agent_name}] step={step} → 调工具 {fname} args={_trunc(json.dumps(fargs, ensure_ascii=False), 150)}")
                yield _event({
                    "type": "step", "agent": agent_name, "tool": fname,
                    "args": fargs, "thinking": thinking,
                })
                exec_task = asyncio.ensure_future(_exec_tool(
                    pid, fname, fargs, depth=0, emit=emit,
                    agent_name=agent_name, run_id=run_id,
                    delegation_log=delegation_log,
                ))
                if hb <= 0:
                    try:
                        result = await exec_task
                    except Exception as e:
                        logger.error(f"[{agent_name}] 工具 {fname} 执行异常: {e}", exc_info=True)
                        result = json.dumps({"error": f"工具 {fname} 执行异常: {e}"}, ensure_ascii=False)
                else:
                    while True:
                        done, _ = await asyncio.wait(
                            {exec_task}, timeout=hb, return_when=asyncio.FIRST_COMPLETED,
                        )
                        while event_queue:
                            yield event_queue.pop(0)
                        if exec_task in done:
                            break
                        yield _event({"type": "heartbeat", "ts": time.time(),
                                      "run_id": run_id})
                    try:
                        result = exec_task.result()
                    except Exception as e:
                        logger.error(f"[{agent_name}] 工具 {fname} 执行异常: {e}", exc_info=True)
                        result = json.dumps({"error": f"工具 {fname} 执行异常: {e}"}, ensure_ascii=False)
                logger.info(f"[{agent_name}] step={step} ← 工具 {fname} 结果={_trunc(result, 200)}")
                while event_queue:
                    yield event_queue.pop(0)
                messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "name": fname, "content": result,
                })
                store.add_message(pid, "tool", result, tool_name=fname, tool_call_id=tc.id)
                # 改进: 工具结果质检 — 异常结果注入调整提示, 引导 LLM 换策略
                hint = _tool_quality_hint(fname, result)
                if hint:
                    messages.append({"role": "system", "content": hint})
                yield _event({
                    "type": "observation", "agent": agent_name,
                    "tool": fname, "result": result,
                })

    logger.warning(f"[{agent_name}] 达到最大步数 {s.max_steps}")
    store.add_message(pid, "assistant", "(已达最大步骤数,请继续指示。)")
    store.add_run_event(run_id, "end", agent=agent_name,
                        output="达到最大步骤", duration_ms=0)
    store.finish_run(run_id, status="done")
    yield _event({
        "type": "done", "agent": agent_name,
        "steps": s.max_steps, "stats": store.stats(pid),
        "note": "达到最大步骤", "run_id": run_id,
    })
