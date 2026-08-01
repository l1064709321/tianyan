"""跨会话长期经验库 (改进: 原版 memory.py 只存项目内记忆, 换项目/换会话就失忆)

与 memory.py 的区别:
- memory.py: 项目级 (一个项目一本账), 记剧情决策/伏笔
- experience.py: 全局级 (跨所有项目), 记用户偏好/反馈/错误教训

经验类型:
- preference: 用户表达的创作偏好 (如"我喜欢快节奏""不要第一人称")
- feedback: 用户对生成结果的反馈 (如"这段太啰嗦""角色语气不对")
- lesson: 错误教训 (如"上次写仙侠被批评世界观单薄, 下次注意")

沉淀来源:
- 用户消息中的偏好/反馈关键词
- 用户主动否定 agent 结果时
- 续写后用户要求重写时

注入: 每次构建 agent 上下文时, 把相关经验注入 system prompt,
让 agent "记住" 跨会话学到的东西, 像真实习生长成。
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from .config import get_settings


def _experience_dir() -> str:
    """全局经验库目录 (跨项目共享)"""
    s = get_settings()
    d = os.path.join(s.data_dir, "experience")
    os.makedirs(d, exist_ok=True)
    return d


def _experience_file() -> str:
    """经验库主文件 (JSON Lines, 每行一条经验)"""
    return os.path.join(_experience_dir(), "experiences.jsonl")


def add_experience(
    kind: str,
    content: str,
    *,
    pid: str = "",
    context: str = "",
    confidence: float = 0.7,
) -> None:
    """追加一条经验到全局经验库。

    kind: preference(偏好) / feedback(反馈) / lesson(教训)
    content: 经验内容 (一句话)
    pid: 关联项目 (可空, 全局经验)
    context: 触发上下文 (可空, 帮助后续检索)
    confidence: 置信度 0-1, 用户明确说的=1.0, 推断的=0.5
    """
    if not content or not content.strip():
        return
    kind = kind if kind in ("preference", "feedback", "lesson") else "feedback"
    entry = {
        "kind": kind,
        "content": content.strip(),
        "pid": pid,
        "context": (context or "")[:200],
        "confidence": round(confidence, 2),
        "ts": time.time(),
    }
    path = _experience_file()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def list_experiences(kind: str = "", pid: str = "") -> list[dict]:
    """列出经验, 可按类型/项目过滤。"""
    path = _experience_file()
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if kind and e.get("kind") != kind:
                continue
            # pid 过滤: 取该项目 + 全局经验(pid 为空)
            if pid and e.get("pid") and e.get("pid") != pid:
                continue
            out.append(e)
    return out


def _score_exp(exp: dict, query: str) -> float:
    """经验与 query 的相关度评分 (关键词 + 置信度)。"""
    text = (exp.get("content", "") + " " + exp.get("context", "")).lower()
    q_tokens = [w for w in query.lower().split() if len(w) > 1]
    if not q_tokens:
        return 0.0
    hit = sum(1 for t in q_tokens if t in text)
    return hit * exp.get("confidence", 0.5)


def search_experiences(query: str, *, pid: str = "", top_k: int = 5) -> list[dict]:
    """按相关性检索经验。"""
    exps = list_experiences(pid=pid)
    if not exps:
        return []
    scored = [(_score_exp(e, query), e) for e in exps]
    scored = [(s, e) for s, e in scored if s > 0]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:top_k]]


def get_experience_context(pid: str = "", query: str = "") -> str:
    """获取经验上下文 (注入到 agent system prompt)。

    返回结构化文本, 让 agent 知道"从过去学到了什么"。
    """
    # 无 query 时取最近的经验摘要; 有 query 时按相关性检索
    if query:
        exps = search_experiences(query, pid=pid, top_k=5)
    else:
        exps = list_experiences(pid=pid)[-8:]  # 最近 8 条
    if not exps:
        return ""

    by_kind: dict[str, list[dict]] = {}
    for e in exps:
        by_kind.setdefault(e.get("kind", "feedback"), []).append(e)

    label = {
        "preference": "用户偏好 (跨会话积累, 务必遵循)",
        "feedback": "用户历史反馈 (避免重蹈覆辙)",
        "lesson": "经验教训 (从过往失误中学到)",
    }
    lines = ["# 跨会话经验 (从过往交互中学到, 非本次项目设定)"]
    for kind, lst in by_kind.items():
        lines.append(f"\n## {label.get(kind, kind)}")
        for e in lst[-5:]:  # 每类最多 5 条
            lines.append(f"- {e.get('content', '')}")
    return "\n".join(lines)


# ===== 自动提取 =====
async def maybe_extract_from_user_message(
    pid: str, user_input: str
) -> None:
    """从用户消息中轻量提取偏好/反馈, 不调 LLM (纯关键词启发式, 零成本)。

    只提取明确信号, 避免误判。LLM 提取走 extract_and_save_memory。
    """
    text = user_input.strip()
    if not text or len(text) < 6:
        return

    # 偏好信号: "我喜欢/我希望/不要/避免/多用..."
    pref_patterns = [
        ("我喜欢", "preference", 1.0),
        ("我希望", "preference", 0.9),
        ("我偏好", "preference", 1.0),
        ("不要", "preference", 0.85),
        ("避免", "preference", 0.85),
        ("多用", "preference", 0.85),
        ("少用", "preference", 0.85),
        ("别再", "feedback", 0.9),
        ("太啰嗦", "feedback", 0.9),
        ("太水", "feedback", 0.9),
        ("不像", "feedback", 0.8),
        ("重写", "feedback", 0.7),
    ]
    for kw, kind, conf in pref_patterns:
        if kw in text:
            # 截取关键词后 40 字作为经验内容
            idx = text.find(kw)
            snippet = text[idx: idx + 40].replace("\n", " ").strip()
            add_experience(kind, snippet, pid=pid, context=text[:100], confidence=conf)
            break  # 一条消息只提取一条, 避免重复
