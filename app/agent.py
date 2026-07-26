"""多 agent 协同的 Codex 式 loop (整合 oh-story 7-agent 架构)。

架构:
- 主 agent (默认 orchestrator 总编) 接收用户输入,通过 delegate_to_agent 工具委派给专家
- 6 位专家 agent (story-architect/narrative-writer/character-designer/
  consistency-checker/story-explorer/worldbuilder) 各司其职,有独立系统提示词与工具子集
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
import sys
import tempfile
import time
from typing import AsyncIterator, Optional

from . import agents, store, tools
from .config import get_settings
from .llm import chat


# ===== 改进:LLM 调用重试机制 =====
# 原版: LLM 调用失败直接报错 → 网络抖动/API 限流就挂
# 改进: 指数退避重试,临时错误自动恢复
async def _chat_with_retry(
    messages: list[dict],
    cfg,
    *,
    tools: list | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format: dict | None = None,
    assistant_prefill: str | None = None,
    stop: list[str] | None = None,
    max_retries: int = 3,
    base_delay: float = 2.0,
) -> dict:
    """带重试的 LLM 调用。指数退避: 2s → 4s → 8s。

    可重试错误: 超时/429限流/5xx服务端错误/连接错误
    不可重试: 401认证失败/400参数错误 (直接抛出)
    """
    last_err = None
    for attempt in range(max_retries):
        try:
            return await chat(
                messages, cfg,
                tools=tools, temperature=temperature, max_tokens=max_tokens,
                response_format=response_format, assistant_prefill=assistant_prefill,
                stop=stop,
            )
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            # 不可重试的错误: 认证失败/参数错误/模型不存在
            if any(kw in err_str for kw in ("401", "403", "invalid api key", "missing credentials", "model_not_found")):
                raise
            # 可重试: 超时/429/5xx/连接错误
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"[重试 {attempt+1}/{max_retries}] LLM 调用失败: {e}, {delay}s 后重试")
                await asyncio.sleep(delay)
            else:
                logger.error(f"[重试耗尽] LLM 调用 {max_retries} 次均失败: {e}")
    raise last_err  # type: ignore


# ---------- 实时日志 ----------
# 配置: 同时输出到 stderr (uvicorn 控制台) 和系统临时目录
_AGENT_LOG = os.environ.get(
    "NA_AGENT_LOG",
    os.path.join(tempfile.gettempdir(), "novel-agent-agent.log"),
)
logger = logging.getLogger("novel_agent")
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


# ===== 上下文窗口管理 (改进:摘要压缩替代粗暴截断) =====
# 原版: 超长直接丢弃旧消息 → 丢失重要上下文(角色设定/伏笔/剧情决策)
# 改进: 超长时把旧消息压缩成摘要,保留关键信息,再拼接最近消息
MAX_CONTEXT_CHARS = 14000  # 历史对话字符上限(超出触发压缩)
KEEP_LAST_TURNS = 8        # 至少保留最近这么多回合


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
    """
    sys_msgs = [m for m in msgs if m.get("role") == "system"]
    rest = [m for m in msgs if m.get("role") != "system"]
    if len(rest) <= 2:
        return msgs
    total = sum(len(str(m.get("content") or "")) for m in rest)
    if total <= MAX_CONTEXT_CHARS:
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
    return sys_msgs + note + rest[cut:]


def _build_messages(pid: str, agent_name: str = agents.DEFAULT_AGENT) -> list[dict]:
    """组装对话历史 + 项目背景 + 指定 agent 的系统提示词。

    改进: 注入项目记忆 + 动态计划上下文
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
                f"类型: {proj.get('genre','')}\n文风: {proj.get('style','')}\n"
                f"核心设定: {proj.get('premise','')}"
            ),
        })

    # 改进: 注入项目长期记忆
    memory_ctx = get_memory_context(pid)
    if memory_ctx:
        msgs.append({"role": "system", "content": memory_ctx})

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
    """从 litellm completion response 提取 token 使用 + 成本。
    litellm 在 resp.usage 给 prompt_tokens/completion_tokens/total_cost (若 _fer.alogo_cost 启用)。
    """
    if resp is None:
        return None, None
    u = getattr(resp, "usage", None)
    if u is None:
        return None, None
    tok = (getattr(u, "prompt_tokens", 0) or 0) + (getattr(u, "completion_tokens", 0) or 0)
    # litellm 在响应上塞 _response_cost (USD),部分版本在 usage 上
    cost = getattr(resp, "_response_cost", None) or getattr(u, "cost", None) or 0
    try:
        cost = float(cost)
    except Exception:
        cost = 0
    return tok, cost


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


# ---------- 工具调用与 trace 落盘 ----------
async def _exec_tool(
    pid: str, fname: str, fargs: dict, *,
    depth: int, emit, agent_name: str = agents.DEFAULT_AGENT,
    run_id: Optional[str] = None,
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
        if run_id:
            store.add_run_event(run_id, "delegate_done", agent=target,
                                output=_truncate_for_trace(result, 800), duration_ms=dur_ms)
        return json.dumps({"agent": target, "task": task, "result": result}, ensure_ascii=False)
    # 普通工具直接走 dispatch
    if run_id:
        store.add_run_event(run_id, "tool_call", agent=agent_name, tool=fname,
                            input_=_truncate_for_trace(fargs, 800))
    t0 = time.time()
    result = await tools.dispatch(pid, fname, fargs)
    dur_ms = int((time.time() - t0) * 1000)
    logger.info(f"[{agent_name}] 工具 {fname} 耗时={dur_ms}ms 结果={_trunc(result, 200)}")
    if run_id:
        store.add_run_event(run_id, "tool_result", agent=agent_name, tool=fname,
                            output=_truncate_for_trace(result, 800), duration_ms=dur_ms)
    return result


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
    for step in range(sub_max_steps):
        t0 = time.time()
        try:
            # 默认用非 reasoning 模型 (如 agnes-1.5-flash),4096 够用。
            # 若用 reasoning 模型 (agnes-2.0-flash) 需在 ModelConfig.max_tokens 调大,
            # 但 reasoning 模型做 agent loop 工具调用决策不可靠,不推荐。
            resp = await _chat_with_retry(messages, s.default_model, tools=tool_schema)
        except Exception as e:
            logger.error(f"[{agent_name}] step={step} LLM 调用失败: {e}")
            if run_id:
                store.add_run_event(run_id, "error", agent=agent_name,
                                    error=f"step={step} {e}", duration_ms=int((time.time()-t0)*1000))
            return f"(子 agent {agent_name} 调用失败: {e})"

        tool_calls = resp["tool_calls"]
        content = resp["content"]
        reasoning = resp.get("reasoning", "") or ""
        # 思考过程 = reasoning + content (reasoning 是模型内部思考,content 是模型输出)
        thinking = (reasoning + "\n" + content).strip() if reasoning else content
        # 落盘 LLM 调用事件 (token + 成本,便于回放/统计)
        if run_id:
            tok, cost = _extract_usage(resp.get("raw"))
            store.add_run_event(run_id, "llm_call", agent=agent_name,
                                input_=_truncate_for_trace(task, 200),
                                output=_truncate_for_trace(content, 400),
                                tokens=tok, cost=cost,
                                duration_ms=int((time.time()-t0)*1000))

        if not tool_calls:
            # 终态:返回最终文本
            logger.info(f"[{agent_name}] step={step} 完成 回答长度={len(content or '')}")
            await emit({"type": "step", "agent": agent_name, "tool": "(完成)",
                        "args": {}, "thinking": thinking, "depth": depth})
            return content or ""

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
    # 新建一次 run 记录,用于 trace 回放/统计
    run_id = store.create_run(pid, user_input, agent_name)
    store.add_run_event(run_id, "start", agent=agent_name, input_=user_input)
    logger.info(f"========== 主 agent 启动 pid={pid} agent={agent_name} max_steps={s.max_steps} run_id={run_id} ==========")
    logger.info(f"[{agent_name}] 用户输入: {_trunc(user_input, 200)}")
    store.add_message(pid, "user", user_input)
    messages = _build_messages(pid, agent_name)
    tool_schema = tools.schema_for(agents.get_tools(agent_name))
    logger.info(f"[{agent_name}] 装载消息 {len(messages)} 条, 工具 {len(agents.get_tools(agent_name))} 个: {agents.get_tools(agent_name)}")

    # 事件缓冲:子 agent 委派过程中产生的事件也要吐给前端
    event_queue: list[str] = []

    async def emit(obj: dict):
        event_queue.append(_event(obj))

    yield _event({"type": "start", "agent": agent_name, "input": user_input, "run_id": run_id})

    # 工具调用循环检测: 记录 (tool_name, args_hash) → 连续调用次数
    # 连续相同调用超阈值 = LLM 卡循环了, 强制终止
    _tool_call_counter: dict[tuple, int] = {}

    for step in range(s.max_steps):
        # 先把子 agent 委派过程中累积的事件吐出去
        while event_queue:
            yield event_queue.pop(0)

        t0 = time.time()
        # 用 task 包装 LLM 调用, 等待期间定期 yield 心跳, 防前端误判断连
        hb = s.sse_heartbeat_interval
        llm_task = asyncio.ensure_future(_chat_with_retry(messages, s.default_model, tools=tool_schema))
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
        # 落盘 LLM 调用事件 (token + 成本)
        tok, cost = _extract_usage(resp.get("raw"))
        store.add_run_event(run_id, "llm_call", agent=agent_name,
                            input_=_truncate_for_trace(user_input, 200),
                            output=_truncate_for_trace(content, 400),
                            tokens=tok, cost=cost,
                            duration_ms=int((time.time()-t0)*1000))

        # ===== 风险防护: 单次 run 累计超限就终止 =====
        # 防止 LLM 失控 / agent 卡循环 / 烧钱
        run_meta = store.get_run(run_id) or {}
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

        if not tool_calls:
            logger.info(f"[{agent_name}] step={step} 完成 回答长度={len(content or '')}")
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
                ))
                if hb <= 0:
                    result = await exec_task
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
                    result = exec_task.result()
                logger.info(f"[{agent_name}] step={step} ← 工具 {fname} 结果={_trunc(result, 200)}")
                while event_queue:
                    yield event_queue.pop(0)
                messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "name": fname, "content": result,
                })
                store.add_message(pid, "tool", result, tool_name=fname, tool_call_id=tc.id)
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
