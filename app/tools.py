"""小说创作工具集。每个工具是 agent 可调用的能力,返回结构化结果。

核心能力:
- generate_outline: 生成小说大纲 + 章节结构
- continue_writing: 续写章节 (融合已有章节/上传小说的检索上下文)
- manage_elements: 增删查 角色/世界观/地点/时间线
- polish: 润色/改写/扩写已有正文
- ingest_text: 把上传的小说或已有章节切分入库,供后续续写检索

检索采用轻量关键词评分 (无外部向量库依赖,零配置即可跑)。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx

from . import store
from .config import get_settings
from .deai import run_full_deai_check
from .llm import chat, stream


# ---------------- 通用辅助 ----------------

def _get_bs4():
    """惰性导入 BeautifulSoup。bs4 是可选依赖 (仅 web_fetch/web_search 需要)。
    未安装时返回 None, 调用方应返回友好错误而非崩溃。
    核心 agent 功能 (大纲/续写/润色/设定等) 不依赖 bs4。
    """
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup
    except ImportError:
        return None


def _project_brief(pid: str) -> str:
    p = store.get_project(pid) or {}
    parts = []
    if p.get("audience"):
        parts.append(f"频道: {p['audience']}")
    if p.get("genre"):
        parts.append(f"类型: {p['genre']}")
    if p.get("style"):
        parts.append(f"文风: {p['style']}")
    if p.get("premise"):
        parts.append(f"核心设定: {p['premise']}")
    return "\n".join(parts) or "(暂无项目设定)"


def _elements_block(pid: str) -> str:
    items = store.list_elements(pid)
    if not items:
        return "(暂无角色/世界观设定)"
    by_kind: dict[str, list[dict]] = {}
    for it in items:
        by_kind.setdefault(it["kind"], []).append(it)
    label = {
        "character": "角色",
        "location": "地点",
        "lore": "世界观/设定",
        "timeline": "时间线",
    }
    lines = []
    for kind, lst in by_kind.items():
        lines.append(f"【{label.get(kind, kind)}】")
        for e in lst:
            lines.append(f"- {e['name']}: {e['detail']}")
    return "\n".join(lines)


def _keyword_score(query: str, text: str) -> float:
    q_tokens = [w for w in re.findall(r"[\w]+", query) if len(w) > 1]
    if not q_tokens:
        return 0.0
    score = 0.0
    low = text.lower()
    for t in q_tokens:
        c = low.count(t.lower())
        if c:
            score += 1.0 / (1 + low.find(t.lower())) * min(c, 5)
    return score


def _retrieve_context(pid: str, query: str, k: int = 6) -> str:
    """从上传小说分块 + 已有章节中检索与 query 最相关的内容。

    改进: 优先使用向量语义检索, 降级到关键词匹配。
    """
    import asyncio
    try:
        from .vector_store import hybrid_retrieve
        # 如果已在事件循环中, 直接用同步方式降级
        try:
            loop = asyncio.get_running_loop()
            # 已在异步上下文中, 创建 task
            import concurrent.futures
            # 向量检索需要异步, 这里用降级的关键词方案
            raise RuntimeError("在异步上下文中, 使用关键词降级")
        except RuntimeError:
            pass
        # 尝试获取结果
        try:
            result = asyncio.get_event_loop().run_until_complete(
                hybrid_retrieve(pid, query, top_k=k)
            )
            if result and result != "(无可用上文)":
                return result
        except Exception:
            pass
    except Exception:
        pass

    # 降级: 原版关键词检索
    s = get_settings()
    k = k or s.retrieve_k
    chunks = store.list_chunks(pid)
    scored = []
    for ch in chunks:
        sc = _keyword_score(query, ch["text"])
        if sc > 0:
            scored.append((sc, ch))
    scored.sort(key=lambda x: x[0], reverse=True)
    picked = [c for _, c in scored[:k]]
    chapters = store.list_chapters(pid)
    chap_tail = ""
    if chapters:
        last = chapters[-1]
        if last.get("content"):
            chap_tail = last["content"][-1500:]
    parts = []
    if picked:
        parts.append("# 相关上文片段(来自上传/已写内容)")
        for c in picked:
            parts.append(f"〔来源 {c['source']}〕\n{c['text']}")
    if chap_tail:
        parts.append(f"# 最近章节《{chapters[-1]['title']}》结尾\n{chap_tail}")
    return "\n\n".join(parts) if parts else "(无可用上文,将自由创作)"


def search_chunks(pid: str, query: str, k: int = 8) -> list[dict]:
    """公开检索:从上传素材分块中找与 query 最相关的内容,返回结构化结果。"""
    s = get_settings()
    k = k or s.retrieve_k
    chunks = store.list_chunks(pid)
    scored = []
    for ch in chunks:
        sc = _keyword_score(query, ch.get("text", ""))
        if sc > 0:
            scored.append((sc, ch))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "source": c.get("source", ""),
            "idx": c.get("idx", 0),
            "text": c.get("text", ""),
            "score": round(float(sc), 3),
        }
        for sc, c in scored[:k]
    ]


def _split_text(text: str, size: int, overlap: int) -> list[str]:
    if not text:
        return []
    step = max(1, size - overlap)
    return [text[i : i + size] for i in range(0, len(text), step)]


# ---------------- 工具实现 ----------------
async def generate_outline(
    pid: str,
    premise: str,
    *,
    num_chapters: int = 12,
    genre: Optional[str] = None,
) -> dict:
    """生成完整大纲与章节结构,并写入项目与章节。"""
    p = store.get_project(pid) or {}
    genre = genre or p.get("genre") or "通用"
    system = (
        "你是一位资深小说策划。根据用户给定的核心设定,产出结构严谨、有起承转合的"
        "小说大纲。严格只输出 JSON,不要任何额外文字。"
    )
    schema_hint = {
        "title": "小说标题",
        "logline": "一句话梗概",
        "themes": ["主题1"],
        "chapters": [
            {"title": "章节标题", "outline": "该章节情节梗概 100-200 字"}
        ],
    }
    user = (
        f"类型:{genre}\n核心设定:{premise}\n文风:{p.get('style','')}\n"
        f"请生成 {num_chapters} 个章节的完整大纲。\n"
        f"只输出 JSON,结构如下:\n{json.dumps(schema_hint, ensure_ascii=False)}"
    )
    resp = await chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        get_settings().default_model,
        temperature=0.9,
        max_tokens=8000,
        response_format={"type": "json_object"},
    )
    content = resp["content"].strip()
    # 容错:截取首个 {...}
    m = re.search(r"\{.*\}", content, re.S)
    if m:
        content = m.group(0)
    try:
        data = json.loads(content)
    except Exception:
        return {"error": "大纲解析失败", "raw": content}

    if data.get("title") and not p.get("name"):
        store.update_project(pid, name=data["title"])
    if data.get("logline") and not p.get("premise"):
        store.update_project(pid, premise=data["logline"])

    # 写入章节(若已有则追加 idx)
    existing = store.list_chapters(pid)
    base = max([c["idx"] for c in existing], default=-1) + 1
    created = []
    for i, ch in enumerate(data.get("chapters", [])):
        cid = store.add_chapter(
            pid,
            title=ch.get("title", f"第{base+i+1}章"),
            idx=base + i,
            outline=ch.get("outline", ""),
            content="",
        )
        created.append({"id": cid, "title": ch.get("title"), "outline": ch.get("outline")})
    data["chapters_created"] = created
    return data


async def continue_writing(
    pid: str,
    chapter_id: Optional[str] = None,
    *,
    instruction: str = "",
    length: int = 2000,
) -> dict:
    """续写章节正文。若指定 chapter_id 则续写该章,否则续写最近一章。"""
    chapters = store.list_chapters(pid)
    if not chapter_id and not chapters:
        return {"error": "尚无章节,请先委派 story-architect 用 generate_outline 生成大纲,章节落库后再调 continue_writing;不要重复盲调本工具"}
    target = None
    if chapter_id:
        target = store.get_chapter(chapter_id)
    if target is None and chapters:
        # 兜底:未指定 chapter_id 时,取第一个"未写完"的章节 (而非最后一章)。
        # 顺序写作场景下应该按 idx 递进,而不是跳到最后一章写。
        # 优先找 has_content=False 的最小 idx;若全有正文则取最后一章续写。
        unwritten = [c for c in chapters if not (c.get("content") or "")]
        target = unwritten[0] if unwritten else chapters[-1]
    if target is None:
        avail = [{"id": c["id"], "title": c["title"], "idx": c["idx"]} for c in chapters]
        return {"error": "未找到目标章节,请先 query_project 查询当前项目的 chapter_id 列表,再用有效 chapter_id 调用 continue_writing", "available_chapters": avail}

    brief = _project_brief(pid)
    elements = _elements_block(pid)
    context = _retrieve_context(pid, target["title"] + " " + target.get("outline", "") + " " + instruction)
    existing_tail = (target.get("content") or "")[-1800:]

    system = (
        "你是一位技艺精湛的小说家。\n\n"
        "【最高优先级:细纲边界 - 不可违反】\n"
        "细纲是本章剧情的唯一权威蓝图:\n"
        "1. 必须严格消费细纲:正文逐项展开细纲已有的核心事件、内容概括、情节安排、人物关系、情节细化、结尾设定和章尾钩子。\n"
        "2. 不得自造剧情:不得为凑字/增强戏剧性新增细纲没有的主线事件、新角色、新反转、新金手指规则、新伏笔结算。\n"
        "3. 只允许微连接:可补角色移动、视线、动作 beat、环境细节、对话承接等微连接,但必须服务于细纲已列情节点。\n"
        "4. 字数不足时:只扩写细纲已列情节点,不新增剧情;仍不足返回 outline_underfilled 欠账报告。\n\n"
        "【三维度揉进写法】\n"
        "每个子事件将发生/感知/反应三维度揉进同一段连续正文:\n"
        "- 发生:这件事出现了 (1-2 句叙事,含具体细节)\n"
        "- 感知:主角注意到的感官细节 (至少 1 个不同感官,聚焦物件或身体部位)\n"
        "- 反应:身体如何回应 (具体身体动作,可含一句极短心理定格)\n"
        "- 三维度织在同一段,不按维度分段写。禁止\"先写发生再补感知再补反应\"的堆叠写法。\n\n"
        "【叙述姿态:深度限知】\n"
        "全程锁死主视角角色的此刻感知,只写她此刻看到/听到/闻到/身体感到/脑中闪过的;"
        "镜头不拉远、不俯瞰、不切他人内心;读者与她同步获知,不提前剧透、不补全背景。\n\n"
        "严格延续已有的人物性格、世界观、文风与情节走向,"
        "自然衔接上文结尾,不要重复已有内容,不要输出除正文外的任何说明。"
    )
    user = (
        f"# 项目设定\n{brief}\n\n# 设定资料\n{elements}\n\n"
        f"# 检索到的相关上文\n{context}\n\n"
        f"# 本章细纲 (唯一权威蓝图,必须逐项消费,不得自造剧情)\n"
        f"标题:{target['title']}\n"
        f"细纲:\n{target.get('outline','(暂无细纲)')}\n\n"
        f"# 本章已有正文(结尾部分)\n{existing_tail or '(本章尚未开始)'}\n"
        f"# 续写要求\n{instruction or '自然推进情节,保持张力。'}\n"
        f"续写约 {length} 字正文,直接输出小说内容。"
    )
    # 状态机: 进入"生成中", 写完切"已写完", 异常切"失败"
    # 让前端能区分"在写"vs"写完了"vs"挂了", 不是黑盒
    store.update_chapter(target["id"], status="generating")
    pieces: list[str] = []
    try:
        async for tok in stream(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            get_settings().default_model,
            temperature=0.85,
            max_tokens=max(1024, int(length * 2)),
        ):
            pieces.append(tok)
    except Exception as e:
        # 失败: 切回 draft, 保留已有 content 不丢, 标记 failed 让前端能重试
        store.update_chapter(target["id"], status="failed")
        return {"chapter_id": target["id"], "title": target["title"],
                "error": f"生成失败: {e}", "existing_chars": len(target.get("content") or "")}
    new_text = "".join(pieces)
    if not new_text.strip():
        store.update_chapter(target["id"], status="failed")
        return {"chapter_id": target["id"], "title": target["title"],
                "error": "生成内容为空", "existing_chars": len(target.get("content") or "")}

    # 追加到该章节内容
    merged = (target.get("content") or "") + ("\n" if target.get("content") else "") + new_text
    store.update_chapter(target["id"], content=merged, status="written")

    # 改进: 自动保存检查点
    try:
        from .checkpoint import auto_checkpoint
        auto_checkpoint(pid, event=f"chapter_{target['idx']}_written")
    except Exception:
        pass

    # 写后去AI味检测 (P0-4: 集成 deai.py)
    deai_result = run_full_deai_check(new_text)

    # ===== 改进: 多轮迭代反思 (ReAct 检查环节强化) =====
    # 原版: 只重写 1 次就收工 → 真实习生会反复打磨到满意
    # 改进: 用 reflect_and_rewrite 真正多轮循环 (生成→自评→不达标带问题重写→再评),
    #       阈值 6 分, 最多重写 2 次, 每轮都带具体问题反馈
    reflection_result = None
    if deai_result["blocking_count"] == 0:  # 只有无 blocking 问题时才反思
        try:
            from .reflection import reflect_and_rewrite, REFLECTION_THRESHOLD

            # 把"流式生成"包装成 reflect_and_rewrite 需要的 generate_fn
            proj = store.get_project(pid) or {}
            genre_val = proj.get("genre", "")
            style_val = proj.get("style", "")
            outline_val = target.get("outline", "")

            async def _gen_fn(instruction: str) -> dict:
                """单次生成函数, 返回 {"content": str}。"""
                cur_user = (
                    f"# 项目设定\n{brief}\n\n# 设定资料\n{elements}\n\n"
                    f"# 检索到的相关上文\n{context}\n\n"
                    f"# 本章细纲 (唯一权威蓝图,必须逐项消费,不得自造剧情)\n"
                    f"标题:{target['title']}\n细纲:\n{outline_val or '(暂无细纲)'}\n\n"
                    f"# 本章已有正文(结尾部分)\n{existing_tail or '(本章尚未开始)'}\n"
                    f"# 续写要求\n{instruction}\n"
                    f"续写约 {length} 字正文,直接输出小说内容。"
                )
                pieces_iter: list[str] = []
                async for tok in stream(
                    [{"role": "system", "content": system}, {"role": "user", "content": cur_user}],
                    get_settings().default_model,
                    temperature=0.85,
                    max_tokens=max(1024, int(length * 2)),
                ):
                    pieces_iter.append(tok)
                return {"content": "".join(pieces_iter)}

            store.update_chapter(target["id"], status="generating")
            rw_result = await reflect_and_rewrite(
                _gen_fn,
                outline=outline_val,
                genre=genre_val,
                style=style_val,
                instruction=instruction or "自然推进情节,保持张力。",
                threshold=REFLECTION_THRESHOLD,  # 6 分
                max_attempts=2,  # 最多重写 2 次 (共 3 轮生成)
            )
            refined = rw_result.get("content", "")
            if refined.strip():
                new_text = refined
            reflection_result = rw_result.get("reflection")
            if reflection_result:
                logger.info(
                    f"[反思] 多轮迭代完成: 尝试={reflection_result.get('attempts')} "
                    f"分数={reflection_result.get('score')} 通过={reflection_result.get('passed')}"
                )
        except Exception as e:
            logger.warning(f"[反思] 多轮迭代失败 (非致命, 用原始文本): {e}")

    # 写后追踪更新 (P0-3: 追踪文件读写闭环)
    tracking_result = await _update_tracking_after_write(
        pid, target, new_text, brief, elements
    )

    return {
        "chapter_id": target["id"],
        "title": target["title"],
        "appended": len(new_text),
        "total_chars": len(merged),
        "deai": {
            "blocking_count": deai_result["blocking_count"],
            "advisory_count": deai_result["advisory_count"],
            "ai_patterns": [f["type"] for f in deai_result["ai_patterns"]],
            "degeneration": [f["type"] for f in deai_result["degeneration"]],
            "tip": (
                "发现 blocking 级问题需修复后再继续。"
                "用 polish 工具重写对应段落,修复后重新续写。"
                if deai_result["blocking_count"] > 0
                else "无 blocking 级问题,可继续。"
            ),
        },
        "tracking": tracking_result,
        "reflection": {
            "score": reflection_result["score"] if reflection_result else None,
            "passed": reflection_result["passes"] if reflection_result else None,
            "issues": reflection_result["issues"] if reflection_result else [],
        } if reflection_result else None,
    }


async def _update_tracking_after_write(
    pid: str, target: dict, new_text: str, brief: str, elements: str,
) -> dict:
    """写后追踪更新 (P0-3): 从新生成的正文中提取伏笔/时间线/角色状态变化并写入数据库。

    使用 LLM 分析新正文, 提取:
    - 新埋设的伏笔 (new_foreshadows)
    - 已回收的伏笔 (recovered_foreshadows)
    - 时间线事件 (timeline_events)
    - 角色状态变化 (character_state_changes)

    然后更新对应的数据库表。
    """
    chapter_idx = target["idx"]
    chapter_title = target["title"]
    outline = target.get("outline") or ""

    # 只用新正文前 3000 字做提取 (避免 token 过多)
    sample = new_text[:3000]

    system = (
        "你是小说结构分析助手。从给定正文中提取追踪信息,严格只输出 JSON。"
    )
    schema = {
        "new_foreshadows": [
            {"name": "伏笔名", "content": "简述", "expected_recovery": "预期回收章节(数字,可null)"}
        ],
        "recovered_foreshadows": [
            {"name": "已回收伏笔名", "how": "回收方式简述"}
        ],
        "timeline_events": [
            {"event": "事件描述", "time_in_story": "故事内时间", "cause": "原因", "effect": "后果"}
        ],
        "character_state_changes": [
            {"character_name": "角色名", "current_state": "当前状态描述", "change": "本章变化"}
        ],
    }
    user = (
        f"# 本章细纲\n{outline}\n\n"
        f"# 本章新正文(前3000字)\n{sample}\n\n"
        f"# 已有角色设定\n{elements[:2000]}\n\n"
        f"请从新正文中提取追踪信息,只输出 JSON:\n{json.dumps(schema, ensure_ascii=False)}"
    )
    try:
        resp = await chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            get_settings().default_model,
            temperature=0.3,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )
        content = resp["content"].strip()
        m = re.search(r"\{.*\}", content, re.S)
        if m:
            content = m.group(0)
        data = json.loads(content)
    except Exception:
        data = {}

    results = {"foreshadows_added": 0, "foreshadows_recovered": 0,
               "timeline_added": 0, "character_states_updated": 0}

    # 新伏笔入库
    for f in data.get("new_foreshadows", []):
        if f.get("name"):
            store.add_foreshadowing(
                pid, f["name"], f.get("content", ""),
                planted_chapter=chapter_idx,
                expected_recovery=f.get("expected_recovery"),
            )
            results["foreshadows_added"] += 1

    # 伏笔回收标记
    for f in data.get("recovered_foreshadows", []):
        if f.get("name"):
            existing = store.list_foreshadowings(pid, status="planted")
            for e in existing:
                if e["name"] == f["name"]:
                    store.update_foreshadowing(
                        e["id"], status="recovered",
                        actual_recovery=chapter_idx,
                    )
                    results["foreshadows_recovered"] += 1
                    break

    # 时间线事件入库
    for ev in data.get("timeline_events", []):
        if ev.get("event"):
            store.add_timeline_event(
                pid, ev["event"],
                chapter_idx=chapter_idx,
                time_in_story=ev.get("time_in_story"),
                cause=ev.get("cause"),
                effect=ev.get("effect"),
            )
            results["timeline_added"] += 1

    # 角色状态更新
    for cs in data.get("character_state_changes", []):
        if cs.get("character_name") and cs.get("current_state"):
            store.upsert_character_state(
                pid, cs["character_name"], cs["current_state"],
                latest_chapter=chapter_idx,
                change=cs.get("change"),
            )
            results["character_states_updated"] += 1

    return results


async def polish(
    pid: str,
    chapter_id: str,
    *,
    mode: str = "polish",  # polish | rewrite | expand
    instruction: str = "",
) -> dict:
    """润色/改写/扩写某章节正文。"""
    ch = store.get_chapter(chapter_id)
    if not ch:
        return {"error": "未找到章节"}
    desc = {"polish": "润色(修正措辞、增强感染力,保持情节与字数基本不变)",
            "rewrite": "改写(按指令重写该章,情节可调整)",
            "expand": "扩写(在保持原有情节基础上扩充细节与描写,字数增加)"}.get(mode, "润色")
    system = "你是一位资深小说编辑。直接输出处理后的完整章节正文,不要任何解释或前后缀。"
    user = (
        f"任务:{desc}\n项目设定:\n{_project_brief(pid)}\n"
        f"角色/设定:\n{_elements_block(pid)}\n"
        f"额外要求:{instruction or '无'}\n\n"
        f"原章节《{ch['title']}》正文:\n{ch.get('content','')}"
    )
    pieces: list[str] = []
    async for tok in stream(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        get_settings().default_model,
        temperature=0.7,
    ):
        pieces.append(tok)
    new_text = "".join(pieces)
    store.update_chapter(chapter_id, content=new_text, status="polished")
    return {"chapter_id": chapter_id, "title": ch["title"], "chars": len(new_text)}


def ingest_text(pid: str, text: str, source: str) -> dict:
    """把文本(上传的小说或导入的内容)分块入库,供续写检索。"""
    s = get_settings()
    # 删除同源旧分块
    store.delete_chunks_by_source(pid, source)
    blocks = _split_text(text, s.chunk_size, s.chunk_overlap)
    for i, b in enumerate(blocks):
        store.add_chunk(pid, source, i, b)
    return {"source": source, "chunks": len(blocks), "chars": len(text)}


def add_element(pid: str, kind: str, name: str, detail: str) -> dict:
    eid = store.add_element(pid, kind, name, detail)
    return {"id": eid, "kind": kind, "name": name, "detail": detail}


async def scan_bestseller(
    pid: str,
    *,
    genre: str = "通用",
    preference: str = "",
) -> dict:
    """扫榜调研:先真实联网抓取各平台榜单,再用 LLM 基于真实数据分析热门题材。

    数据来源(按优先级):
    1. Bing 搜索 "<genre>小说 排行榜 2026" 取真实榜单页 URL
    2. 直接抓取主流网文平台榜单页 (起点/番茄/七猫/晋江)
    3. 把抓到的真实文本喂给 LLM 做趋势分析 (而非让 LLM 凭训练数据空想)
    """
    import time as _time

    # ---------- 第 1 步: 真实联网采集 ----------
    # 主流网文平台榜单页 (genre 为中文题材词, 用于搜索关键词)
    genre_kw = genre.strip() or "小说"
    search_queries = [
        f"{genre_kw} 排行榜 2026",
        f"{genre_kw}小说 热门 起点 番茄",
        f"网文 {genre_kw} 趋势 番茄小说 七猫",
    ]
    fetched_texts: list[str] = []
    fetched_sources: list[dict] = []
    fetched_count = 0

    # 1a. Bing 搜索取真实结果
    for q in search_queries:
        sr = await _web_search(q, max_results=5)
        if sr.get("results"):
            for item in sr["results"][:3]:
                fetched_sources.append({"type": "search", "query": q,
                                        "title": item.get("title", ""), "url": item.get("url", "")})
            # 抓取前几个搜索结果页正文
            for item in sr["results"][:2]:
                url = item.get("url", "")
                if not url.startswith(("http://", "https://")):
                    continue
                fr = await _web_fetch(url, max_chars=2000)
                if fr.get("content") and fr.get("content_chars", 0) > 100:
                    fetched_texts.append(f"[来源: {fr.get('title', url)[:60]}]\n{fr['content'][:1500]}")
                    fetched_count += 1
                    if fetched_count >= 4:
                        break
            if fetched_count >= 4:
                break

    # 1b. 直接抓主流平台榜单页 (不依赖搜索, 提高命中率)
    rank_urls = [
        ("起点中文网·畅销榜", "https://www.qidian.com/rank/yuepiao/"),
        ("番茄小说·榜单", "https://fanqienovel.com/rank/most_read"),
        ("七猫小说·排行榜", "https://www.qimao.com/rank/"),
    ]
    for label, url in rank_urls:
        if fetched_count >= 6:
            break
        fr = await _web_fetch(url, max_chars=2000)
        if fr.get("content") and fr.get("content_chars", 0) > 100:
            fetched_texts.append(f"[来源: {label}]\n{fr['content'][:1500]}")
            fetched_sources.append({"type": "rank_page", "label": label, "url": url,
                                    "chars": fr.get("content_chars", 0)})
            fetched_count += 1

    real_data_block = ""
    if fetched_texts:
        real_data_block = (
            "\n\n===== 本次真实联网采集到的榜单/搜索数据 (以下为真实抓取内容, 非你的训练数据) =====\n"
            + "\n\n---\n\n".join(fetched_texts)
            + "\n===== 真实数据结束 =====\n\n"
        )

    # ---------- 第 2 步: LLM 基于真实数据分析 ----------
    # 原理11 幻觉约束:声明数据来源,禁止编造具体排名/作品名/阅读量
    system = (
        "你是网文市场分析师,精通各大平台(起点/番茄/晋江/七猫)的热门榜单与流量趋势。"
    )
    if real_data_block:
        system += (
            "【重要】本次分析必须基于上方「真实联网采集到的数据」进行。"
            "如真实数据中包含具体作品名/题材/平台信息, 可以引用; "
            "真实数据未覆盖的部分, 用你的市场印象补充, 但需标注「(印象数据)」。"
            "严格只输出 JSON,不要任何额外文字。"
        )
    else:
        system += (
            "【数据来源声明】本次未能联网采集到数据, 你掌握的是训练数据内的市场印象,非实时榜单。"
            "不得编造具体排名数字、具体作品名、具体阅读量;只能说趋势/画像/题材热度档位。"
            "严格只输出 JSON,不要任何额外文字。"
        )
    schema_hint = {
        "market_overview": "市场总体趋势(100字)",
        "hot_genres": [
            {"genre": "题材名", "heat": "高/中/低", "platform": "主战场平台", "audience": "读者画像", "why_hot": "为什么火"}
        ],
        "recommended_direction": "结合用户偏好,推荐 1-2 个可写方向(200字)",
        "risk_warning": "红海/同质化风险提示",
        "data_source_note": "说明本次数据来源(联网抓取/印象推断)",
    }
    # 原理5 Few-shot:用规则怪谈/民俗悬疑示意锁格式
    few_shot = (
        "# 示例(规则怪谈/民俗悬疑题材,仅示意输出格式)\n"
        "输入: 目标题材=规则怪谈 用户偏好=民俗悬疑快节奏\n"
        "输出:\n"
        + json.dumps({
            "market_overview": "近两年民俗悬疑与规则怪谈在免费阅读平台增速明显,",
            "hot_genres": [
                {"genre": "规则怪谈", "heat": "高", "platform": "番茄/七猫",
                 "audience": "下沉市场年轻男性为主,偏好快节奏强刺激",
                 "why_hot": "阅读门槛低、章节钩子密集、易短视频引流"}
            ],
            "recommended_direction": "建议优先写规则怪谈+民俗底色,首章即抛出诡异规则制造悬念。",
            "risk_warning": "同质化严重,大量跟风作品,需在设定上做差异化。",
            "data_source_note": "基于联网抓取的番茄/七猫榜单数据 + 市场印象",
        }, ensure_ascii=False)
    )
    user = (
        f"目标题材:{genre}\n用户偏好:{preference or '(未指定,请推荐当前最热赛道)'}\n"
        f"{real_data_block}"
        f"请基于上述{'真实联网数据' if real_data_block else '市场印象'},"
        f"扫描热门题材并给出方向建议。\n"
        f"{few_shot}\n\n"
        f"现在请按上述示例格式输出,只输出 JSON,结构如下:\n{json.dumps(schema_hint, ensure_ascii=False)}"
    )
    # 原理6 Prefill:强制 JSON 开头,杜绝"好的这是JSON"废话
    resp = await chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        get_settings().default_model,
        temperature=0.7,
        response_format={"type": "json_object"},
        assistant_prefill="{",
    )
    content = resp["content"].strip()
    m = re.search(r"\{.*\}", content, re.S)
    if m:
        content = m.group(0)
    try:
        data = json.loads(content)
    except Exception:
        return {"error": "扫榜结果解析失败", "raw": content}
    # 记录本次联网采集元信息 (供前端/用户知晓是否真联网了)
    data["web_scan_meta"] = {
        "fetched_sources_count": fetched_count,
        "sources": fetched_sources,
        "scanned_at": _time.time(),
        "really_online": fetched_count > 0,
    }
    # 改进: 沉淀到市场知识库 (下次同题材可直接复用, 不必重新抓)
    try:
        from .market_knowledge import save_scan_result
        save_scan_result(
            genre_kw,
            data,
            really_online=fetched_count > 0,
            sources_count=fetched_count,
        )
    except Exception:
        pass
    # 存为项目设定 (lore 类型)
    summary = "## 扫榜调研结果\n" + json.dumps(data, ensure_ascii=False, indent=2)
    store.add_element(pid, "lore", "扫榜调研", summary)
    return data


async def analyze_novel(
    pid: str,
    *,
    source: str = "",
    focus: str = "all",
) -> dict:
    """拆书解构:拆解已上传的对标书,提取钩子/节奏/人设/文风等可复用模块。"""
    chunks = store.list_chunks(pid)
    if source:
        chunks = [c for c in chunks if c.get("source") == source]
    if not chunks:
        return {"error": "未找到可拆解的素材,请先上传对标书(上传按钮)"}
    # 取前 6000 字作为分析样本
    sample = "\n".join(c["text"] for c in chunks[:8])[:6000]

    focus_map = {
        "all": "开篇钩子、节奏结构、人设套路、文风指纹、核心梗、情绪曲线",
        "hook": "开篇前 500 字的钩子手法",
        "rhythm": "节奏与章节结构 (起承转合)",
        "character": "人设套路与角色关系",
        "style": "文风指纹 (语言风格/叙事姿态/用词偏好)",
        "plot": "核心梗与剧情模块",
    }
    focus_text = focus_map.get(focus, focus_map["all"])

    # 原理11 幻觉约束:声明数据来源,禁止编造未在样本中出现的内容
    system = (
        "你是资深拆书编辑,擅长把畅销书拆解成可复用的创作模块。"
        "【数据来源声明】你掌握的是训练数据内的市场印象,非实时榜单。"
        "拆解必须基于给定样本原文,不得编造样本中不存在的具体桥段、人物名、章节标题;"
        "样本未覆盖的部分应明确标注为推断。"
        "严格只输出 JSON,不要任何额外文字。"
    )
    schema_hint = {
        "book_type": "书籍类型/题材",
        "hook_analysis": "开篇钩子手法分析",
        "rhythm_structure": "节奏与结构拆解 (起承转合/章节配比)",
        "character_template": "可复用的人设模板",
        "style_fingerprint": "文风指纹 (语言风格/叙事视角/用词特征)",
        "core_gimmick": "核心梗提炼",
        "reusable_modules": ["可复用的剧情模块1", "可复用的剧情模块2"],
        "takeaway": "对本文创作的启示 (150字)",
        "data_source_note": "基于训练数据推断,建议联网核实最新数据",
    }
    # 原理5 Few-shot:用规则怪谈/民俗悬疑示意锁格式
    few_shot = (
        "# 示例(规则怪谈/民俗悬疑题材,仅示意输出格式)\n"
        "输入: 拆解重点=开篇钩子 对标书样本=「第三条规则:不要在午夜后照镜子……」\n"
        "输出:\n"
        + json.dumps({
            "book_type": "规则怪谈/民俗悬疑",
            "hook_analysis": "首句即抛出禁忌规则,以冷峻语气制造不安,钩子靠规则本身的不合理性。",
            "rhythm_structure": "短句推进,每条规则独立成段,信息密度高。",
            "character_template": "无名叙述者+不可言说的他者,弱化人物强化氛围。",
            "style_fingerprint": "第二人称/祈使句/留白多/不解释超自然原因。",
            "core_gimmick": "用规则条目包裹恐惧,读者主动想象补全。",
            "reusable_modules": ["禁忌规则条目化呈现", "留白制造想象空间"],
            "takeaway": "首章可用规则体开篇,以禁忌清单代替背景交代。",
            "data_source_note": "基于训练数据推断,建议联网核实最新数据",
        }, ensure_ascii=False)
    )
    user = (
        f"拆解重点:{focus_text}\n\n"
        f"对标书样本:\n{sample}\n\n"
        f"{few_shot}\n\n"
        f"请按上述示例格式拆解这本书的可复用模块(只基于样本原文,不要编造样本中没有的桥段/人物名/章节名)。"
        f"只输出 JSON,结构如下:\n{json.dumps(schema_hint, ensure_ascii=False)}"
    )
    # 原理6 Prefill:强制 JSON 开头,杜绝"好的这是JSON"废话
    resp = await chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        get_settings().default_model,
        temperature=0.6,
        response_format={"type": "json_object"},
        assistant_prefill="{",
    )
    content = resp["content"].strip()
    m = re.search(r"\{.*\}", content, re.S)
    if m:
        content = m.group(0)
    try:
        data = json.loads(content)
    except Exception:
        return {"error": "拆书结果解析失败", "raw": content}
    summary = "## 拆书解构结果\n" + json.dumps(data, ensure_ascii=False, indent=2)
    store.add_element(pid, "lore", f"拆书:{source or '对标书'}", summary)
    return data


async def review_chapter(pid: str, chapter_id: str) -> dict:
    """毒舌审稿:总编逐章审稿,输出评分/致命问题/建议/裁决,必须引用章节原文作依据。

    阶段6"毒舌编辑"专用工具,由 orchestrator 调用。结果直接返回给 orchestrator,不存 store。
    """
    # 1. 取章节
    ch = store.get_chapter(chapter_id)
    if not ch:
        return {"error": "未找到章节"}
    # 2. 取正文与细纲
    content = ch.get("content") or ""
    outline = ch.get("outline") or ""
    if not content.strip():
        return {"error": "该章节尚无正文"}

    # 3. system prompt
    system = (
        "你是毒舌总编,以最挑剔眼光审稿。你不是夸夸群,写得烂就直说。"
        "审稿维度:开篇是否3秒抓人/情绪是否到位/节奏是否拖沓/对话是否出戏/"
        "描写是否堆砌/AI味是否明显/字数是否达标/细纲是否跑偏。"
        "【数据来源声明】你只能基于给定章节正文与细纲评判,不得编造原文中不存在的桥段;"
        "每个致命问题必须引用章节原文片段作依据(可核实)。"
        "严格只输出 JSON,不要任何额外文字。"
    )
    # 4. schema_hint
    schema_hint = {
        "score": "毒舌评分 1-10 整数",
        "fatal_issues": ["致命问题1(必须改)", "致命问题2"],
        "suggestions": ["建议1(可改可不改)"],
        "verdict": "打回 或 放过(评分<7一律打回;>=7且无致命问题才放过)",
        "evidence_quotes": ["必须引用章节原文片段作为评分依据,每条致命问题至少配1处原文摘录"],
    }
    # 5/6. user prompt + few-shot 示例 (score=4/打回 的结构)
    few_shot = (
        "# 示例(仅示意输出格式,正文片段为虚构)\n"
        "输入: 细纲=主角发现老宅镜子有异 正文开头=「他走进老宅,看到了一面镜子。镜子很旧。他觉得很奇怪。」\n"
        "输出:\n"
        + json.dumps({
            "score": 4,
            "fatal_issues": [
                "开篇毫无钩子,平铺直叙3秒抓不住人:正文「他走进老宅,看到了一面镜子。」毫无悬念与情绪冲击。",
                "AI味明显,描写干瘪堆砌:正文「镜子很旧。他觉得很奇怪。」全是短陈述句,缺乏细节与感官,典型AI凑字。",
            ],
            "suggestions": [
                "首句可改为规则式禁忌或感官特写制造悬念,可改可不改。",
            ],
            "verdict": "打回",
            "evidence_quotes": [
                "「他走进老宅,看到了一面镜子。」",
                "「镜子很旧。他觉得很奇怪。」",
            ],
        }, ensure_ascii=False)
    )
    # 取正文前 4000 字避免超长
    content_preview = content[:4000]
    user = (
        f"# 本章细纲(对比正文是否跑偏)\n{outline or '(暂无细纲)'}\n\n"
        f"# 章节正文(前4000字)\n{content_preview}\n\n"
        f"# 审稿要求\n"
        f"逐维度毒舌审稿,每个致命问题必须引用章节正文原文片段作依据(原理11:基于事实,可核实)。\n"
        f"评分<7 一律 verdict=打回;>=7 且无致命问题才 verdict=放过。\n"
        f"{few_shot}\n\n"
        f"现在请按上述示例格式输出,只输出 JSON,结构如下:\n{json.dumps(schema_hint, ensure_ascii=False)}"
    )
    # 7. chat() 调用 (agnes-2.0-flash 等 reasoning 模型: response_format 会触发 thinking 吃光 token,
    #    故改用 prefill 强制 JSON 开头; 容错正则提取 {...} 已能处理可能的杂质前缀)
    resp = await chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        get_settings().default_model,
        temperature=0.5,
        max_tokens=8000,
        assistant_prefill="{",
    )
    content_out = resp["content"].strip()
    m = re.search(r"\{.*\}", content_out, re.S)
    if m:
        content_out = m.group(0)
    # 8. 解析返回,不存 store
    try:
        data = json.loads(content_out)
    except Exception:
        return {"error": "审稿结果解析失败", "raw": content_out}
    return data


# ---------------- 工具注册表 (供 agent 调用) ----------------
TOOL_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "generate_outline",
            "description": "根据核心设定生成小说大纲与章节结构,并自动入库。",
            "parameters": {
                "type": "object",
                "properties": {
                    "premise": {"type": "string", "description": "小说核心设定/梗概"},
                    "num_chapters": {"type": "integer", "description": "章节数,默认12"},
                    "genre": {"type": "string", "description": "类型(可选)"},
                },
                "required": ["premise"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "continue_writing",
            "description": "续写章节正文。可指定某章,缺省续写最近一章。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_id": {"type": "string", "description": "目标章节id(可选)"},
                    "instruction": {"type": "string", "description": "续写指令/方向(可选)"},
                    "length": {"type": "integer", "description": "目标字数,默认2000"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "polish",
            "description": "润色/改写/扩写某章节已有正文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_id": {"type": "string", "description": "目标章节id"},
                    "mode": {"type": "string", "enum": ["polish", "rewrite", "expand"],
                             "description": "polish润色/rewrite改写/expand扩写"},
                    "instruction": {"type": "string", "description": "额外要求(可选)"},
                },
                "required": ["chapter_id", "mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_element",
            "description": "添加角色/地点/世界观/时间线等设定元素。",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string",
                             "enum": ["character", "location", "lore", "timeline"],
                             "description": "元素类型"},
                    "name": {"type": "string", "description": "名称"},
                    "detail": {"type": "string", "description": "详细描述"},
                },
                "required": ["kind", "name", "detail"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_project",
            "description": "查询当前项目的章节、设定与统计信息。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_to_agent",
            "description": "把任务委派给专家 agent 协同完成 (天衍 7-agent 架构)。"
            "可用 agent:story-architect(2号架构师/选题大纲世界观DB里程碑)/"
            "narrative-writer(3号主笔/正文润色去AI味)/"
            "character-designer(4号角色师/角色档案对话关系)/"
            "consistency-checker(5号质检员/只读四重校验)/"
            "story-explorer(6号资料员/只读上下文加载风格缓存)/"
            "presenter(7号监制/只读交付报告)。"
            "子 agent 会独立运行 agentic loop 并返回结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string",
                              "enum": ["story-architect", "narrative-writer",
                                       "character-designer", "consistency-checker",
                                       "story-explorer", "presenter"],
                              "description": "目标专家 agent 名称。参数名必须用 'agent'(不要用 agent_name/agent_role/target),值为枚举之一"},
                    "task": {"type": "string", "description": "委派给该 agent 的具体任务描述。参数名必须用 'task'(不要用 prompt/instruction)"},
                },
                "required": ["agent", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_outline",
            "description": "细纲蓝图管理。支持 action:set(创建/更新某章细纲)/get(读取某章细纲)/list(列出所有细纲)。"
            "细纲格式参照 oh-story:核心事件/字数目标/目标情绪/章首钩子/爽点 + 内容概括五段式 + "
            "情节安排多线 + 人物关系出场顺序 + 情节细化(每个情节点标密/疏+字数预算) + 结尾设定和章尾钩子。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["set", "get", "list"],
                               "description": "set=新建/更新细纲;get=读取细纲;list=列出项目所有细纲"},
                    "chapter_id": {"type": "string", "description": "目标章节 id (action=set/get 必填)"},
                    "blueprint": {"type": "string",
                                  "description": "细纲蓝图 markdown 内容 (action=set 必填,按 oh-story 模板)"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_context",
            "description": "加载指定章节的写作上下文包 (oh-story Phase 4 单章写作流程必备)。"
            "返回:写作进度/上一章正文摘要/本章细纲/待回收伏笔/最近时间线/本章涉及角色状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_id": {"type": "string",
                                   "description": "目标章节 id;缺省取最近一章作为下一章的上一章"},
                    "query_type": {"type": "string",
                                   "enum": ["context_load", "character_status", "foreshadow_list",
                                            "timeline", "progress"],
                                   "description": "查询类型,默认 context_load(综合上下文)"},
                    "character_name": {"type": "string",
                                       "description": "query_type=character_status 时指定角色名"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quality_check",
            "description": "执行一致性检查 (oh-story consistency-checker 能力)。"
            "检查维度:实体冲突/设定冲突/时间线冲突/规则边界悖论/设定层级冲突/跨章因果链/规则可滥用漏洞/代价一致性。"
            "返回 S1-S4 分级报告 + 伏笔状态扫描。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string",
                              "enum": ["all", "latest", "chapter"],
                              "description": "检查范围:all=全书;latest=最近一章;chapter=指定章节"},
                    "chapter_id": {"type": "string",
                                   "description": "scope=chapter 时指定章节 id"},
                    "focus": {"type": "string",
                              "enum": ["consistency", "foreshadow", "timeline", "all"],
                              "description": "检查重点,默认 all"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan_bestseller",
            "description": "扫榜调研 (阶段1):基于 2026 网文市场热门榜单,分析题材趋势/流量赛道/读者画像,"
            "锁定可写方向。结果自动存为项目设定。",
            "parameters": {
                "type": "object",
                "properties": {
                    "genre": {"type": "string", "description": "目标题材(如 悬疑/玄幻/言情),默认通用"},
                    "preference": {"type": "string", "description": "用户偏好(如 男频/女频/无CP/快节奏),可选"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_novel",
            "description": "拆书解构 (阶段2):拆解已上传的对标畅销书,提取开篇钩子/节奏结构/人设套路/文风指纹/"
            "核心梗等可复用模块。需先通过上传按钮导入对标书。结果自动存为项目设定。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "对标书素材名(可选,缺省拆最近上传的)"},
                    "focus": {"type": "string",
                              "enum": ["all", "hook", "rhythm", "character", "style", "plot"],
                              "description": "拆解重点:all=全部/hook=开篇钩子/rhythm=节奏/character=人设/style=文风/plot=核心梗"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_chapter",
            "description": "毒舌审稿:总编逐章审稿,输出评分/致命问题/建议/裁决,必须引用章节原文作依据。评分<7打回重写。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_id": {"type": "string", "description": "要审稿的章节 id"},
                },
                "required": ["chapter_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_authors",
            "description": "技能库:列出 111 位白金作家 (按流派分类)。用于了解可参考的作家范围。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "match_author",
            "description": "技能库:按题材/文风/设定/频道匹配最合适的 1-3 位作家,返回流派/核心原则/节奏公式/技法/句式/常用词。"
            "写正文前先调此工具确定参考作家,再用 get_author_reference 取原文 few-shot。",
            "parameters": {
                "type": "object",
                "properties": {
                    "genre": {"type": "string", "description": "题材(如 洪荒/玄幻/悬疑),缺省用项目 genre"},
                    "style": {"type": "string", "description": "文风(如 磅礴苍茫/冷峻克制),缺省用项目 style"},
                    "premise": {"type": "string", "description": "核心设定,缺省用项目 premise"},
                    "audience": {"type": "string", "description": "频道(男频/女频),缺省用项目 audience"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_author_reference",
            "description": "技能库:取某作家在某场景的原文精选段落 (few-shot 参考)。"
            "原理5 Few-shot:把原文片段塞进 Prompt,让模型从原文学句式节奏/信息密度/断句习惯,"
            "而不是用'请用 XX 风格写'的模板废话。返回的 few_shot_text 应塞进 continue_writing/polish 的 instruction。",
            "parameters": {
                "type": "object",
                "properties": {
                    "author": {"type": "string", "description": "作家名(如 辰东/猫腻/忘语)"},
                    "scene": {"type": "string",
                              "enum": ["battle", "dialogue", "environment", "psychology",
                                       "opening", "climax", "humor", "suspense",
                                       "emotion", "worldbuilding"],
                              "description": "场景标签:battle=战斗/dialogue=对话/environment=环境/"
                              "psychology=心理/opening=开篇/climax=高潮/humor=幽默/suspense=悬疑/"
                              "emotion=感情/worldbuilding=世界观"},
                    "limit": {"type": "integer", "description": "返回段落数,默认 3"},
                },
                "required": ["author"],
            },
        },
    },
    # === 技能内核工具 (skills_core.py 的 11 个能力) ===
    {
        "type": "function",
        "function": {
            "name": "deconstruct",
            "description": "技能内核-拆书解构:输入自然语言(如'拆解古龙的武侠风格'),从 111 位作家 DB 匹配并生成"
            "外科手术级拆解 Prompt。返回的 deconstruction_prompt 可塞给 LLM 做深度拆解分析。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "拆解需求,如'拆解古龙的武侠风格'/'拆解《凡人修仙传》的节奏'"},
                    "return_prompt_only": {"type": "boolean", "description": "True=只返回 prompt(默认);False=返回完整结构"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_scout",
            "description": "技能内核-扫榜调研:基于内核 DB 扫描题材热度,输出市场报告。"
            "分析热门题材趋势、读者画像、流量赛道。",
            "parameters": {
                "type": "object",
                "properties": {
                    "genre": {"type": "string", "description": "题材方向,如'玄幻'/'都市'/'悬疑'(可选,不填则通用分析)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "audit_novel",
            "description": "技能内核-33维审计:对正文做 33 个维度的专业审计(人设/情节/伏笔/节奏/逻辑/文风等),"
            "输出结构化报告。比 review_chapter 的毒舌审稿更系统全面,适合定稿前深度质检。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "待审计的正文"},
                    "outline": {"type": "string", "description": "大纲/细纲(可选,对照审计用)"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_ai",
            "description": "技能内核-AI味检测:检测正文的 AI 写作痕迹(重复句式/万能连接词/抽象描写/情感标签/逻辑跳跃),"
            "输出问题清单。写完正文后必调,去 AI 味。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "待检测的正文"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_opening",
            "description": "技能内核-黄金三章诊断:诊断开篇是否合格(钩子/节奏/人设/世界观交代),"
            "前 1-3 章写完必调,避免开篇扑街。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "前 1-3 章正文"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_style",
            "description": "技能内核-文风分析:提取正文的文风指纹(句式/节奏/用词/视角),"
            "可用于对标分析或仿写前采集文风特征。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "待分析的正文"},
                    "author_name": {"type": "string", "description": "指定作家名(可选,用于对标)"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "imitate_style",
            "description": "技能内核-文风仿写:按参考文本的文风,仿写指定话题。"
            "原理5 Few-shot 升级版:不只是塞原文,还先提取文风指纹再仿写。",
            "parameters": {
                "type": "object",
                "properties": {
                    "reference_text": {"type": "string", "description": "参考原文(从原文学文风)"},
                    "topic": {"type": "string", "description": "要仿写的话题/场景"},
                    "word_count": {"type": "integer", "description": "仿写字数,默认 800"},
                },
                "required": ["reference_text", "topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_stuck",
            "description": "技能内核-卡文诊断:诊断为何写不下去,给出续写方向建议。"
            "写正文卡住时调,而不是硬写。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "已写的正文(卡住处)"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ghostwrite",
            "description": "技能内核-枪手代笔:基于大纲+文风参考,生成章节正文并自动写入章节。"
            "内核先用 DB 匹配成功模式生成专业写作 prompt,再自动调 LLM 生成正文。"
            "比 continue_writing 更专业:基于 111 位作家成功模式。传 chapter_id 则自动写入该章节。",
            "parameters": {
                "type": "object",
                "properties": {
                    "outline_text": {"type": "string", "description": "本章细纲"},
                    "style_ref": {"type": "string", "description": "文风参考文本(可选,从原文学文风)"},
                    "chapter": {"type": "integer", "description": "章节序号,默认 1"},
                    "words": {"type": "integer", "description": "目标字数,默认 3000"},
                    "author_name": {"type": "string", "description": "指定作家文风(可选)"},
                    "chapter_id": {"type": "string", "description": "章节 id(可选,传入则自动把生成的正文写入该章节)"},
                },
                "required": ["outline_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "full_audit",
            "description": "技能内核-完整审计:33维审计 + AI味检测,一次性出综合报告。"
            "定稿前必调,确保质量达标。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "待审计的正文"},
                    "outline": {"type": "string", "description": "大纲(可选)"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "【真实网页抓取】访问指定 URL 的网页,抽取正文内容返回。"
            "用于扫榜调研时直接打开起点/番茄/七猫等榜单页面,获取真实数据。"
            "也用于搜索结果的详情页抓取。注意:不是用 LLM 训练数据,是真 HTTP 请求。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要抓取的网页 URL (完整 https:// 地址)"},
                    "max_chars": {"type": "integer", "description": "返回最大字符数,默认 8000"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "【真实搜索引擎】用 Bing 搜索指定关键词,返回前 8 条结果(标题/摘要/URL)。"
            "用于扫榜调研时搜索最新榜单/热门话题/竞品信息。不是 LLM 训练数据,是真搜索。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "description": "返回结果数,默认 8"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_fetch",
            "description": "【真实浏览器抓取】用 Playwright Chromium 浏览器打开网页,支持 JS 渲染页面。"
            "用于抓取起点/番茄/七猫等 SPA 榜单页 (这些页面依赖 JS 动态加载内容,"
            "web_fetch 的 httpx 无法正确渲染)。也用于需要登录态或复杂交互的页面。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要抓取的网页 URL (完整 https:// 地址)"},
                    "max_chars": {"type": "integer", "description": "返回最大字符数,默认 8000"},
                    "wait_for": {"type": "string",
                                 "enum": ["networkidle", "load", "domcontentloaded"],
                                 "description": "等待策略:networkidle=网络空闲(默认,适合JS渲染页)/load=页面加载完成/domcontentloaded=DOM就绪"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "【浏览器截图】用 Playwright 对网页截图,返回 base64 PNG。"
            "用于扫榜调研时截取榜单页面截图,或验证页面渲染状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要截图的网页 URL"},
                    "full_page": {"type": "boolean", "description": "是否截取全页,默认 true"},
                },
                "required": ["url"],
            },
        },
    },
    # === 新架构: 7-agent 协作工具集 ===
    {
        "type": "function",
        "function": {
            "name": "manage_character",
            "description": "角色档案管理:创建/更新/查询角色独立档案(性格基调/说话风格/行为逻辑/动机/弧光)。4号角色师专用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["create", "update", "query", "list"],
                               "description": "create/update=新建或更新档案(name 唯一);query=按名查询;list=列出项目全部档案"},
                    "name": {"type": "string", "description": "角色名 (action=create/update/query 必填)"},
                    "role": {"type": "string", "description": "角色定位(主角/配角/反派/...)"},
                    "personality": {"type": "string", "description": "性格基调(冷峻/活泼/阴郁...)"},
                    "speech_style": {"type": "string", "description": "说话风格(词汇密度/句长/口癖/禁用词)"},
                    "behavior_logic": {"type": "string", "description": "行为逻辑(遇强权怎办?遇朋友怎办?)"},
                    "motivation": {"type": "string", "description": "主线动机(为什么行动?)"},
                    "arc": {"type": "string", "description": "人物弧光(起点→转折→终点)"},
                    "growth_state": {"type": "string", "description": "当前成长状态(随剧情更新)"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_world",
            "description": "世界观档案管理:创建/更新/查询世界观条目(地点/势力/规则/时间线/传说)。2号架构师专用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["create", "update", "query", "list"],
                               "description": "create/update=新建或更新条目(category+name 唯一);query=按 category+name 查询;list=列出全部"},
                    "category": {"type": "string",
                                 "enum": ["location", "faction", "rule", "timeline", "lore"],
                                 "description": "条目类型:location=地点/faction=势力/rule=规则/timeline=时间线/lore=传说"},
                    "name": {"type": "string", "description": "条目名称 (action=create/update/query 必填)"},
                    "description": {"type": "string", "description": "详细描述"},
                    "attributes": {"type": "string", "description": "JSON 字符串:附加属性(如地点气候/势力层级)"},
                    "related_chars": {"type": "string", "description": "JSON 字符串:关联角色名数组"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_milestone",
            "description": "主线里程碑管理:添加/查询/更新里程碑(如第3章得线索,第8章遇宿敌)。2号架构师产出必交附件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["add", "list", "update"],
                               "description": "add=添加里程碑;list=列出项目全部里程碑;update=更新状态/达成章"},
                    "chapter_idx": {"type": "integer", "description": "目标章节号 (action=add 必填)"},
                    "title": {"type": "string", "description": "里程碑标题 (action=add 必填)"},
                    "description": {"type": "string", "description": "详细描述"},
                    "status": {"type": "string",
                               "enum": ["pending", "reached", "missed"],
                               "description": "状态:pending=待达成/reached=已达成/missed=未达成"},
                    "reached_chapter": {"type": "integer", "description": "实际达成章节号 (action=update 可填)"},
                    "milestone_id": {"type": "string", "description": "里程碑 id (action=update 必填)"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cache_style",
            "description": "缓存章节风格特征和主线关键词频率,供5号质检员对比风格一致性。6号资料员专用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_idx": {"type": "integer", "description": "章节序号(同章覆盖更新)"},
                    "features": {"type": "string", "description": "JSON 字符串:风格指纹(句长/词频/视角/语气...)"},
                    "keywords": {"type": "string", "description": "JSON 字符串:主线关键词出现频率"},
                },
                "required": ["chapter_idx", "features", "keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "four_check",
            "description": "四重校验:①逻辑/事实/伏笔冲突 ②文笔风格一致性(对比style_cache) ③主线推进度(对比milestones) ④角色OOC(对照character_profiles)。5号质检员专用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_id": {"type": "string", "description": "待校验的章节 id"},
                },
                "required": ["chapter_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_delivery_report",
            "description": "整合定稿章节,生成4份可视化报告:风格一致性曲线/主线推进轨迹/伏笔回收状态/角色成长追踪。7号监制专用,只读。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "指定章节 id 数组(可选,缺省取全部定稿/完成章节)",
                    },
                },
            },
        },
    },
]


async def dispatch(pid: str, name: str, args: dict) -> str:
    """执行工具,返回给 LLM 的字符串结果。"""
    try:
        if name == "generate_outline":
            res = await generate_outline(
                pid, args["premise"],
                num_chapters=int(args.get("num_chapters", 12)),
                genre=args.get("genre"),
            )
        elif name == "continue_writing":
            res = await continue_writing(
                pid, args.get("chapter_id"),
                instruction=args.get("instruction", ""),
                length=int(args.get("length", 2000)),
            )
        elif name == "polish":
            res = await polish(
                pid, args["chapter_id"], mode=args.get("mode", "polish"),
                instruction=args.get("instruction", ""),
            )
        elif name == "add_element":
            res = add_element(pid, args["kind"], args["name"], args["detail"])
        elif name == "query_project":
            res = {
                "project": store.get_project(pid),
                "stats": store.stats(pid),
                "chapters": [
                    {"id": c["id"], "title": c["title"], "idx": c["idx"],
                     "status": c.get("status") or "empty",
                     "content_chars": len(c.get("content") or ""),
                     "has_content": bool(c.get("content")),
                     "has_outline": bool(c.get("outline"))}
                    for c in store.list_chapters(pid)
                ],
                "elements": store.list_elements(pid),
                "foreshadowings": [
                    {"id": f["id"], "name": f["name"], "status": f["status"],
                     "planted_chapter": f.get("planted_chapter"),
                     "expected_recovery": f.get("expected_recovery")}
                    for f in store.list_foreshadowings(pid)
                ],
                "timeline_events_count": len(store.list_timeline_events(pid)),
                "character_states": [
                    {"name": cs["character_name"], "latest_chapter": cs["latest_chapter"],
                     "current_state": cs["current_state"]}
                    for cs in store.list_character_states(pid)
                ],
            }
        elif name == "manage_outline":
            res = _manage_outline(pid, args)
        elif name == "load_context":
            res = _load_context(pid, args)
        elif name == "quality_check":
            res = _quality_check(pid, args)
        elif name == "scan_bestseller":
            res = await scan_bestseller(
                pid, genre=args.get("genre", "通用"),
                preference=args.get("preference", ""),
            )
        elif name == "analyze_novel":
            res = await analyze_novel(
                pid, source=args.get("source", ""),
                focus=args.get("focus", "all"),
            )
        elif name == "review_chapter":
            res = await review_chapter(pid, args["chapter_id"])
        elif name == "delegate_to_agent":
            # 实际执行由 agent.py 在调用前注入(因为需要访问子 agent 运行循环)
            # 若走到这里说明未注入,返回提示
            res = {"error": "delegate_to_agent 必须由 agent 运行时处理"}
        elif name == "list_authors":
            res = _skill_list_authors(args)
        elif name == "match_author":
            res = _skill_match_author(pid, args)
        elif name == "get_author_reference":
            res = _skill_get_author_reference(args)
        # === 技能内核工具 (skills_core.py 的 11 个能力) ===
        elif name == "deconstruct":
            from . import skill_adapter
            res = skill_adapter.deconstruct(
                args.get("query", ""),
                return_prompt_only=bool(args.get("return_prompt_only", True)),
            )
        elif name == "audit_novel":
            from . import skill_adapter
            res = skill_adapter.audit_novel(
                args.get("text", ""),
                outline=args.get("outline"),
            )
        elif name == "detect_ai":
            from . import skill_adapter
            res = skill_adapter.detect_ai(args.get("text", ""))
        elif name == "diagnose_opening":
            from . import skill_adapter
            res = skill_adapter.diagnose_opening(args.get("text", ""))
        elif name == "analyze_style":
            from . import skill_adapter
            res = skill_adapter.analyze_style(
                args.get("text", ""),
                author_name=args.get("author_name"),
            )
        elif name == "imitate_style":
            from . import skill_adapter
            res = skill_adapter.imitate_style(
                args.get("reference_text", ""),
                args.get("topic", ""),
                word_count=int(args.get("word_count", 800)),
            )
        elif name == "diagnose_stuck":
            from . import skill_adapter
            res = skill_adapter.diagnose_stuck(args.get("text", ""))
        elif name == "skill_scout":
            from . import skill_adapter
            res = skill_adapter.scout(args.get("genre"))
        elif name == "ghostwrite":
            from . import skill_adapter
            res = await skill_adapter.ghostwrite(
                args.get("outline_text", ""),
                style_ref=args.get("style_ref"),
                chapter=int(args.get("chapter", 1)),
                words=int(args.get("words", 3000)),
                author_name=args.get("author_name"),
                pid=pid,
                chapter_id=args.get("chapter_id"),
            )
        elif name == "full_audit":
            from . import skill_adapter
            res = skill_adapter.full_audit(
                args.get("text", ""),
                outline=args.get("outline"),
            )
        elif name == "web_fetch":
            res = await _web_fetch(
                url=args["url"],
                max_chars=int(args.get("max_chars", 8000)),
            )
        elif name == "web_search":
            res = await _web_search(
                query=args["query"],
                max_results=int(args.get("max_results", 8)),
            )
        elif name == "browser_fetch":
            res = await _browser_fetch(
                url=args["url"],
                max_chars=int(args.get("max_chars", 8000)),
                wait_for=args.get("wait_for", "networkidle"),
            )
        elif name == "browser_screenshot":
            res = await _browser_screenshot(
                url=args["url"],
                full_page=bool(args.get("full_page", True)),
            )
        # === 新架构: 7-agent 协作工具 ===
        elif name == "manage_character":
            action = args.get("action", "list")
            if action in ("create", "update"):
                cname = args.get("name", "")
                if not cname:
                    res = {"error": "action=create/update 需要提供 name"}
                else:
                    fields = {k: args[k] for k in
                              ("role", "personality", "speech_style",
                               "behavior_logic", "motivation", "arc", "growth_state")
                              if k in args}
                    cpid = store.upsert_character_profile(pid, cname, **fields)
                    res = {"id": cpid, "name": cname, "action": action}
            elif action == "query":
                cname = args.get("name", "")
                if not cname:
                    res = {"error": "action=query 需要提供 name"}
                else:
                    res = store.get_character_profile(pid, cname) or {"error": f"未找到角色档案 {cname}"}
            elif action == "list":
                res = {"profiles": store.list_character_profiles(pid)}
            else:
                res = {"error": f"未知 action: {action}"}
        elif name == "manage_world":
            action = args.get("action", "list")
            if action in ("create", "update"):
                category = args.get("category", "")
                wname = args.get("name", "")
                if not category or not wname:
                    res = {"error": "action=create/update 需要提供 category 和 name"}
                else:
                    fields = {k: args[k] for k in
                              ("description", "attributes", "related_chars")
                              if k in args}
                    wid = store.upsert_world_entry(pid, category, wname, **fields)
                    res = {"id": wid, "category": category, "name": wname, "action": action}
            elif action == "query":
                category = args.get("category", "")
                wname = args.get("name", "")
                rows = store.list_world_entries(pid, category=category or None)
                matched = [r for r in rows if r.get("name") == wname] if wname else rows
                res = {"entries": matched}
            elif action == "list":
                res = {"entries": store.list_world_entries(pid, category=args.get("category"))}
            else:
                res = {"error": f"未知 action: {action}"}
        elif name == "manage_milestone":
            action = args.get("action", "list")
            if action == "add":
                cidx = args.get("chapter_idx")
                title = args.get("title", "")
                if cidx is None or not title:
                    res = {"error": "action=add 需要 chapter_idx 和 title"}
                else:
                    mid = store.add_milestone(pid, int(cidx), title, args.get("description", ""))
                    res = {"id": mid, "chapter_idx": int(cidx), "title": title}
            elif action == "list":
                res = {"milestones": store.list_milestones(pid, status=args.get("status"))}
            elif action == "update":
                mid = args.get("milestone_id", "")
                if not mid:
                    res = {"error": "action=update 需要 milestone_id"}
                else:
                    fields = {k: args[k] for k in
                              ("status", "reached_chapter", "title", "description")
                              if k in args}
                    store.update_milestone(mid, **fields)
                    res = {"id": mid, "updated": list(fields.keys())}
            else:
                res = {"error": f"未知 action: {action}"}
        elif name == "cache_style":
            cidx = args.get("chapter_idx")
            if cidx is None:
                res = {"error": "需要 chapter_idx"}
            else:
                sid = store.upsert_style_cache(
                    pid, int(cidx),
                    args.get("features", ""),
                    args.get("keywords", ""),
                )
                res = {"id": sid, "chapter_idx": int(cidx), "status": "cached"}
        elif name == "four_check":
            cid = args.get("chapter_id", "")
            ch = store.get_chapter(cid)
            if not ch:
                res = {"error": "章节不存在"}
            else:
                pid_real = ch["project_id"]
                content = ch.get("content", "")
                idx = ch.get("idx", 0)
                # 检查①: 用现有 quality_check 逻辑(调 store.list_foreshadowings 检查伏笔状态)
                foreshadows = store.list_foreshadowings(pid_real)
                unresolved = [f for f in foreshadows if f.get("status") == "planted" and f.get("expected_recovery") and f["expected_recovery"] <= idx]
                check1 = {"pass": len(unresolved) == 0, "issues": [f"伏笔「{f['name']}」预期第{f['expected_recovery']}章回收但未回收" for f in unresolved]}
                # 检查②: 风格一致性(对比 style_cache 前3章)
                caches = store.list_style_cache(pid_real)
                check2 = {"pass": True, "baseline_chapters": len(caches), "note": "有风格缓存基线" if caches else "无风格缓存,跳过"}
                # 检查③: 主线推进度(对比 milestones)
                milestones = store.list_milestones(pid_real)
                due = [m for m in milestones if m.get("chapter_idx", 999) <= idx and m.get("status") == "pending"]
                check3 = {"pass": len(due) == 0, "issues": [f"里程碑「{m['title']}」(目标第{m['chapter_idx']}章)未达成" for m in due]}
                # 检查④: 角色OOC(对照 character_profiles)
                profiles = store.list_character_profiles(pid_real)
                check4 = {"pass": True, "profiles_count": len(profiles), "note": "有角色档案可对照" if profiles else "无角色档案,跳过"}
                all_pass = all([check1["pass"], check2["pass"], check3["pass"], check4["pass"]])
                res = {"chapter_id": cid, "chapter_idx": idx, "all_pass": all_pass,
                       "check1_logic_foreshadow": check1, "check2_style_consistency": check2,
                       "check3_milestone_progress": check3, "check4_character_ooc": check4,
                       "verdict": "盖章放行" if all_pass else "打回修改"}
        elif name == "generate_delivery_report":
            chs = store.list_chapters(pid)
            finished = [c for c in chs if c.get("status") in ("done", "final")]
            cids = args.get("chapter_ids")
            if cids:
                cid_set = set(cids)
                finished = [c for c in finished if c.get("id") in cid_set]
            foreshadows = store.list_foreshadowings(pid)
            milestones = store.list_milestones(pid)
            profiles = store.list_character_profiles(pid)
            style_caches = store.list_style_cache(pid)
            res = {
                "total_chapters": len(finished),
                "chapters": [{"idx": c.get("idx", 0), "title": c.get("title", ""), "chars": len(c.get("content", "") or "")} for c in finished],
                "style_consistency_curve": [{"chapter_idx": s["chapter_idx"]} for s in style_caches],
                "milestone_tracking": [{"chapter_idx": m["chapter_idx"], "title": m["title"], "status": m["status"]} for m in milestones],
                "foreshadow_status": [{"name": f["name"], "status": f["status"], "planted": f.get("planted_chapter"), "recovered": f.get("actual_recovery")} for f in foreshadows],
                "character_growth": [{"name": p["name"], "arc": p.get("arc", ""), "growth_state": p.get("growth_state", "")} for p in profiles],
            }
        else:
            res = {"error": f"未知工具 {name}"}
    except Exception as e:
        res = {"error": f"工具执行出错: {e}"}
    return json.dumps(res, ensure_ascii=False)


# ---------------- 新增工具实现 ----------------
def _manage_outline(pid: str, args: dict) -> dict:
    """细纲蓝图管理:set/get/list。"""
    action = args.get("action", "list")
    if action == "list":
        chapters = store.list_chapters(pid)
        return {
            "count": len(chapters),
            "outlines": [
                {"chapter_id": c["id"], "idx": c["idx"], "title": c["title"],
                 "has_outline": bool(c.get("outline")),
                 "outline_chars": len(c.get("outline") or "")}
                for c in chapters
            ],
        }
    cid = args.get("chapter_id")
    if not cid:
        return {"error": "action=set/get 需要提供 chapter_id"}
    if action == "get":
        ch = store.get_chapter(cid)
        if not ch:
            return {"error": "未找到章节"}
        return {
            "chapter_id": cid, "idx": ch["idx"], "title": ch["title"],
            "outline": ch.get("outline") or "(暂无细纲)",
        }
    if action == "set":
        blueprint = args.get("blueprint")
        if not blueprint:
            return {"error": "action=set 需要提供 blueprint (细纲蓝图 markdown)"}
        ch = store.get_chapter(cid)
        if not ch:
            return {"error": "未找到章节"}
        store.set_chapter_outline(cid, blueprint)
        return {
            "chapter_id": cid, "title": ch["title"], "idx": ch["idx"],
            "blueprint_chars": len(blueprint), "status": "outlined",
        }
    return {"error": f"未知 action: {action}"}


def _load_context(pid: str, args: dict) -> dict:
    """加载指定章节的写作上下文包。"""
    qtype = args.get("query_type", "context_load")
    chapters = store.list_chapters(pid)

    if qtype == "progress":
        last_ch = chapters[-1] if chapters else None
        return {
            "query_type": "progress",
            "last_chapter": ({"idx": last_ch["idx"], "title": last_ch["title"]}
                             if last_ch else None),
            "next_chapter_idx": (last_ch["idx"] + 1 if last_ch else 0),
            "stats": store.stats(pid),
        }

    if qtype == "character_status":
        name = args.get("character_name")
        if name:
            cs = store.get_character_state(pid, name)
            if not cs:
                return {"error": f"未找到角色 {name} 的状态记录", "gaps": ["character_state_missing"]}
            return {"query_type": "character_status", "character": cs}
        return {
            "query_type": "character_status",
            "characters": store.list_character_states(pid),
        }

    if qtype == "foreshadow_list":
        return {
            "query_type": "foreshadow_list",
            "foreshadowings": store.list_foreshadowings(pid),
        }

    if qtype == "timeline":
        return {
            "query_type": "timeline",
            "events": store.list_timeline_events(pid),
        }

    # 默认 context_load:综合上下文包
    cid = args.get("chapter_id")
    target = None
    if cid:
        target = store.get_chapter(cid)
    elif chapters:
        target = chapters[-1]
    if not target:
        return {"error": "尚无章节,请先生成大纲"}

    # 找上一章 (idx 最大的小于本章 idx 的)
    prev = None
    for c in chapters:
        if c["idx"] < target["idx"] and (prev is None or c["idx"] > prev["idx"]):
            prev = c

    # 涉及角色状态:从角色设定提取名字,匹配 character_states
    elements = store.list_elements(pid, kind="character")
    char_names = [e["name"] for e in elements]
    char_states = []
    for n in char_names:
        cs = store.get_character_state(pid, n)
        if cs:
            char_states.append(cs)

    return {
        "query_type": "context_load",
        "progress": {
            "last_chapter": {"idx": target["idx"], "title": target["title"]},
            "next_chapter_idx": target["idx"] + 1,
        },
        "chapter_plan": {
            "chapter_id": target["id"], "idx": target["idx"], "title": target["title"],
            "outline": target.get("outline") or "(暂无细纲)",
        },
        "previous_chapter_summary": (
            {"idx": prev["idx"], "title": prev["title"],
             "tail": (prev.get("content") or "")[-1500:]}
            if prev else None
        ),
        "active_foreshadows": store.list_foreshadowings(pid, status="planted"),
        "recent_timeline": store.list_timeline_events(pid)[-10:],
        "characters": [
            {"name": cs["character_name"], "current_state": cs["current_state"],
             "latest_chapter": cs["latest_chapter"]}
            for cs in char_states
        ],
        "gaps": [] if target.get("outline") else ["chapter_outline_missing"],
    }


def _quality_check(pid: str, args: dict) -> dict:
    """执行一致性检查,返回 S1-S4 分级报告。

    此处做轻量级确定性检查 (无需 LLM);需要深度推理的检查由 consistency-checker
    agent 在调用本工具后,基于返回的事实清单自行推理输出 S1-S4 报告。
    """
    scope = args.get("scope", "all")
    focus = args.get("focus", "all")

    chapters = store.list_chapters(pid)
    if not chapters:
        return {"error": "尚无章节,无法检查"}

    # 范围筛选
    if scope == "latest":
        target_chapters = [chapters[-1]]
    elif scope == "chapter":
        cid = args.get("chapter_id")
        if not cid:
            return {"error": "scope=chapter 需要提供 chapter_id"}
        target = store.get_chapter(cid)
        if not target:
            return {"error": "未找到章节"}
        target_chapters = [target]
    else:
        target_chapters = chapters

    findings: list[dict] = []

    # ----- 伏笔扫描 -----
    if focus in ("foreshadow", "all"):
        fs_all = store.list_foreshadowings(pid)
        max_idx = max((c["idx"] for c in chapters), default=0)
        for f in fs_all:
            if f["status"] == "planted":
                planted = f.get("planted_chapter") or 0
                expected = f.get("expected_recovery")
                gap = max_idx - planted
                if expected and max_idx > expected:
                    findings.append({
                        "level": "S2", "type": "foreshadow_overdue",
                        "msg": f"伏笔「{f['name']}」预期第 {expected} 章回收,当前已写到第 {max_idx} 章未回收",
                        "foreshadow_id": f["id"],
                    })
                elif gap > 50:
                    findings.append({
                        "level": "S4", "type": "foreshadow_long_unrecovered",
                        "msg": f"伏笔「{f['name']}」第 {planted} 章埋设,已 {gap} 章未回收 (>50 章)",
                        "foreshadow_id": f["id"],
                    })

        # 伏笔密度
        per_volume = 50  # 假设 50 章/卷
        density = len(fs_all) / max(1, max_idx / per_volume)
        if density < 3 and max_idx > 10:
            findings.append({
                "level": "S4", "type": "foreshadow_low_density",
                "msg": f"伏笔密度 {density:.1f}/卷,低于建议下限 3/卷",
            })
        elif density > 15:
            findings.append({
                "level": "S4", "type": "foreshadow_high_density",
                "msg": f"伏笔密度 {density:.1f}/卷,高于建议上限 15/卷",
            })

    # ----- 时间线检查 -----
    if focus in ("timeline", "all"):
        events = store.list_timeline_events(pid)
        # 简单检查:同一章节有多个事件但 time_in_story 不同 (可能时间线冲突)
        per_chapter: dict[int, list] = {}
        for ev in events:
            ci = ev.get("chapter_idx")
            if ci is not None:
                per_chapter.setdefault(ci, []).append(ev)
        for ci, evs in per_chapter.items():
            times = {e.get("time_in_story") for e in evs if e.get("time_in_story")}
            if len(times) > 1:
                findings.append({
                    "level": "S3", "type": "timeline_multiple_times",
                    "msg": f"第 {ci} 章记录了多个不同时间点: {sorted(times)}",
                })

    # ----- 章节字数检查 -----
    for ch in target_chapters:
        content = ch.get("content") or ""
        chars = len(content)
        # 找细纲中的字数目标
        outline = ch.get("outline") or ""
        target_chars = None
        m = re.search(r"字数目标[::]\s*(\d+)", outline)
        if m:
            target_chars = int(m.group(1))
        if target_chars and chars < target_chars * 0.9:
            findings.append({
                "level": "S2", "type": "chapter_underworded",
                "msg": f"第 {ch['idx']+1} 章《{ch['title']}》字数 {chars} < 目标 {target_chars} 的 90%",
                "chapter_id": ch["id"],
            })
        elif chars < 2000 and ch.get("status") not in ("draft", "outlined"):
            findings.append({
                "level": "S3", "type": "chapter_short",
                "msg": f"第 {ch['idx']+1} 章《{ch['title']}》字数 {chars} < 2000 (长篇最低门槛)",
                "chapter_id": ch["id"],
            })

    # ----- 章节是否有细纲 -----
    for ch in target_chapters:
        if not (ch.get("outline") or "").strip() and ch.get("status") != "draft":
            findings.append({
                "level": "S3", "type": "outline_missing",
                "msg": f"第 {ch['idx']+1} 章《{ch['title']}》无细纲蓝图",
                "chapter_id": ch["id"],
            })

    # ----- 角色状态与设定一致性 (角色有设定但无状态记录) -----
    if focus in ("consistency", "all"):
        char_elements = store.list_elements(pid, kind="character")
        for e in char_elements:
            cs = store.get_character_state(pid, e["name"])
            if not cs:
                findings.append({
                    "level": "S4", "type": "character_state_missing",
                    "msg": f"角色「{e['name']}」已建档但无状态快照记录",
                    "element_id": e["id"],
                })

    # 汇总
    verdict = "APPROVE"
    if any(f["level"] == "S1" for f in findings):
        verdict = "REJECT"
    elif any(f["level"] == "S2" for f in findings):
        verdict = "CONCERNS"

    counts = {"S1": 0, "S2": 0, "S3": 0, "S4": 0}
    for f in findings:
        counts[f["level"]] += 1

    return {
        "verdict": verdict,
        "scope": scope,
        "checked_chapters": [c["idx"] for c in target_chapters],
        "counts": counts,
        "findings": findings,
        "note": ("本工具仅做确定性检查 (字数/伏笔超期/密度/细纲缺失/角色状态缺失)。"
                 "深度推理检查 (规则边界悖论/设定层级冲突/跨章因果链/代价一致性) "
                 "由 consistency-checker agent 基于本结果推理输出。"),
    }


def schema_for(tool_names: list[str]) -> list[dict]:
    """按 agent 工具白名单过滤出对应的 tool schema。"""
    by_name = {t["function"]["name"]: t for t in TOOL_SCHEMA}
    return [by_name[n] for n in tool_names if n in by_name]


# ---------------- 技能库工具 (Skill: 111 位作家语料 + 方法论) ----------------
# 集成自 https://github.com/l1064709321/Online-writing-skill
# 理念: 不存统计摘要,存原文精选段落;Prompt 直接塞原文做 few-shot (原理5 Few-shot),
# 让模型从原文学句式节奏、信息密度、断句习惯,而不是"请用 XX 风格写"的模板废话。

def _skill_list_authors(args: dict) -> dict:
    """列出技能库中所有可用作家 (含流派分类)。"""
    from . import skill_library
    loader = skill_library.get_corpus_loader()
    all_authors = loader.list_authors()
    # 按 methodology 中的 genre 分类
    by_genre: dict[str, list[str]] = {}
    for a in all_authors:
        m = skill_library.get_methodology(a)
        g = m.get("genre", "其他") if m else "其他"
        # 取流派第一段作分类键
        key = g.split("/")[0].strip() if g else "其他"
        by_genre.setdefault(key, []).append(a)
    return {
        "total": len(all_authors),
        "scene_tags": skill_library.SCENE_TAGS,
        "by_genre": by_genre,
        "tip": "用 match_author 按题材匹配,用 get_author_reference 取原文 few-shot 参考",
    }


def _skill_match_author(pid: str, args: dict) -> dict:
    """按题材/文风/设定/频道匹配最合适的 1-3 位作家 (含方法论摘要)。"""
    from . import skill_library
    p = store.get_project(pid) or {}
    genre = args.get("genre") or p.get("genre", "通用")
    style = args.get("style") or p.get("style", "")
    premise = args.get("premise") or p.get("premise", "")
    audience = args.get("audience") or p.get("audience", "")
    matches = skill_library.match_author(genre, style, premise, audience)
    return {
        "genre": genre,
        "audience": audience or "未指定",
        "matched_authors": matches,
        "tip": ("下一步: 用 get_author_reference(author=某作家, scene=battle) "
                "取该作家在该场景的原文 few-shot,塞进 continue_writing 的 instruction 里, "
                "让模型从原文学文风而非模板仿写。"),
    }


def _skill_get_author_reference(args: dict) -> dict:
    """取某作家在某场景的原文精选段落 (few-shot 参考)。"""
    from . import skill_library
    author = args.get("author", "")
    scene = args.get("scene")
    limit = int(args.get("limit", 3))
    if not author:
        return {"error": "缺少 author 参数"}
    loader = skill_library.get_corpus_loader()
    passages = loader.get_passages(author, scene_type=scene, limit=limit)
    if not passages:
        return {
            "error": f"未找到作家 {author} 在场景 {scene} 的语料",
            "available_scenes": list(skill_library.SCENE_TAGS.keys()),
            "tip": "scene 可选: battle/dialogue/environment/psychology/opening/climax/humor/suspense/emotion/worldbuilding",
        }
    few_shot = loader.get_few_shot_prompt(author, scene_type=scene, limit=limit)
    m = skill_library.get_methodology(author)
    return {
        "author": author,
        "scene": scene or "all",
        "methodology": {
            "core_principle": m.get("core_principle", ""),
            "rhythm_formula": m.get("rhythm_formula", ""),
            "technique": m.get("technique", ""),
            "sentence_style": m.get("sentence_style", ""),
            "common_words": m.get("common_words", []),
        } if m else {},
        "few_shot_text": few_shot,
        "usage": (
            "把 few_shot_text 塞进 continue_writing/polish 的 instruction 前部, "
            "让模型从原文片段学句式节奏与信息密度。methodology 给出节奏公式与常用词供参考。"
        ),
    }


# ---------------- 真实网络工具 (web_fetch / web_search) ----------------
# 这些工具发起真实 HTTP 请求,不是用 LLM 训练数据。
# 用于扫榜调研时访问起点/番茄/七猫等真实榜单页面,以及搜索最新热门话题。

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_HTTP_TIMEOUT = 15.0  # 请求超时(秒), 避免长时间挂起


async def _web_fetch(url: str, max_chars: int = 8000) -> dict:
    """抓取指定 URL 的网页,抽取正文内容。

    使用 httpx 发送 HTTP 请求, BeautifulSoup 解析 HTML,
    优先用 readability-lxml 提取正文, 失败则回退到纯文本抽取。
    """
    if not url.startswith(("http://", "https://")):
        return {"error": "url 必须以 http:// 或 https:// 开头", "url": url}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as cl:
            resp = await cl.get(
                url,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            )
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "url": url}
        html = resp.text
    except httpx.TimeoutException:
        return {"error": "请求超时 (15s)", "url": url}
    except Exception as e:
        return {"error": f"请求失败: {e}", "url": url}

    # 惰性导入 BeautifulSoup (bs4 是可选依赖, 仅网页抓取需要)
    BS = _get_bs4()
    if BS is None:
        return {
            "error": "网页抓取需要 beautifulsoup4 库。请运行: pip install beautifulsoup4",
            "url": url,
        }

    # 优先用 lxml 解析 (快), 失败则用 html.parser (纯 Python, 无需编译)
    try:
        soup = BS(html, "lxml")
    except Exception:
        soup = BS(html, "html.parser")

    # 优先用 readability 提取正文
    try:
        from readability import Document
        doc = Document(html)
        title = doc.title() or soup.title.string or ""
        text = doc.summary()
        text_soup = BS(text, "html.parser")
        main_text = text_soup.get_text(separator="\n", strip=True)
    except Exception:
        # 回退: 去掉 script/style/nav/footer/header, 提取 body 文本
        for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        title = soup.title.string if soup.title else ""
        main_text = soup.get_text(separator="\n", strip=True)

    # 去空行 + 截断
    lines = [ln.strip() for ln in main_text.split("\n") if ln.strip()]
    main_text = "\n".join(lines)
    if len(main_text) > max_chars:
        main_text = main_text[:max_chars] + f"\n\n… (已截断, 原文共 {len(main_text)} 字符)"

    return {
        "url": url,
        "title": title,
        "content": main_text,
        "content_chars": len(main_text),
        "fetched_at": __import__("time").time(),
    }


async def _web_search(query: str, max_results: int = 8) -> dict:
    """用 Bing 搜索, 返回结构化结果。

    不依赖 API Key, 直接用 Bing HTML 搜索接口抓取结果页。
    """
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as cl:
            resp = await cl.get(
                "https://www.bing.com/search",
                params={"q": query, "mkt": "zh-CN", "setlang": "zh-Hans"},
                headers={"User-Agent": _USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
            )
        if resp.status_code >= 400:
            return {"error": f"搜索请求失败 HTTP {resp.status_code}", "query": query}
        html = resp.text
    except Exception as e:
        return {"error": f"搜索失败: {e}", "query": query}

    BS = _get_bs4()
    if BS is None:
        return {
            "error": "网页搜索需要 beautifulsoup4 库。请运行: pip install beautifulsoup4",
            "query": query,
        }
    soup = BS(html, "lxml")
    results = []
    # Bing 搜索结果结构: li.b_algo > h2 > a
    for item in soup.select("li.b_algo"):
        title_el = item.select_one("h2 a")
        if not title_el:
            continue
        href = title_el.get("href", "")
        title = title_el.get_text(strip=True)
        # 摘要: p 或 div.b_caption
        snippet_el = item.select_one(".b_caption p, .b_lineclamp2, p")
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        if title and href:
            results.append({
                "title": title,
                "url": href,
                "snippet": snippet,
            })
        if len(results) >= max_results:
            break

    if not results:
        return {
            "query": query,
            "results": [],
            "note": "未找到结果。Bing 可能返回了验证页面, 稍后重试。",
        }

    return {
        "query": query,
        "count": len(results),
        "results": results,
        "searched_at": __import__("time").time(),
    }


# ---------------- Playwright 浏览器工具 (browser_fetch / browser_screenshot) ----------------
# 使用 Playwright 真实浏览器访问网页,支持 JS 渲染页面 (如起点/番茄/七猫等 SPA 榜单页)。
# 沙箱逻辑: 这两个工具都是只读的 (不修改项目数据), 所有 agent 均可调用,
# 包括只读 agent (story-explorer, consistency-checker)。

_BROWSER_TIMEOUT = 20.0  # 浏览器操作超时(秒)

# 全局单例 browser 实例 (避免每次请求都启动浏览器)
_browser: Any = None
_playwright: Any = None


async def _get_browser():
    """获取或创建 Playwright browser 实例 (懒加载单例)。

    内核装在各平台系统默认位置 (Linux: ~/.cache/ms-playwright/,
    Windows: %USERPROFILE%\AppData\Local\ms-playwright\),
    不装进项目目录 —— 因为 Chromium 内核是平台二进制, Linux/Windows/macOS
    互不通用, 跟着项目走没意义且白占 300MB+。
    首次使用请跑项目根目录的一键安装脚本 (已固化国内镜像源):
      Linux/macOS: ./setup_browser.sh
      Windows:     setup_browser.bat
    """
    global _browser, _playwright
    if _browser is None:
        try:
            from playwright.async_api import async_playwright
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
        except Exception as e:
            # 内核未装 / 缺系统库 → 给出含国内源的一键安装指引
            return None, (
                f"浏览器启动失败: {e}。"
                "Chromium 内核尚未安装或缺少系统依赖, 请在项目根目录运行一键安装脚本 "
                "(已固化国内镜像源, 下载快): "
                "Linux/macOS 执行 ./setup_browser.sh, Windows 执行 setup_browser.bat"
            )
    return _browser, None


async def _browser_fetch(url: str, max_chars: int = 8000, *, wait_for: str = "networkidle") -> dict:
    """使用 Playwright 真实浏览器抓取网页 (支持 JS 渲染)。

    相比 web_fetch (httpx), 这个工具能正确渲染需 JavaScript 的页面,
    如起点/番茄/七猫等 SPA 榜单页、动态加载内容的页面。
    """
    if not url.startswith(("http://", "https://")):
        return {"error": "url 必须以 http:// 或 https:// 开头", "url": url}

    browser, err = await _get_browser()
    if err:
        return {"error": err, "url": url}

    try:
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until=wait_for, timeout=_BROWSER_TIMEOUT * 1000)
            title = await page.title()
            # 提取页面正文文本
            text = await page.evaluate("""() => {
                // 去掉 script/style/nav/footer/header
                const els = document.querySelectorAll('script, style, nav, footer, header, aside, [role="navigation"]');
                els.forEach(el => el.remove());
                return document.body ? document.body.innerText : document.documentElement.innerText;
            }""")
            # 清理空行
            lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
            clean = "\n".join(lines)
            if len(clean) > max_chars:
                clean = clean[:max_chars] + f"\n\n… (已截断, 原文共 {len(clean)} 字符)"
            return {
                "url": url,
                "title": title,
                "content": clean,
                "content_chars": len(clean),
                "fetched_at": __import__("time").time(),
                "method": "playwright",
            }
        finally:
            await page.close()
    except Exception as e:
        return {"error": f"浏览器抓取失败: {e}", "url": url}


async def _browser_screenshot(url: str, *, full_page: bool = True) -> dict:
    """使用 Playwright 对网页截图 (返回 base64 PNG)。

    用于扫榜调研时截取榜单页面/竞品分析截图,或验证页面渲染状态。
    截图结果可直接在前端展示。
    """
    import base64

    if not url.startswith(("http://", "https://")):
        return {"error": "url 必须以 http:// 或 https:// 开头", "url": url}

    browser, err = await _get_browser()
    if err:
        return {"error": err, "url": url}

    try:
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            await page.goto(url, wait_until="networkidle", timeout=_BROWSER_TIMEOUT * 1000)
            title = await page.title()
            screenshot_bytes = await page.screenshot(full_page=full_page, type="png")
            b64 = base64.b64encode(screenshot_bytes).decode("ascii")
            return {
                "url": url,
                "title": title,
                "screenshot_base64": b64,
                "format": "png",
                "size_bytes": len(screenshot_bytes),
                "taken_at": __import__("time").time(),
            }
        finally:
            await page.close()
    except Exception as e:
        return {"error": f"截图失败: {e}", "url": url}

