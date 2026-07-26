"""项目级长期记忆模块 (改进: 原版每次会话从零开始, 无跨会话记忆)

功能:
- 项目级 MEMORY.md: 存储角色决策/伏笔/剧情走向等关键信息
- 会话摘要: 每次对话结束后自动提取关键决策写入记忆
- 记忆检索: 按关键词检索相关记忆片段

灵感来源: OpenClaw 的 MEMORY.md + 每日笔记架构
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from .config import get_settings


def _memory_dir(pid: str) -> str:
    """获取项目的记忆目录"""
    s = get_settings()
    d = os.path.join(s.data_dir, "projects", pid, "memory")
    os.makedirs(d, exist_ok=True)
    return d


def _memory_file(pid: str) -> str:
    """项目级 MEMORY.md 路径"""
    return os.path.join(_memory_dir(pid), "MEMORY.md")


def _daily_file(pid: str) -> str:
    """每日记忆文件路径 (memory/YYYY-MM-DD.md)"""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(_memory_dir(pid), f"{today}.md")


def get_memory(pid: str) -> str:
    """读取项目长期记忆"""
    path = _memory_file(pid)
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def append_memory(pid: str, content: str, category: str = "general") -> None:
    """追加内容到项目长期记忆"""
    path = _memory_file(pid)
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    entry = f"\n\n## [{category}] {now}\n{content}\n"
    
    if os.path.exists(path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)
    else:
        header = f"# 项目记忆\n\n> 自动生成的项目记忆文件，记录关键决策、角色设定、剧情走向等。\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(header + entry)


def append_daily(pid: str, content: str) -> None:
    """追加到每日记忆文件"""
    path = _daily_file(pid)
    from datetime import datetime
    now = datetime.now().strftime("%H:%M")
    
    entry = f"\n### {now}\n{content}\n"
    
    if os.path.exists(path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)
    else:
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        header = f"# {today} 创作日志\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(header + entry)


def search_memory(pid: str, query: str, max_results: int = 5) -> list[dict]:
    """按关键词检索记忆 (轻量级，无向量依赖)"""
    results = []
    memory_dir = _memory_dir(pid)
    
    if not os.path.exists(memory_dir):
        return results
    
    query_lower = query.lower()
    query_tokens = [t for t in query_lower.split() if len(t) > 1]
    
    for fname in os.listdir(memory_dir):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(memory_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue
        
        content_lower = content.lower()
        # 简单关键词匹配
        score = 0
        for token in query_tokens:
            count = content_lower.count(token)
            if count > 0:
                score += count
        
        if score > 0:
            # 提取匹配的段落
            paragraphs = content.split("\n\n")
            relevant = []
            for para in paragraphs:
                para_lower = para.lower()
                if any(token in para_lower for token in query_tokens):
                    relevant.append(para.strip())
            
            results.append({
                "file": fname,
                "score": score,
                "snippets": relevant[:3],  # 最多 3 个片段
            })
    
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:max_results]


def get_memory_context(pid: str, query: str = "") -> str:
    """获取记忆上下文 (注入到 agent 的 system prompt)"""
    # 读取长期记忆
    memory = get_memory(pid)
    if not memory and not query:
        return ""
    
    parts = []
    
    if memory:
        # 只取最近的记忆 (避免太长)
        lines = memory.split("\n")
        if len(lines) > 50:
            memory = "\n".join(lines[-50:])
        parts.append(f"# 项目记忆\n{memory}")
    
    if query:
        # 检索相关记忆
        results = search_memory(pid, query)
        if results:
            snippets = []
            for r in results:
                for s in r["snippets"]:
                    if len(s) > 200:
                        s = s[:200] + "..."
                    snippets.append(s)
            if snippets:
                parts.append(f"# 相关记忆片段\n" + "\n---\n".join(snippets[:5]))
    
    return "\n\n".join(parts)


async def extract_and_save_memory(pid: str, user_input: str, assistant_response: str) -> None:
    """从对话中提取关键信息并保存到记忆 (改进: 原版对话结束就丢弃)"""
    from .llm import chat
    
    system = (
        "你是记忆提取助手。从对话中提取值得长期记住的关键信息。"
        "只提取以下类型的信息:\n"
        "1. 角色设定/性格/关系变化\n"
        "2. 剧情走向/伏笔/转折点\n"
        "3. 用户的创作偏好/风格要求\n"
        "4. 重要的创作决策\n"
        "不要提取过程性信息(如工具调用细节)。"
        "如果对话中没有值得记住的信息，返回空字符串。"
        "直接输出提取的内容，不要任何前缀。"
    )
    
    user = (
        f"用户输入:\n{user_input[:500]}\n\n"
        f"助手回复:\n{assistant_response[:500]}\n\n"
        "请提取关键信息:"
    )
    
    try:
        resp = await chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            get_settings().default_model,
            temperature=0.3,
            max_tokens=500,
        )
        content = resp["content"].strip()
        if content and len(content) > 10:
            append_memory(pid, content, category="对话提取")
            append_daily(pid, f"**用户**: {user_input[:100]}...\n**提取**: {content}")
    except Exception:
        pass  # 记忆提取失败不影响主流程
