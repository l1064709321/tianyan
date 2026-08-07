"""人类审批门模块 (改进: 原版 Agent 自己决定所有事, 无关键决策暂停)

功能:
- 重大决策前暂停, 等待用户确认
- 支持异步审批 (不阻塞 SSE 流)
- 审批结果缓存 (避免重复询问)

灵感来源:
- LangGraph 的 human-in-the-loop
- AutoGen 的 ExternalTermination
- dive-into-llms Ch9 的置信度 + 人类介入
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("tianyan")


@dataclass
class ApprovalRequest:
    """审批请求"""
    id: str
    pid: str
    agent: str
    action: str  # 审批的动作描述
    context: dict = field(default_factory=dict)  # 相关上下文
    status: str = "pending"  # pending | approved | rejected | timeout
    created_at: float = field(default_factory=time.time)
    resolved_at: Optional[float] = None
    user_response: str = ""


# 待审批队列
_pending_approvals: dict[str, ApprovalRequest] = {}
# 已审批缓存
_approval_cache: dict[str, str] = {}  # key -> "approved"/"rejected"

# 审批超时 (秒)
APPROVAL_TIMEOUT = 300  # 5 分钟


def _approval_key(pid: str, action: str) -> str:
    """生成审批缓存键"""
    import hashlib
    return hashlib.md5(f"{pid}:{action}".encode()).hexdigest()[:12]


def should_request_approval(action: str, context: dict = None) -> bool:
    """判断是否需要人类审批。

    需要审批的场景:
    1. 删除角色/设定
    2. 大幅修改剧情走向
    3. 涉及敏感内容
    4. 用户明确要求审批
    """
    # 关键词匹配
    approval_keywords = [
        "删除", "删掉", "移除",
        "结局", "结尾", "大结局",
        "死亡", "牺牲", "黑化",
        "敏感", "争议", "政治",
    ]
    action_lower = action.lower()
    return any(kw in action_lower for kw in approval_keywords)


async def request_approval(
    pid: str,
    agent: str,
    action: str,
    context: dict = None,
    timeout: int = APPROVAL_TIMEOUT,
) -> dict:
    """请求人类审批。

    返回:
    - status: "approved" | "rejected" | "timeout"
    - user_response: 用户的回复 (如果有)
    """
    import uuid

    # 检查缓存
    key = _approval_key(pid, action)
    if key in _approval_cache:
        cached = _approval_cache[key]
        logger.info(f"[审批] 命中缓存: {action[:50]}... -> {cached}")
        return {"status": cached, "cached": True}

    # 创建审批请求
    req = ApprovalRequest(
        id=str(uuid.uuid4())[:8],
        pid=pid,
        agent=agent,
        action=action,
        context=context or {},
    )
    _pending_approvals[req.id] = req

    logger.info(f"[审批] 等待用户确认: {action[:80]}...")

    # 等待审批 (带超时)
    start = time.time()
    while time.time() - start < timeout:
        if req.status != "pending":
            break
        await asyncio.sleep(1)

    # 超时处理
    if req.status == "pending":
        req.status = "timeout"
        logger.warning(f"[审批] 超时 ({timeout}s): {action[:50]}...")

    # 缓存结果
    _approval_cache[key] = req.status
    req.resolved_at = time.time()

    return {
        "status": req.status,
        "user_response": req.user_response,
        "request_id": req.id,
    }


def approve(request_id: str, response: str = "approved") -> bool:
    """用户审批通过"""
    req = _pending_approvals.get(request_id)
    if not req:
        return False
    req.status = "approved"
    req.user_response = response
    req.resolved_at = time.time()
    return True


def reject(request_id: str, response: str = "rejected") -> bool:
    """用户拒绝"""
    req = _pending_approvals.get(request_id)
    if not req:
        return False
    req.status = "rejected"
    req.user_response = response
    req.resolved_at = time.time()
    return True


def list_pending() -> list[dict]:
    """列出所有待审批请求"""
    return [
        {
            "id": req.id,
            "agent": req.agent,
            "action": req.action,
            "status": req.status,
            "created_at": req.created_at,
        }
        for req in _pending_approvals.values()
        if req.status == "pending"
    ]
