"""技能市场 (Skill Market)

聚合 14 个内置技能元信息 + 用户自定义技能 + 启用状态持久化。

数据来源:
- 内置技能: 静态常量 BUILTIN_SKILLS (12 个工具,来自 skill_adapter + skill_library)
- 自定义技能: ~/.tianyan/skills_custom.json (用户在前端添加的 prompt 模板)
- 启用状态: ~/.tianyan/skills_enabled.json (开关持久化)

API:
- list_skills(): 返回所有技能 (内置 + 自定义) + 启用状态 + 调用次数
- toggle_skill(name): 切换启用/禁用
- add_custom_skill(name, description, prompt): 添加自定义技能
- remove_custom_skill(name): 删除自定义技能
- is_enabled(name): 运行时查询 (agents.py 用此过滤 AGENT_TOOLS)
- get_custom_skill_prompts(): 返回所有已启用自定义技能的 prompt 文本 (供 agent system prompt 注入)

集成自: https://github.com/l1064709321/Online-writing-skill
"""
from __future__ import annotations

import json
import os
from typing import Optional

# 持久化路径 (~/.tianyan/)
_USER_DIR = os.path.expanduser("~/.tianyan")
_CUSTOM_PATH = os.path.join(_USER_DIR, "skills_custom.json")
_ENABLED_PATH = os.path.join(_USER_DIR, "skills_enabled.json")
_USAGE_PATH = os.path.join(_USER_DIR, "skills_usage.json")


# ---------------- 内置技能定义 ----------------
# category: deconstruction(拆解) / audit(审计) / write(写作) / corpus(语料)
# agents: 哪些 agent 在 AGENT_TOOLS 白名单里有这个工具
BUILTIN_SKILLS: list[dict] = [
    # ---- 语料库类 (skill_library) ----
    {
        "name": "list_authors",
        "label": "作家列表",
        "description": "列出语料库中 110+ 位白金作家的全部名单,供主笔/架构师选参考",
        "category": "corpus",
        "source": "skill_library",
        "agents": ["orchestrator", "story-architect", "narrative-writer"],
        "icon": "📚",
    },
    {
        "name": "match_author",
        "label": "题材→作家匹配",
        "description": "根据题材/文风/设定自动匹配 1-3 位最合适的作家 (附方法论摘要)",
        "category": "corpus",
        "source": "skill_library",
        "agents": ["orchestrator", "story-architect", "narrative-writer"],
        "icon": "🎯",
    },
    {
        "name": "get_author_reference",
        "label": "取作家原文 few-shot",
        "description": "按场景标签取某作家精选原文段落,做 few-shot 参考塞进 prompt",
        "category": "corpus",
        "source": "skill_library",
        "agents": ["orchestrator", "story-architect", "narrative-writer"],
        "icon": "📖",
    },
    # ---- 拆解类 (skill_adapter) ----
    {
        "name": "deconstruct",
        "label": "拆书解构",
        "description": "拆解对标书/作者风格,提取钩子/节奏/人设/文风等可复用模块",
        "category": "deconstruction",
        "source": "skill_adapter",
        "agents": ["orchestrator", "story-architect"],
        "icon": "🔧",
    },
    {
        "name": "analyze_style",
        "label": "文风分析",
        "description": "分析给定文本的文风特征 (句式/节奏/词汇/信息密度),可对标指定作家",
        "category": "deconstruction",
        "source": "skill_adapter",
        "agents": ["narrative-writer"],
        "icon": "🔬",
    },
    {
        "name": "imitate_style",
        "label": "文风仿写",
        "description": "基于给定文本的文风分析,生成同风格的新内容 (指定话题+字数)",
        "category": "deconstruction",
        "source": "skill_adapter",
        "agents": ["narrative-writer"],
        "icon": "🎭",
    },
    {
        "name": "diagnose_stuck",
        "label": "卡文诊断",
        "description": "诊断卡文原因 (情节断裂/人物崩/动力不足等),给续写方向建议",
        "category": "deconstruction",
        "source": "skill_adapter",
        "agents": ["narrative-writer"],
        "icon": "🚑",
    },
    # ---- 审计类 (skill_adapter) ----
    {
        "name": "audit_novel",
        "label": "33 维审计",
        "description": "从情节/人设/世界观/逻辑/文笔等 33 个维度审计正文,出问题清单",
        "category": "audit",
        "source": "skill_adapter",
        "agents": ["consistency-checker"],
        "icon": "🔍",
    },
    {
        "name": "detect_ai",
        "label": "AI 味检测",
        "description": "检测文本中的 AI 味 (套话/水词/逻辑跳跃),给清洗建议",
        "category": "audit",
        "source": "skill_adapter",
        "agents": ["consistency-checker"],
        "icon": "🤖",
    },
    {
        "name": "diagnose_opening",
        "label": "黄金三章诊断",
        "description": "诊断前 3 章是否抓住读者 (钩子/张力/代入感),给开篇优化建议",
        "category": "audit",
        "source": "skill_adapter",
        "agents": ["consistency-checker"],
        "icon": "⚡",
    },
    {
        "name": "full_audit",
        "label": "完整审计",
        "description": "33 维 + AI 味 + 黄金三章一次过,定稿前必跑",
        "category": "audit",
        "source": "skill_adapter",
        "agents": ["orchestrator", "consistency-checker"],
        "icon": "✅",
    },
    # ---- 写作类 (skill_adapter) ----
    {
        "name": "ghostwrite",
        "label": "枪手代笔",
        "description": "基于大纲 + 风格参考生成正文 (内核出 prompt + LLM 生成),narrative-writer 默认走这里",
        "category": "write",
        "source": "skill_adapter",
        "agents": ["orchestrator", "narrative-writer"],
        "icon": "✍️",
    },
]

# 内置技能名集合 (用于 toggle 时区分内置/自定义)
_BUILTIN_NAMES = {s["name"] for s in BUILTIN_SKILLS}


# ---------------- 持久化层 ----------------
def _ensure_user_dir() -> None:
    os.makedirs(_USER_DIR, exist_ok=True)


def _load_json(path: str, default) -> object:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data) -> None:
    _ensure_user_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 自定义技能 (用户在前端添加)
def _load_custom() -> list[dict]:
    data = _load_json(_CUSTOM_PATH, [])
    if not isinstance(data, list):
        return []
    return data


def _save_custom(skills: list[dict]) -> None:
    _save_json(_CUSTOM_PATH, skills)


# 启用状态 (name -> bool)
def _load_enabled() -> dict:
    data = _load_json(_ENABLED_PATH, {})
    if not isinstance(data, dict):
        return {}
    return data


def _save_enabled(enabled: dict) -> None:
    _save_json(_ENABLED_PATH, enabled)


# 调用次数 (name -> int)
def _load_usage() -> dict:
    data = _load_json(_USAGE_PATH, {})
    if not isinstance(data, dict):
        return {}
    return data


def _save_usage(usage: dict) -> None:
    _save_json(_USAGE_PATH, usage)


# ---------------- 对外 API ----------------
def list_skills() -> list[dict]:
    """列出所有技能 (内置 + 自定义) + 启用状态 + 调用次数。"""
    enabled = _load_enabled()
    usage = _load_usage()
    custom = _load_custom()
    result = []
    for s in BUILTIN_SKILLS:
        name = s["name"]
        result.append({
            **s,
            "kind": "builtin",
            "enabled": enabled.get(name, True),  # 内置默认启用
            "usage": usage.get(name, 0),
        })
    for s in custom:
        name = s.get("name", "")
        result.append({
            "name": name,
            "label": s.get("label", name),
            "description": s.get("description", ""),
            "category": "custom",
            "source": "user",
            "agents": s.get("agents", ["orchestrator", "narrative-writer"]),
            "icon": s.get("icon", "⭐"),
            "prompt": s.get("prompt", ""),
            "kind": "custom",
            "enabled": enabled.get(name, True),
            "usage": usage.get(name, 0),
        })
    return result


def is_enabled(name: str) -> bool:
    """运行时查询某技能是否启用 (agents.py 过滤 AGENT_TOOLS 用)。"""
    enabled = _load_enabled()
    return enabled.get(name, True)  # 默认启用


def toggle_skill(name: str) -> dict:
    """切换启用/禁用,返回新状态。"""
    enabled = _load_enabled()
    cur = enabled.get(name, True)
    enabled[name] = not cur
    _save_enabled(enabled)
    return {"name": name, "enabled": enabled[name]}


def add_custom_skill(name: str, label: str, description: str,
                     prompt: str, agents: Optional[list[str]] = None,
                     icon: str = "⭐") -> dict:
    """添加自定义技能。name 不能与内置重名。"""
    name = name.strip()
    if not name:
        return {"error": "技能名不能为空"}
    if name in _BUILTIN_NAMES:
        return {"error": f"名称 {name} 与内置技能冲突"}
    skills = _load_custom()
    if any(s.get("name") == name for s in skills):
        return {"error": f"自定义技能 {name} 已存在"}
    skill = {
        "name": name,
        "label": label.strip() or name,
        "description": description.strip(),
        "prompt": prompt,
        "agents": agents or ["orchestrator", "narrative-writer"],
        "icon": icon,
    }
    skills.append(skill)
    _save_custom(skills)
    # 默认启用
    enabled = _load_enabled()
    enabled[name] = True
    _save_enabled(enabled)
    return {"ok": True, "skill": skill}


def remove_custom_skill(name: str) -> dict:
    """删除自定义技能 (内置技能不可删)。"""
    if name in _BUILTIN_NAMES:
        return {"error": "内置技能不可删除,只能禁用"}
    skills = _load_custom()
    new_skills = [s for s in skills if s.get("name") != name]
    if len(new_skills) == len(skills):
        return {"error": f"未找到自定义技能 {name}"}
    _save_custom(new_skills)
    # 清理启用状态
    enabled = _load_enabled()
    enabled.pop(name, None)
    _save_enabled(enabled)
    return {"ok": True}


def get_custom_skill_prompts(agents: list[str]) -> str:
    """返回所有已启用自定义技能中,适用于指定 agent 列表的 prompt 文本。
    供 agent system prompt 注入 (agent.py 调用)。
    """
    enabled = _load_enabled()
    custom = _load_custom()
    parts = []
    for s in custom:
        name = s.get("name", "")
        if not enabled.get(name, True):
            continue
        # 检查这个技能是否适用于这些 agent
        skill_agents = set(s.get("agents", ["orchestrator", "narrative-writer"]))
        if not skill_agents.intersection(agents):
            continue
        prompt = s.get("prompt", "").strip()
        if not prompt:
            continue
        label = s.get("label", name)
        parts.append(f"## 用户自定义技能: {label}\n{prompt}")
    if not parts:
        return ""
    return "\n\n---\n\n".join(parts)


def get_enabled_tools_for_agent(agent_name: str, all_tools: list[str]) -> list[str]:
    """过滤出某 agent 启用状态的技能工具列表。
    agents.py 运行时用此过滤 AGENT_TOOLS。
    """
    enabled = _load_enabled()
    # 内置技能 + 自定义技能名都查一遍
    result = [t for t in all_tools if enabled.get(t, True)]
    return result


def increment_usage(name: str) -> None:
    """记录某技能被调用 (tools.dispatch 调用)。"""
    usage = _load_usage()
    usage[name] = usage.get(name, 0) + 1
    _save_usage(usage)


def skill_market_status() -> dict:
    """供 /api/skills/status 端点用。"""
    enabled = _load_enabled()
    custom = _load_custom()
    usage = _load_usage()
    builtin_enabled = sum(1 for s in BUILTIN_SKILLS if enabled.get(s["name"], True))
    builtin_disabled = len(BUILTIN_SKILLS) - builtin_enabled
    custom_enabled = sum(1 for s in custom if enabled.get(s.get("name", ""), True))
    return {
        "builtin_total": len(BUILTIN_SKILLS),
        "builtin_enabled": builtin_enabled,
        "builtin_disabled": builtin_disabled,
        "custom_total": len(custom),
        "custom_enabled": custom_enabled,
        "total_calls": sum(usage.values()),
        "user_dir": _USER_DIR,
    }
