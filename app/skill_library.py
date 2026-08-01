"""小说创作技能库 (Skill): 111 位白金作家风格语料 + 方法论。

三层架构:
- 记忆层 (data/corpus/authors/*.json): 111 位作家的原文精选段落,按场景标签索引
- 能力层 (本文件 + data/corpus/loader.py): CorpusLoader 检索原文作 few-shot + methodology 方法论查询 + 作者匹配
- 调用层 (tools.py 暴露为 3 个工具): list_authors / match_author / get_author_reference

集成自: https://github.com/l1064709321/Online-writing-skill
理念: 不存统计摘要,存原文精选段落;Prompt 直接塞原文做 few-shot (原理5 Few-shot),
让模型从原文学句式节奏、信息密度、断句习惯,而不是"请用 XX 风格写"的模板废话。

注: CorpusLoader 已统一到 data/corpus/loader.py (单一来源), 本文件只复用不再重复实现。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Optional

# 把 data/ 加入 sys.path 以便 import corpus.loader
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
if _DATA_DIR not in sys.path:
    sys.path.insert(0, _DATA_DIR)

# 复用 loader.py 中的 CorpusLoader (单一来源,不再重复实现)
from corpus.loader import CorpusLoader, get_corpus_loader  # noqa: E402

# 语料库根目录 (app/data/corpus)
_CORPUS_DIR = os.path.join(_DATA_DIR, "corpus")
_AUTHORS_DIR = os.path.join(_CORPUS_DIR, "authors")
_METHODOLOGY_PATH = os.path.join(_CORPUS_DIR, "methodology.json")

# 场景标签说明 (供 LLM 理解每个 scene 参数的含义)
SCENE_TAGS = {
    "battle": "战斗/打斗",
    "dialogue": "对话/嘴炮",
    "environment": "环境/场景描写",
    "psychology": "心理/内心独白",
    "opening": "开篇/出场",
    "climax": "高潮/燃点",
    "humor": "幽默/搞笑",
    "suspense": "悬疑/惊悚",
    "emotion": "感情/细腻",
    "worldbuilding": "世界观/设定",
}


# 兼容别名: skill_library 旧版用 list_authors() 名字, loader.py 用 get_all_authors()
# 给 CorpusLoader 加一个 list_authors 方法, 转发到 get_all_authors
def _list_authors_compat(self) -> list[str]:
    return sorted(self.get_all_authors())


CorpusLoader.list_authors = _list_authors_compat  # type: ignore[attr-defined]



# ---------------- 能力层: 方法论查询 ----------------
_methodology_cache: Optional[dict] = None


def _load_methodology() -> dict:
    global _methodology_cache
    if _methodology_cache is not None:
        return _methodology_cache
    if not os.path.exists(_METHODOLOGY_PATH):
        _methodology_cache = {}
        return _methodology_cache
    with open(_METHODOLOGY_PATH, "r", encoding="utf-8") as f:
        _methodology_cache = json.load(f).get("methodology", {})
    return _methodology_cache


def get_methodology(author_name: str) -> dict:
    """获取某作家的方法论 (核心原则/关键洞察/节奏公式/技法/句式/常用词)。"""
    m = _load_methodology()
    return m.get(author_name, {})


def list_authors_with_methodology() -> list[str]:
    return sorted(_load_methodology().keys())


# ---------------- 能力层: 作者匹配 ----------------
# 题材关键词 -> 推荐作家 (基于 methodology 中的 genre/category/technique 字段)
_GENRE_MATCH = {
    "玄幻": ["辰东", "天蚕土豆", "我吃西红柿", "耳根", "梦入神机", "宅猪", "猫腻", "忘语", "烽火戏诸侯", "血红"],
    "仙侠": ["耳根", "梦入神机", "忘语", "萧潜", "还珠楼主", "梁羽生", "烽火戏诸侯"],
    "洪荒": ["梦入神机", "辰东", "我吃西红柿", "宅猪", "血红"],
    "都市": ["会说话的肘子", "卖报小郎君", "打眼", "柳岸花又明", "尝谕", "青衫取醉"],
    "悬疑": ["南派三叔", "天下霸唱", "丁墨", "蜘蛛"],
    "盗墓": ["天下霸唱", "南派三叔"],
    "历史": ["月关", "孑与2", "高月", "柯山梦"],
    "武侠": ["古龙", "梁羽生", "温瑞安", "金庸"],
    "言情": ["吱吱", "叶非夜", "丁墨", "容光"],
    "科幻": ["方想", "七十二编", "会说话的肘子"],
    "系统": ["卖报小郎君", "会说话的肘子", "最白的乌鸦"],
    "凡人修仙": ["忘语", "萧潜", "耳根"],
    "群像": ["烽火戏诸侯", "猫腻", "辰东"],
}

# 女频作家集合 (category 含 "女频" 或公认女频作者)
_FEMALE_AUTHORS = {
    "吱吱", "叶非夜", "丁墨", "容光", "天下归元", "桩桩", "桐华",
    "辛夷坞", "顾漫", "唐七公子", "匪我思存", "流潋紫", "寐语者",
    "关心则乱", "电线", "九夜茴", "饶雪漫",
}


def match_author(genre: str, style: str = "", premise: str = "", audience: str = "") -> list[dict]:
    """根据题材/文风/设定/频道匹配最合适的作家 (返回 1-3 位)。

    匹配规则:
    1. 题材关键词命中 _GENRE_MATCH 的优先返回
    2. 若指定 audience (男频/女频), 过滤掉不属于该频道的作家
    3. 返回的作家附带 methodology 摘要 (流派/核心原则/节奏公式/句式/常用词)
    4. 最多返回 3 位,避免 Prompt 过长
    """
    # 收集候选
    candidates: list[str] = []
    text = f"{genre} {style} {premise}"
    for kw, authors in _GENRE_MATCH.items():
        if kw in text:
            for a in authors:
                if a not in candidates:
                    candidates.append(a)
    # 没匹配上时,按频道给默认推荐
    if not candidates:
        if audience == "女频":
            candidates = ["吱吱", "叶非夜", "容光"]
        else:
            candidates = ["猫腻", "烽火戏诸侯", "忘语"]  # 男频通用万金油

    # 按频道过滤
    if audience == "女频":
        # 女频: 只保留女频作家
        filtered = [a for a in candidates if a in _FEMALE_AUTHORS]
        if filtered:
            candidates = filtered
        elif not any(a in _FEMALE_AUTHORS for a in candidates):
            # 题材匹配到的全是男频作家, 但用户要女频 → 补女频作家
            candidates = ["吱吱", "叶非夜", "容光"]
    elif audience == "男频":
        # 男频: 排除女频作家
        filtered = [a for a in candidates if a not in _FEMALE_AUTHORS]
        if filtered:
            candidates = filtered

    # 取前 3 位,附带方法论摘要
    result = []
    for author in candidates[:3]:
        m = get_methodology(author)
        result.append({
            "author": author,
            "genre": m.get("genre", ""),
            "core_principle": m.get("core_principle", ""),
            "rhythm_formula": m.get("rhythm_formula", ""),
            "technique": m.get("technique", ""),
            "sentence_style": m.get("sentence_style", ""),
            "common_words": m.get("common_words", []),
            "has_corpus": bool(CorpusLoader().get_passages(author, limit=1)),
        })
    return result


# ---------------- 全局单例 ----------------
# get_corpus_loader 已从 corpus.loader 导入 (上方 import),
# 不再在此处重复定义,统一使用 loader.py 的单例。
