"""市场知识库 (改进: 原版 scan_bestseller 抓完用完就丢, 下次同题材又从零抓)

功能:
- 扫榜结果结构化沉淀 (按题材标签 + 时间戳)
- 同题材复用: 下次扫同题材时, 先查知识库, 命中且未过期则直接复用
- 知识老化: 超过 TTL 的记录标记 stale, 仍可参考但提示需重新抓取

存储: data/market_knowledge/<genre>.jsonl (每题材一个文件, JSON Lines)
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from .config import get_settings

# 知识有效期 (秒): 7 天内的扫榜结果可直接复用, 超过则标记需重新抓
KNOWLEDGE_TTL = 7 * 24 * 3600


def _kb_dir() -> str:
    s = get_settings()
    d = os.path.join(s.data_dir, "market_knowledge")
    os.makedirs(d, exist_ok=True)
    return d


def _genre_file(genre: str) -> str:
    """题材归一化为文件名 (去空格/斜杠, 小写)。"""
    safe = "".join(c for c in genre if c.isalnum() or c in "-_") or "general"
    return os.path.join(_kb_dir(), f"{safe.lower()}.jsonl")


def save_scan_result(
    genre: str,
    data: dict,
    *,
    really_online: bool = True,
    sources_count: int = 0,
) -> None:
    """把一次扫榜结果沉淀到知识库。

    data: scan_bestseller 返回的结构化结果 (热门题材/画像/趋势等)
    really_online: 是否真实联网抓取 (False=LLM 凭印象, 置信度低)
    """
    if not data or not genre:
        return
    # 只存可复用的核心字段, 不存抓取元信息
    reusable = {
        "genre": genre,
        "hot_topics": data.get("hot_topics", [])[:10],
        "reader_profile": data.get("reader_profile", ""),
        "trends": data.get("trends", []),
        "recommendations": data.get("recommendations", [])[:8],
        "really_online": really_online,
        "sources_count": sources_count,
        "ts": time.time(),
        "confidence": 0.9 if really_online else 0.4,
    }
    path = _genre_file(genre)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(reusable, ensure_ascii=False) + "\n")


def load_latest(genre: str, *, max_age: int = KNOWLEDGE_TTL) -> Optional[dict]:
    """加载某题材最新的扫榜知识。

    返回最新一条; 若超过 max_age 则标记 stale=True (仍返回, 但提示需更新)。
    无记录返回 None。
    """
    path = _genre_file(genre)
    if not os.path.exists(path):
        return None
    last = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except Exception:
                continue
    if last is None:
        return None
    age = time.time() - last.get("ts", 0)
    last["age_days"] = round(age / 86400, 1)
    last["stale"] = age > max_age
    return last


def list_genres() -> list[dict]:
    """列出知识库已有的题材 + 最新记录时间。"""
    d = _kb_dir()
    out = []
    if not os.path.exists(d):
        return out
    for fname in os.listdir(d):
        if not fname.endswith(".jsonl"):
            continue
        path = os.path.join(d, fname)
        genre = fname[:-6]
        last_ts = 0
        count = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    last_ts = max(last_ts, e.get("ts", 0))
                    count += 1
                except Exception:
                    continue
        out.append({
            "genre": genre,
            "records": count,
            "last_scan": last_ts,
            "last_scan_str": time.strftime("%Y-%m-%d %H:%M", time.localtime(last_ts)) if last_ts else "",
        })
    return out


def get_knowledge_context(genre: str = "") -> str:
    """获取市场知识上下文 (注入 agent system prompt)。

    有同题材知识 → 注入并标注时效性
    无知识 → 返回空 (不干扰)
    """
    if not genre:
        return ""
    latest = load_latest(genre)
    if not latest:
        return ""

    stale_tag = " (⚠ 数据已过期, 建议重新扫榜)" if latest.get("stale") else ""
    online_tag = "真实联网" if latest.get("really_online") else "LLM印象(置信度低)"
    lines = [
        f"# 市场知识库·{genre}{stale_tag}",
        f"(数据来源: {online_tag}, 采集于 {latest.get('age_days', 0)} 天前)",
    ]
    if latest.get("hot_topics"):
        lines.append("热门题材: " + ", ".join(latest["hot_topics"][:6]))
    if latest.get("reader_profile"):
        lines.append(f"读者画像: {latest['reader_profile']}")
    if latest.get("trends"):
        lines.append("趋势: " + "; ".join(latest["trends"][:3]))
    return "\n".join(lines)
