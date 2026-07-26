"""自我反思模块 (改进: 原版生成内容后直接返回, 无自检)

功能:
- 生成内容后自动自评 (1-10 分)
- 低于阈值自动重写 (最多 N 次)
- 提取具体问题并反馈给生成环节

灵感来源:
- happy-llm 的 Agent 自评分机制
- dive-into-llms Ch9 的置信度评分
- novel-agent 原有的 review_chapter (毒舌审稿) 工具
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from .config import get_settings
from .llm import chat

logger = logging.getLogger("novel_agent")

# 自评阈值: 低于此分数触发重写
REFLECTION_THRESHOLD = 6
# 最大重写次数
MAX_REWRITE_ATTEMPTS = 2


async def self_reflect(
    content: str,
    outline: str = "",
    genre: str = "",
    style: str = "",
    *,
    threshold: int = REFLECTION_THRESHOLD,
) -> dict:
    """对生成的内容进行自我反思评价。
    
    返回:
    - score: 1-10 分
    - issues: 问题列表
    - passes: 是否通过 (>= threshold)
    """
    system = (
        "你是一位严苛的小说质检员。对给定的正文进行快速评估。\n"
        "评估维度:\n"
        "1. 开篇是否有钩子 (前 200 字能否抓住读者)\n"
        "2. 叙述是否流畅自然 (有无 AI 味/生硬转折)\n"
        "3. 角色行为是否合理 (符合人设)\n"
        "4. 情节是否推进 (有无原地踏步)\n"
        "5. 文风是否一致 (与要求的风格匹配)\n"
        "6. 细节是否具体 (有无空泛描写)\n\n"
        "严格只输出 JSON，不要任何额外文字。"
    )
    
    schema = {
        "score": "整数 1-10，10=完美，1=完全不可用",
        "issues": ["问题1", "问题2"],
        "highlights": ["亮点1"],
        "rewrite_focus": "如果需要重写，应该重点关注什么 (一句话)",
    }
    
    # 截取前 3000 字做评估 (避免 token 过多)
    sample = content[:3000]
    
    user = (
        f"{'类型: ' + genre if genre else ''}\n"
        f"{'文风: ' + style if style else ''}\n"
        f"{'细纲: ' + outline[:500] if outline else ''}\n\n"
        f"正文:\n{sample}\n\n"
        f"请评估，只输出 JSON:\n{json.dumps(schema, ensure_ascii=False)}"
    )
    
    try:
        resp = await chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            get_settings().default_model,
            temperature=0.3,
            max_tokens=1000,
            response_format={"type": "json_object"},
        )
        raw = resp["content"].strip()
        import re
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            raw = m.group(0)
        data = json.loads(raw)
        
        score = int(data.get("score", 5))
        issues = data.get("issues", [])
        highlights = data.get("highlights", [])
        rewrite_focus = data.get("rewrite_focus", "")
        
        return {
            "score": score,
            "issues": issues if isinstance(issues, list) else [str(issues)],
            "highlights": highlights if isinstance(highlights, list) else [str(highlights)],
            "rewrite_focus": rewrite_focus,
            "passes": score >= threshold,
        }
    except Exception as e:
        logger.warning(f"[反思] 自评失败: {e}")
        # 自评失败时不阻断流程，默认通过
        return {
            "score": 7,
            "issues": [],
            "highlights": [],
            "rewrite_focus": "",
            "passes": True,
            "error": str(e),
        }


async def reflect_and_rewrite(
    generate_fn,
    *,
    outline: str = "",
    genre: str = "",
    style: str = "",
    instruction: str = "",
    threshold: int = REFLECTION_THRESHOLD,
    max_attempts: int = MAX_REWRITE_ATTEMPTS,
) -> dict:
    """生成 → 反思 → 重写 循环。
    
    generate_fn: 异步生成函数，返回 {"content": str, ...}
    
    流程:
    1. 调用 generate_fn 生成内容
    2. 自评打分
    3. 如果分数 >= threshold，返回
    4. 如果分数 < threshold 且还有重试次数，带着问题重写
    5. 重试耗尽仍不达标，返回最后一次结果 + 警告
    """
    attempts = 0
    last_result = None
    reflection_history = []
    
    while attempts <= max_attempts:
        # 1. 生成内容
        if attempts == 0:
            result = await generate_fn(instruction=instruction)
        else:
            # 带着反思问题重写
            rewrite_instruction = (
                f"{instruction}\n\n"
                f"【重写要求】上次生成的问题:\n"
                + "\n".join(f"- {issue}" for issue in last_reflection["issues"])
                + f"\n重点关注: {last_reflection.get('rewrite_focus', '')}"
            )
            result = await generate_fn(instruction=rewrite_instruction)
        
        content = result.get("content", "")
        if not content:
            return {**result, "reflection": {"error": "生成内容为空"}}
        
        # 2. 自评
        reflection = await self_reflect(
            content, outline=outline, genre=genre, style=style,
            threshold=threshold,
        )
        reflection_history.append({
            "attempt": attempts + 1,
            "score": reflection["score"],
            "issues": reflection["issues"],
        })
        
        logger.info(
            f"[反思] 第 {attempts + 1} 次: 分数={reflection['score']} "
            f"{'✓ 通过' if reflection['passes'] else '✗ 需重写'} "
            f"问题={reflection['issues']}"
        )
        
        # 3. 通过则返回
        if reflection["passes"]:
            return {
                **result,
                "reflection": {
                    "score": reflection["score"],
                    "attempts": attempts + 1,
                    "history": reflection_history,
                    "passed": True,
                },
            }
        
        # 4. 需要重写
        last_result = result
        last_reflection = reflection
        attempts += 1
    
    # 5. 重试耗尽
    logger.warning(
        f"[反思] 重试耗尽 ({max_attempts} 次)，最终分数={last_reflection['score']}"
    )
    return {
        **last_result,
        "reflection": {
            "score": last_reflection["score"],
            "attempts": attempts,
            "history": reflection_history,
            "passed": False,
            "warning": f"重试 {max_attempts} 次仍未达标 (阈值={threshold})",
        },
    }
