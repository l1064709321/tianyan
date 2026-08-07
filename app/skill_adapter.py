"""技能适配层: 把 Online-writing-skill 的 NovelDeconstructionSkill 内核
暴露给 tianyan 的工具系统调用。

架构:
- 内核层 (data/skills_core.py): 5686 行原样复制的技能实现,不改一行
  - NovelDeconstructionSkill 统一入口,11 个方法覆盖全部能力
  - 含 4000+ 行 NOVEL_DECONSTRUCTION_DB 拆解数据库
  - 33 维 NovelAuditor / AIDetector / ContinuationEngine / StyleImitator
  - OpeningDiagnosis / EditorialPipeline
- 适配层 (本文件): 薄封装,把内核方法对接到 tianyan 的工具签名
  - 单例化 NovelDeconstructionSkill (避免重复加载 4000 行 DB)
  - 输入参数适配 (chapter_id -> 取章节正文 -> 传给内核)
  - 输出格式适配 (内核返回 str 报告,适配层包装成 dict 给工具系统)

集成自: https://github.com/l1064709321/Online-writing-skill
理念: 把整个仓库作为"写作技能"内核, tianyan 调用它获得专业写作能力,
而不是自己重新实现审计/拆解/仿写/代笔。内核是专业的, agent 是调度者。
"""
from __future__ import annotations

import os
import sys
from typing import Optional

# 把 data 目录加入 sys.path, 才能 import skills_core
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
if _DATA_DIR not in sys.path:
    sys.path.insert(0, _DATA_DIR)

try:
    import skills_core  # type: ignore
    _SKILL_AVAILABLE = True
except Exception as e:
    skills_core = None  # type: ignore
    _SKILL_AVAILABLE = False
    _SKILL_LOAD_ERROR = str(e)


# ---------------- 单例: NovelDeconstructionSkill ----------------
_skill_instance = None
_skill_init_error: Optional[str] = None


def get_skill():
    """获取 NovelDeconstructionSkill 单例。

    内核初始化会加载 4000+ 行拆解 DB, 故做单例避免重复开销。
    若加载失败,返回 None,调用方应降级处理。
    """
    global _skill_instance, _skill_init_error
    if not _SKILL_AVAILABLE:
        return None
    if _skill_instance is None and _skill_init_error is None:
        try:
            _skill_instance = skills_core.NovelDeconstructionSkill()
        except Exception as e:
            _skill_init_error = str(e)
            return None
    return _skill_instance


def skill_status() -> dict:
    """技能内核加载状态,供排查问题用。"""
    return {
        "available": _SKILL_AVAILABLE,
        "loaded": _skill_instance is not None,
        "error": _skill_init_error or (None if _SKILL_AVAILABLE else _SKILL_LOAD_ERROR if "_SKILL_LOAD_ERROR" in globals() else "未知"),
        "db_authors_count": len(getattr(skills_core, "NOVEL_DECONSTRUCTION_DB", [])) if skills_core else 0,
    }


# ---------------- 适配层: 工具方法封装 ----------------
# 每个方法对应一个 agent 可调用的工具, 签名匹配 tools.py 的 TOOL_SCHEMA

def deconstruct(user_input: str, return_prompt_only: bool = True) -> dict:
    """拆书解构: 解析用户意图, 匹配作家/作品, 生成拆解 Prompt。

    user_input: 自然语言, 如 "帮我拆解古龙的武侠风格" / "拆解《凡人修仙传》的节奏"
    return_prompt_only: True=只返回拆解 prompt (推荐给 LLM 用);
                        False=返回完整结构 (intent + matched_authors + final_prompt)
    """
    sk = get_skill()
    if sk is None:
        return {"error": "技能内核未加载", **skill_status()}
    try:
        result = sk.execute(user_input, return_prompt_only=return_prompt_only)
        if return_prompt_only:
            return {"deconstruction_prompt": result}
        import json
        return json.loads(result)
    except Exception as e:
        return {"error": f"拆解失败: {e}"}


def audit_novel(text: str, outline: Optional[str] = None) -> dict:
    """33 维审计: 对正文做 33 个维度的专业审计, 输出结构化报告。

    text: 待审计的正文
    outline: 可选, 大纲/细纲, 用于对照审计
    """
    sk = get_skill()
    if sk is None:
        return {"error": "技能内核未加载", **skill_status()}
    try:
        # 调用 auditor.audit() 拿结构化字典, 再 format_report() 拿可读文本
        raw_report = sk.auditor.audit(text, outline)
        formatted = sk.auditor.format_report(raw_report)
        return {
            "audit_report": raw_report,         # 结构化字典 (含 issues/highlights/scores)
            "audit_text": formatted,             # 可读文本报告
            "text_length": len(text),
        }
    except Exception as e:
        return {"error": f"审计失败: {e}"}


def detect_ai(text: str) -> dict:
    """AI 味检测: 检测正文的 AI 写作痕迹, 输出问题清单。

    检测维度: 重复句式 / 万能连接词 / 抽象描写 / 情感标签 / 逻辑跳跃 等
    """
    sk = get_skill()
    if sk is None:
        return {"error": "技能内核未加载", **skill_status()}
    try:
        # detector.detect() 返回 list[dict], format_report() 返回 str
        issues = sk.detector.detect(text)
        formatted = sk.detector.format_report(issues)
        return {
            "ai_issues": issues,                 # 结构化列表 (每项含 type/message/suggestion)
            "ai_text": formatted,                 # 可读文本报告
            "text_length": len(text),
        }
    except Exception as e:
        return {"error": f"AI 检测失败: {e}"}


def diagnose_opening(text: str) -> dict:
    """黄金三章诊断: 诊断开篇是否合格 (钩子/节奏/人设/世界观交代)。

    text: 前 1-3 章正文
    """
    sk = get_skill()
    if sk is None:
        return {"error": "技能内核未加载", **skill_status()}
    try:
        # opener.diagnose() 返回 dict, format_report() 返回 str
        raw_report = sk.opener.diagnose(text)
        formatted = sk.opener.format_report(raw_report)
        return {
            "opening_report": raw_report,         # 结构化字典
            "opening_text": formatted,             # 可读文本报告
            "text_length": len(text),
        }
    except Exception as e:
        return {"error": f"开篇诊断失败: {e}"}


def analyze_style(text: str, author_name: Optional[str] = None) -> dict:
    """文风分析: 提取正文的文风指纹 (句式/节奏/用词/视角)。"""
    sk = get_skill()
    if sk is None:
        return {"error": "技能内核未加载", **skill_status()}
    try:
        result = sk.analyze_style(text, author_name)
        return {"style_analysis": result, "text_length": len(text)}
    except Exception as e:
        return {"error": f"文风分析失败: {e}"}


def imitate_style(reference_text: str, topic: str, word_count: int = 800) -> dict:
    """文风仿写: 按参考文本的文风, 仿写指定话题。

    reference_text: 参考原文 (从原文学文风)
    topic: 要仿写的话题/场景
    word_count: 仿写字数
    """
    sk = get_skill()
    if sk is None:
        return {"error": "技能内核未加载", **skill_status()}
    try:
        result = sk.imitate_style(reference_text, topic, word_count)
        return {"imitated_text": result, "topic": topic, "word_count": word_count}
    except Exception as e:
        return {"error": f"仿写失败: {e}"}


def diagnose_stuck(text: str, last_chapter_summary: str = "") -> dict:
    """卡文诊断: 诊断为何写不下去, 给出续写方向建议。"""
    sk = get_skill()
    if sk is None:
        return {"error": "技能内核未加载", **skill_status()}
    try:
        result = sk.diagnose_stuck(text)
        return {"stuck_diagnosis": result}
    except Exception as e:
        return {"error": f"卡文诊断失败: {e}"}


def scout(genre: Optional[str] = None) -> dict:
    """扫榜: 基于内核 DB 扫描题材热度, 输出市场报告。"""
    sk = get_skill()
    if sk is None:
        return {"error": "技能内核未加载", **skill_status()}
    try:
        report = sk.scout(genre)
        return {"scout_report": report, "genre": genre or "通用"}
    except Exception as e:
        return {"error": f"扫榜失败: {e}"}


def generate_outline_kernel(concept: str, volumes: int = 3) -> dict:
    """大纲生成 (内核版): 用 EditorialPipeline 生成大纲。

    与 tools.py 的 generate_outline 区别: 本方法用内核的 EditorialPipeline,
    基于拆解 DB 的成功模式生成, 更专业。tools.generate_outline 直接调 LLM。
    """
    sk = get_skill()
    if sk is None:
        return {"error": "技能内核未加载", **skill_status()}
    try:
        result = sk.outline(concept, volumes)
        return {"outline": result, "volumes": volumes}
    except Exception as e:
        return {"error": f"大纲生成失败: {e}"}


async def ghostwrite(outline_text: str, style_ref: Optional[str] = None,
               chapter: int = 1, words: int = 3000,
               author_name: Optional[str] = None,
               pid: Optional[str] = None, chapter_id: Optional[str] = None) -> dict:
    """枪手代笔: 基于大纲+文风参考, 生成章节正文。

    内核 EditorialPipeline.ghostwriter 返回的是"专业写作 Prompt" (不是正文)。
    本方法在拿到 prompt 后, 自动调 LLM 生成正文, 并可选写入章节 (传 pid+chapter_id 时)。

    outline_text: 本章细纲
    style_ref: 文风参考文本 (可选, 从原文学文风)
    chapter: 章节序号
    words: 目标字数
    author_name: 指定作家文风 (可选)
    pid: 项目 id (可选, 传入则自动写入章节)
    chapter_id: 章节 id (可选, 传入则自动写入该章节)
    """
    sk = get_skill()
    if sk is None:
        return {"error": "技能内核未加载", **skill_status()}
    try:
        # 1. 内核生成专业写作 prompt
        prompt_text = sk.ghostwrite(outline_text, style_ref, chapter, words)
        # 2. 自动调 LLM 把 prompt 变成正文
        from .llm import stream
        from .config import get_settings
        pieces: list[str] = []
        async for tok in stream(
            [{"role": "user", "content": prompt_text}],
            get_settings().default_model,
            temperature=0.85,
            max_tokens=max(1024, int(words * 2)),
        ):
            pieces.append(tok)
        novel_text = "".join(pieces)
        # 3. 可选: 写入章节
        written = False
        if pid and chapter_id:
            from . import store
            ch = store.get_chapter(chapter_id)
            if ch:
                merged = (ch.get("content") or "") + ("\n" if ch.get("content") else "") + novel_text
                store.update_chapter(chapter_id, content=merged, status="writing")
                written = True
        return {
            "ghostwritten_text": novel_text,
            "chars": len(novel_text),
            "written_to_chapter": written,
            "chapter": chapter,
            "words_target": words,
        }
    except Exception as e:
        return {"error": f"代笔失败: {e}"}


def full_audit(text: str, outline: Optional[str] = None) -> dict:
    """完整审计: 33 维审计 + AI 味检测, 一次性出综合报告。"""
    sk = get_skill()
    if sk is None:
        return {"error": "技能内核未加载", **skill_status()}
    try:
        result = sk.full_audit(text, outline)
        # result 含 audit(str), ai_flavor(str), audit_raw(dict), ai_raw(list)
        return {
            "audit_text": result.get("audit", ""),
            "ai_text": result.get("ai_flavor", ""),
            "audit_report": result.get("audit_raw", {}),
            "ai_issues": result.get("ai_raw", []),
            "text_length": len(text),
        }
    except Exception as e:
        return {"error": f"完整审计失败: {e}"}
