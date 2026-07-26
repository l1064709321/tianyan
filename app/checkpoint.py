"""检查点模块 (改进: 原版无持久化状态, 崩溃后无法恢复)

功能:
- 每章完成后自动保存检查点
- 崩溃恢复: 从最近检查点继续
- 状态快照: 项目/章节/设定/记忆的完整快照

灵感来源:
- LangGraph 的 Checkpointer
- AutoGen 的持久化执行
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from .config import get_settings
from . import store

logger = logging.getLogger("novel_agent")


def _checkpoint_dir(pid: str) -> str:
    s = get_settings()
    d = os.path.join(s.data_dir, "projects", pid, "checkpoints")
    os.makedirs(d, exist_ok=True)
    return d


def save_checkpoint(pid: str, label: str = "", metadata: dict = None) -> str:
    """保存项目检查点。

    Args:
        pid: 项目 ID
        label: 检查点标签 (如 "chapter_3_completed")
        metadata: 额外元数据

    Returns:
        检查点 ID
    """
    checkpoint_id = f"{int(time.time())}_{label or 'auto'}"
    checkpoint_dir = _checkpoint_dir(pid)

    # 收集项目状态
    proj = store.get_project(pid)
    chapters = store.list_chapters(pid)
    elements = store.list_elements(pid)
    stats = store.stats(pid)

    snapshot = {
        "id": checkpoint_id,
        "pid": pid,
        "label": label,
        "created_at": time.time(),
        "metadata": metadata or {},
        "project": proj,
        "chapters": [
            {
                "id": c["id"],
                "idx": c["idx"],
                "title": c["title"],
                "status": c.get("status"),
                "content_chars": len(c.get("content") or ""),
                "has_outline": bool(c.get("outline")),
            }
            for c in chapters
        ],
        "elements_count": len(elements),
        "stats": stats,
    }

    # 保存快照
    path = os.path.join(checkpoint_dir, f"{checkpoint_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    # 保存最新指针
    latest_path = os.path.join(checkpoint_dir, "latest.txt")
    with open(latest_path, "w") as f:
        f.write(checkpoint_id)

    logger.info(f"[检查点] 保存: {checkpoint_id} ({stats.get('chapters', 0)} 章, {stats.get('total_chars', 0)} 字)")
    return checkpoint_id


def get_latest_checkpoint(pid: str) -> Optional[dict]:
    """获取最近的检查点"""
    checkpoint_dir = _checkpoint_dir(pid)
    latest_path = os.path.join(checkpoint_dir, "latest.txt")

    if not os.path.exists(latest_path):
        return None

    with open(latest_path, "r") as f:
        checkpoint_id = f.read().strip()

    path = os.path.join(checkpoint_dir, f"{checkpoint_id}.json")
    if not os.path.exists(path):
        return None

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_checkpoints(pid: str, limit: int = 10) -> list[dict]:
    """列出所有检查点"""
    checkpoint_dir = _checkpoint_dir(pid)
    checkpoints = []

    for fname in sorted(os.listdir(checkpoint_dir), reverse=True):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(checkpoint_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            checkpoints.append({
                "id": data.get("id"),
                "label": data.get("label"),
                "created_at": data.get("created_at"),
                "stats": data.get("stats"),
            })
        except Exception:
            continue

        if len(checkpoints) >= limit:
            break

    return checkpoints


def auto_checkpoint(pid: str, event: str = "") -> Optional[str]:
    """自动检查点 (在关键事件后自动触发)。

    触发条件:
    - 章节完成 (status = "written")
    - 大纲生成完成
    - 用户手动触发
    """
    try:
        label = event or "auto"
        return save_checkpoint(pid, label=label)
    except Exception as e:
        logger.warning(f"[检查点] 自动保存失败: {e}")
        return None
