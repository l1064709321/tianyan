"""动态规划模块 (改进: 原版固定 8 阶段流程, 不灵活)

功能:
- 动态 TODO 列表: 根据当前状态自动生成下一步计划
- 计划执行追踪: 标记已完成/进行中/待办
- 自适应调整: 根据执行结果调整后续计划

灵感来源:
- Codex 的 TODO 列表
- Claude Code 的 Plan Mode
- happy-llm 的自主规划概念
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from .config import get_settings
from .llm import chat
from . import store

logger = logging.getLogger("novel_agent")


@dataclass
class PlanStep:
    """单个计划步骤"""
    id: int
    description: str
    agent: str = ""  # 负责的 agent
    tool: str = ""  # 需要调用的工具
    status: str = "pending"  # pending | in_progress | completed | skipped
    result: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


@dataclass
class ProjectPlan:
    """项目级计划"""
    pid: str
    steps: list[PlanStep] = field(default_factory=list)
    current_step: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


# 内存中的计划缓存 (不持久化，重启后重新生成)
_plans: dict[str, ProjectPlan] = {}


def get_plan(pid: str) -> Optional[ProjectPlan]:
    """获取项目的当前计划"""
    return _plans.get(pid)


def save_plan(plan: ProjectPlan) -> None:
    """保存计划到内存"""
    plan.updated_at = time.time()
    _plans[plan.pid] = plan


def mark_step(pid: str, step_id: int, status: str, result: str = "") -> None:
    """标记步骤状态"""
    plan = get_plan(pid)
    if not plan:
        return
    for step in plan.steps:
        if step.id == step_id:
            step.status = status
            step.result = result
            if status == "completed":
                step.completed_at = time.time()
            break
    save_plan(plan)


def get_current_step(pid: str) -> Optional[PlanStep]:
    """获取当前待执行的步骤"""
    plan = get_plan(pid)
    if not plan:
        return None
    for step in plan.steps:
        if step.status in ("pending", "in_progress"):
            return step
    return None


def get_plan_summary(pid: str) -> str:
    """获取计划摘要 (注入到 agent 上下文)"""
    plan = get_plan(pid)
    if not plan:
        return ""
    
    lines = ["# 当前执行计划"]
    for step in plan.steps:
        status_icon = {
            "pending": "⬜",
            "in_progress": "🔶",
            "completed": "✅",
            "skipped": "⏭️",
        }.get(step.status, "❓")
        lines.append(f"{status_icon} [{step.id}] {step.description}")
        if step.agent:
            lines.append(f"     负责: {step.agent}")
        if step.result and step.status == "completed":
            lines.append(f"     结果: {step.result[:100]}...")
    
    # 统计
    total = len(plan.steps)
    done = sum(1 for s in plan.steps if s.status == "completed")
    lines.append(f"\n进度: {done}/{total} ({done*100//max(total,1)}%)")
    
    return "\n".join(lines)


async def generate_plan(pid: str, user_goal: str) -> ProjectPlan:
    """根据用户目标动态生成执行计划。
    
    不再固定 8 阶段，而是根据当前项目状态 + 用户目标，
    让 LLM 自主规划需要执行的步骤。
    """
    # 获取当前项目状态
    proj = store.get_project(pid)
    chapters = store.list_chapters(pid)
    elements = store.list_elements(pid)
    stats = store.stats(pid)
    
    status_info = (
        f"项目状态:\n"
        f"- 章节数: {stats.get('chapters', 0)}\n"
        f"- 设定数: {stats.get('elements', 0)}\n"
        f"- 已写字数: {stats.get('total_chars', 0)}\n"
        f"- 伏笔数: {stats.get('foreshadowings', 0)}\n"
        f"- 有大纲的章节: {sum(1 for c in chapters if c.get('outline'))}\n"
        f"- 有正文的章节: {sum(1 for c in chapters if c.get('content'))}\n"
    )
    
    system = (
        "你是小说创作项目的规划助手。根据用户目标和当前项目状态，"
        "生成一个可执行的分步计划。\n\n"
        "可用的 agent:\n"
        "- orchestrator: 总编，全局调度\n"
        "- story-architect: 架构师，扫榜/拆书/大纲/世界观DB/里程碑\n"
        "- narrative-writer: 主笔，正文写作\n"
        "- character-designer: 角色师，角色档案管理\n"
        "- consistency-checker: 质检员，四重校验\n"
        "- story-explorer: 资料员，上下文加载/风格缓存\n"
        "- presenter: 监制，交付报告\n\n"
        "可用的工具:\n"
        "- scan_bestseller: 扫榜调研\n"
        "- analyze_novel: 拆书解构\n"
        "- generate_outline: 生成大纲\n"
        "- continue_writing: 续写正文\n"
        "- polish: 润色/改写\n"
        "- add_element: 添加设定\n"
        "- review_chapter: 毒舌审稿\n"
        "- quality_check: 一致性检查\n\n"
        "严格只输出 JSON。"
    )
    
    schema = {
        "steps": [
            {
                "id": 1,
                "description": "步骤描述",
                "agent": "负责的 agent 名",
                "tool": "需要调用的工具",
            }
        ],
        "reasoning": "规划思路 (为什么这样安排)",
    }
    
    user = (
        f"{status_info}\n\n"
        f"用户目标: {user_goal}\n\n"
        f"请生成执行计划，只输出 JSON:\n{json.dumps(schema, ensure_ascii=False)}"
    )
    
    try:
        resp = await chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            get_settings().default_model,
            temperature=0.5,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )
        raw = resp["content"].strip()
        import re
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            raw = m.group(0)
        data = json.loads(raw)
        
        steps = []
        for i, s in enumerate(data.get("steps", [])):
            steps.append(PlanStep(
                id=s.get("id", i + 1),
                description=s.get("description", ""),
                agent=s.get("agent", ""),
                tool=s.get("tool", ""),
            ))
        
        plan = ProjectPlan(pid=pid, steps=steps)
        save_plan(plan)
        
        logger.info(f"[动态规划] 为项目 {pid} 生成了 {len(steps)} 步计划")
        return plan
        
    except Exception as e:
        logger.error(f"[动态规划] 生成计划失败: {e}")
        # 返回一个默认的单步计划
        plan = ProjectPlan(pid=pid, steps=[
            PlanStep(id=1, description=user_goal, agent="orchestrator"),
        ])
        save_plan(plan)
        return plan
