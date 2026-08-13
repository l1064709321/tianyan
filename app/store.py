"""SQLite 持久化层。存储:小说项目、角色/世界观设定、章节正文、
上传文档的分块(用于续写检索)、以及对话消息(用于 agent 上下文)。

使用同步 sqlite3 (足够小说场景,避免 aiosqlite 额外依赖)。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional

from .config import get_settings

_lock = threading.Lock()


def _now() -> float:
    return time.time()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    s = get_settings()
    os.makedirs(os.path.dirname(s.db_path), exist_ok=True)
    conn = sqlite3.connect(s.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                genre TEXT,
                premise TEXT,
                style TEXT,
                audience TEXT DEFAULT '',
                meta TEXT DEFAULT '{}',
                created_at REAL
            );
            CREATE TABLE IF NOT EXISTS chapters (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                title TEXT NOT NULL,
                idx INTEGER NOT NULL,
                outline TEXT,
                content TEXT DEFAULT '',
                status TEXT DEFAULT 'draft',
                created_at REAL,
                updated_at REAL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS elements (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                kind TEXT NOT NULL,        -- character | location | lore | timeline
                name TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at REAL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                source TEXT NOT NULL,      -- upload:filename | chapter:id
                idx INTEGER NOT NULL,
                text TEXT NOT NULL,
                embedding TEXT,            -- 预留向量字段 (当前用关键词检索)
                created_at REAL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                role TEXT NOT NULL,        -- user | assistant | tool
                content TEXT NOT NULL,
                tool_name TEXT,
                tool_call_id TEXT,
                created_at REAL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            -- 伏笔追踪 (跨章/跨卷)
            CREATE TABLE IF NOT EXISTS foreshadowings (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                name TEXT NOT NULL,           -- 伏笔名称/简述
                content TEXT NOT NULL,        -- 详细描述
                planted_chapter INTEGER,     -- 埋设章节号
                expected_recovery INTEGER,   -- 预期回收章节号
                actual_recovery INTEGER,     -- 实际回收章节号
                status TEXT DEFAULT 'planted', -- planted | recovered | abandoned
                created_at REAL,
                updated_at REAL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            -- 时间线事件
            CREATE TABLE IF NOT EXISTS timeline_events (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                event TEXT NOT NULL,          -- 事件描述
                chapter_idx INTEGER,          -- 所属章节号
                time_in_story TEXT,           -- 故事内时间点 (自由格式)
                cause TEXT,                   -- 原因
                effect TEXT,                  -- 后果
                created_at REAL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            -- 角色状态快照 (随章节更新)
            CREATE TABLE IF NOT EXISTS character_states (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                character_name TEXT NOT NULL, -- 角色名
                current_state TEXT NOT NULL,  -- 当前状态描述 (身份/能力/关系/公众形象)
                latest_chapter INTEGER,       -- 最近一次更新的章节号
                change_log TEXT DEFAULT '[]',  -- JSON 数组: [{chapter, change, at}]
                updated_at REAL,
                UNIQUE(project_id, character_name),
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            -- ===== 评测与可观测性 =====
            -- 一次完整的 agent loop (从用户发输入到 SSE 结束) = 一个 run
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                user_input TEXT NOT NULL,
                entry_agent TEXT NOT NULL,        -- 入口 agent (默认 orchestrator)
                status TEXT DEFAULT 'running',    -- running | done | error | interrupted
                total_tokens INTEGER DEFAULT 0,  -- 累计 token (prompt+completion)
                total_cost REAL DEFAULT 0,        -- 累计成本 (USD, 按 litellm pricing)
                total_steps INTEGER DEFAULT 0,   -- agent loop 迭代次数
                total_cache_hit_tokens INTEGER DEFAULT 0,  -- 累计缓存命中 token
                total_cache_miss_tokens INTEGER DEFAULT 0, -- 累计缓存未命中 token
                error TEXT,                       -- 失败时记录错误消息
                started_at REAL NOT NULL,
                ended_at REAL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            -- run 内的每个关键事件 (LLM 调用 / 工具调用 / 工具结果 / 委派 / 错误)
            CREATE TABLE IF NOT EXISTS run_events (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,             -- 事件序号 (从 0 递增)
                ts REAL NOT NULL,
                type TEXT NOT NULL,               -- start|llm_call|tool_call|tool_result|delegate|delegate_done|error|end
                agent TEXT,                       -- 当前活跃 agent (如 narrative-writer)
                tool TEXT,                        -- 工具名 (tool_call/tool_result 时填)
                input TEXT,                       -- JSON 字符串: 输入参数 / LLM prompt 摘要
                output TEXT,                      -- JSON 字符串: 输出结果 / LLM 回复
                tokens INTEGER,                   -- 该事件 token 数 (LLM 时填)
                cost REAL,                        -- 该事件成本 (LLM 时填)
                cache_hit_tokens INTEGER,         -- 缓存命中 token 数 (LLM 调用时填)
                cache_miss_tokens INTEGER,        -- 缓存未命中 token 数 (LLM 调用时填)
                duration_ms INTEGER,              -- 耗时 (毫秒)
                error TEXT,                       -- 错误信息 (type=error 时填)
                FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, seq);
            CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id, started_at DESC);

            -- ===== 新架构: 角色档案系统 (4号角色师管理) =====
            -- 每个角色独立档案: 性格基调/说话风格/行为逻辑/主线动机/人物弧光
            CREATE TABLE IF NOT EXISTS character_profiles (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                name TEXT NOT NULL,             -- 角色名
                role TEXT DEFAULT '',           -- 主角/配角/反派/...
                personality TEXT DEFAULT '',    -- 性格基调 (冷峻/活泼/阴郁...)
                speech_style TEXT DEFAULT '',   -- 说话风格 (词汇密度/句长/口癖/禁用词)
                behavior_logic TEXT DEFAULT '', -- 行为逻辑 (遇强权怎办?遇朋友怎办?)
                motivation TEXT DEFAULT '',     -- 主线动机 (为什么行动?)
                arc TEXT DEFAULT '',            -- 人物弧光 (起点→转折→终点)
                growth_state TEXT DEFAULT '',   -- 当前成长状态 (随剧情更新)
                meta TEXT DEFAULT '{}',
                created_at REAL,
                updated_at REAL,
                UNIQUE(project_id, name),
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            -- ===== 新架构: 世界观档案系统 (2号架构师管理) =====
            -- 地点/势力/规则/时间线节点, 每条独立档案
            CREATE TABLE IF NOT EXISTS world_entries (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                category TEXT NOT NULL,         -- location | faction | rule | timeline | lore
                name TEXT NOT NULL,
                description TEXT DEFAULT '',    -- 详细描述
                attributes TEXT DEFAULT '{}',   -- JSON: 附加属性 (如地点的气候/势力的层级)
                related_chars TEXT DEFAULT '[]',-- JSON: 关联角色名数组
                created_at REAL,
                updated_at REAL,
                UNIQUE(project_id, category, name),
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            -- ===== 新架构: 主线里程碑清单 (2号架构师产出) =====
            CREATE TABLE IF NOT EXISTS milestones (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                chapter_idx INTEGER NOT NULL,   -- 目标章节号
                title TEXT NOT NULL,            -- 里程碑标题 (如"第3章得线索")
                description TEXT DEFAULT '',    -- 详细描述
                status TEXT DEFAULT 'pending',  -- pending | reached | missed
                reached_chapter INTEGER,        -- 实际达成章节号
                created_at REAL,
                updated_at REAL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            -- ===== 新架构: 风格缓存 (6号资料员维护) =====
            -- 缓存前N章风格特征, 供5号质检员对比风格一致性
            CREATE TABLE IF NOT EXISTS style_cache (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                chapter_idx INTEGER NOT NULL,
                features TEXT DEFAULT '{}',     -- JSON: 风格指纹 (句长/词频/视角/语气...)
                keywords TEXT DEFAULT '{}',     -- JSON: 主线关键词出现频率
                created_at REAL,
                UNIQUE(project_id, chapter_idx),
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            """
        )
    # 中断恢复: 上次进程异常退出 (kill/崩溃) 会留下 status='running' 的孤儿 run
    # 和 status='generating' 的孤儿章节。重启时统一标记为 interrupted/failed,
    # 让前端能识别"上次没跑完",而不是永远显示"运行中"
    recover_interrupted()
    _migrate_add_audience()
    _migrate_add_cache_columns()


def recover_interrupted() -> dict:
    """把上次未完成的 run 和章节标记为中断/失败状态。

    场景: 服务被 kill -9 / OOM / 容器重启, agent loop 跑到一半挂了,
    数据库里还留着 status='running' 的 run 和 status='generating' 的章节。
    重启后这些状态会一直挂着, 前端显示"运行中"误导用户。
    本函数在 init_db 时自动调用, 也可手动调。
    """
    now = _now()
    with _lock, get_conn() as c:
        runs = c.execute(
            "SELECT id FROM runs WHERE status='running'"
        ).fetchall()
        for r in runs:
            c.execute(
                "UPDATE runs SET status='interrupted', error='进程重启时检测到未完成,自动标记中断', ended_at=? WHERE id=?",
                (now, r["id"]),
            )
        chs = c.execute(
            "SELECT id FROM chapters WHERE status='generating'"
        ).fetchall()
        for ch in chs:
            c.execute(
                "UPDATE chapters SET status='failed' WHERE id=?", (ch["id"],)
            )
    return {"recovered_runs": len(runs), "recovered_chapters": len(chs)}


def _migrate_add_audience() -> None:
    """旧库迁移: projects 表加 audience 列。"""
    with get_conn() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(projects)").fetchall()]
        if "audience" not in cols:
            c.execute("ALTER TABLE projects ADD COLUMN audience TEXT DEFAULT ''")


def _migrate_add_cache_columns() -> None:
    """旧库迁移: runs / run_events 加 cache 命中统计列。"""
    with get_conn() as c:
        # runs 表
        run_cols = [r[1] for r in c.execute("PRAGMA table_info(runs)").fetchall()]
        if "total_cache_hit_tokens" not in run_cols:
            c.execute("ALTER TABLE runs ADD COLUMN total_cache_hit_tokens INTEGER DEFAULT 0")
        if "total_cache_miss_tokens" not in run_cols:
            c.execute("ALTER TABLE runs ADD COLUMN total_cache_miss_tokens INTEGER DEFAULT 0")
        # run_events 表
        evt_cols = [r[1] for r in c.execute("PRAGMA table_info(run_events)").fetchall()]
        if "cache_hit_tokens" not in evt_cols:
            c.execute("ALTER TABLE run_events ADD COLUMN cache_hit_tokens INTEGER")
        if "cache_miss_tokens" not in evt_cols:
            c.execute("ALTER TABLE run_events ADD COLUMN cache_miss_tokens INTEGER")


def _uuid() -> str:
    return uuid.uuid4().hex


# ---------- projects ----------
def create_project(name: str, genre: str = "", premise: str = "", style: str = "", audience: str = "") -> str:
    pid = _uuid()
    with _lock, get_conn() as c:
        c.execute(
            "INSERT INTO projects(id,name,genre,premise,style,audience,meta,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (pid, name, genre, premise, style, audience, "{}", _now()),
        )
    return pid


def list_projects() -> list[dict]:
    with get_conn() as c:
        rows = c.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_project(pid: str) -> Optional[dict]:
    with get_conn() as c:
        r = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None


def update_project(pid: str, **fields) -> None:
    if not fields:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    with _lock, get_conn() as c:
        c.execute(f"UPDATE projects SET {sets} WHERE id=?", (*fields.values(), pid))


def delete_project(pid: str) -> None:
    with _lock, get_conn() as c:
        c.execute("DELETE FROM projects WHERE id=?", (pid,))


# ---------- chapters ----------
def add_chapter(pid: str, title: str, idx: int, outline: str = "", content: str = "") -> str:
    cid = _uuid()
    now = _now()
    with _lock, get_conn() as c:
        c.execute(
            "INSERT INTO chapters(id,project_id,title,idx,outline,content,status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (cid, pid, title, idx, outline, content, "draft", now, now),
        )
    return cid


def list_chapters(pid: str) -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM chapters WHERE project_id=? ORDER BY idx ASC", (pid,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_chapter(cid: str) -> Optional[dict]:
    with get_conn() as c:
        r = c.execute("SELECT * FROM chapters WHERE id=?", (cid,)).fetchone()
        return dict(r) if r else None


def update_chapter(cid: str, **fields) -> None:
    fields = {**fields, "updated_at": _now()}
    if not fields:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    with _lock, get_conn() as c:
        c.execute(f"UPDATE chapters SET {sets} WHERE id=?", (*fields.values(), cid))


def delete_chapter(cid: str) -> None:
    with _lock, get_conn() as c:
        c.execute("DELETE FROM chapters WHERE id=?", (cid,))


# ---------- elements ----------
def add_element(pid: str, kind: str, name: str, detail: str) -> str:
    eid = _uuid()
    with _lock, get_conn() as c:
        c.execute(
            "INSERT INTO elements(id,project_id,kind,name,detail,created_at) VALUES(?,?,?,?,?,?)",
            (eid, pid, kind, name, detail, _now()),
        )
    return eid


def list_elements(pid: str, kind: Optional[str] = None) -> list[dict]:
    with get_conn() as c:
        if kind:
            rows = c.execute(
                "SELECT * FROM elements WHERE project_id=? AND kind=? ORDER BY created_at",
                (pid, kind),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM elements WHERE project_id=? ORDER BY kind,created_at", (pid,)
            ).fetchall()
        return [dict(r) for r in rows]


def delete_element(eid: str) -> None:
    with _lock, get_conn() as c:
        c.execute("DELETE FROM elements WHERE id=?", (eid,))


# ---------- chunks (上传小说 / 章节分块) ----------
def add_chunk(pid: str, source: str, idx: int, text: str) -> str:
    cid = _uuid()
    with _lock, get_conn() as c:
        c.execute(
            "INSERT INTO chunks(id,project_id,source,idx,text,created_at) VALUES(?,?,?,?,?,?)",
            (cid, pid, source, idx, text, _now()),
        )
    return cid


def list_chunks(pid: str) -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM chunks WHERE project_id=? ORDER BY source,idx", (pid,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_chunks_by_source(pid: str, source: str) -> None:
    with _lock, get_conn() as c:
        c.execute("DELETE FROM chunks WHERE project_id=? AND source=?", (pid, source))


# ---------- messages ----------
def add_message(
    pid: str,
    role: str,
    content: str,
    tool_name: Optional[str] = None,
    tool_call_id: Optional[str] = None,
) -> str:
    mid = _uuid()
    with _lock, get_conn() as c:
        c.execute(
            "INSERT INTO messages(id,project_id,role,content,tool_name,tool_call_id,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (mid, pid, role, content, tool_name, tool_call_id, _now()),
        )
    return mid


def list_messages(pid: str, limit: int = 50) -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM messages WHERE project_id=? ORDER BY created_at ASC LIMIT ?",
            (pid, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def clear_messages(pid: str) -> None:
    with _lock, get_conn() as c:
        c.execute("DELETE FROM messages WHERE project_id=?", (pid,))


def stats(pid: str) -> dict:
    with get_conn() as c:
        ch = c.execute("SELECT COUNT(*) n FROM chapters WHERE project_id=?", (pid,)).fetchone()
        el = c.execute("SELECT COUNT(*) n FROM elements WHERE project_id=?", (pid,)).fetchone()
        ck = c.execute("SELECT COUNT(*) n FROM chunks WHERE project_id=?", (pid,)).fetchone()
        wc = c.execute(
            "SELECT COALESCE(SUM(LENGTH(content)),0) n FROM chapters WHERE project_id=?", (pid,)
        ).fetchone()
        fs = c.execute("SELECT COUNT(*) n FROM foreshadowings WHERE project_id=?", (pid,)).fetchone()
        tl = c.execute("SELECT COUNT(*) n FROM timeline_events WHERE project_id=?", (pid,)).fetchone()
        cs = c.execute("SELECT COUNT(*) n FROM character_states WHERE project_id=?", (pid,)).fetchone()
        return {
            "chapters": ch["n"],
            "elements": el["n"],
            "chunks": ck["n"],
            "total_chars": wc["n"],
            "foreshadowings": fs["n"],
            "timeline_events": tl["n"],
            "character_states": cs["n"],
        }


# ---------- foreshadowings (伏笔追踪) ----------
def add_foreshadowing(
    pid: str, name: str, content: str,
    planted_chapter: Optional[int] = None,
    expected_recovery: Optional[int] = None,
) -> str:
    fid = _uuid()
    now = _now()
    with _lock, get_conn() as c:
        c.execute(
            "INSERT INTO foreshadowings(id,project_id,name,content,"
            "planted_chapter,expected_recovery,actual_recovery,status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,NULL,'planted',?,?)",
            (fid, pid, name, content, planted_chapter, expected_recovery, now, now),
        )
    return fid


def list_foreshadowings(pid: str, status: Optional[str] = None) -> list[dict]:
    with get_conn() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM foreshadowings WHERE project_id=? AND status=? "
                "ORDER BY planted_chapter ASC",
                (pid, status),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM foreshadowings WHERE project_id=? "
                "ORDER BY planted_chapter ASC",
                (pid,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_foreshadowing(fid: str) -> Optional[dict]:
    with get_conn() as c:
        r = c.execute("SELECT * FROM foreshadowings WHERE id=?", (fid,)).fetchone()
        return dict(r) if r else None


def update_foreshadowing(fid: str, **fields) -> None:
    if not fields:
        return
    fields = {**fields, "updated_at": _now()}
    sets = ",".join(f"{k}=?" for k in fields)
    with _lock, get_conn() as c:
        c.execute(f"UPDATE foreshadowings SET {sets} WHERE id=?", (*fields.values(), fid))


def delete_foreshadowing(fid: str) -> None:
    with _lock, get_conn() as c:
        c.execute("DELETE FROM foreshadowings WHERE id=?", (fid,))


# ---------- timeline_events (时间线) ----------
def add_timeline_event(
    pid: str, event: str,
    chapter_idx: Optional[int] = None,
    time_in_story: Optional[str] = None,
    cause: Optional[str] = None,
    effect: Optional[str] = None,
) -> str:
    tid = _uuid()
    with _lock, get_conn() as c:
        c.execute(
            "INSERT INTO timeline_events(id,project_id,event,chapter_idx,"
            "time_in_story,cause,effect,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (tid, pid, event, chapter_idx, time_in_story, cause, effect, _now()),
        )
    return tid


def list_timeline_events(
    pid: str, from_chapter: Optional[int] = None, to_chapter: Optional[int] = None
) -> list[dict]:
    with get_conn() as c:
        if from_chapter is not None and to_chapter is not None:
            rows = c.execute(
                "SELECT * FROM timeline_events WHERE project_id=? "
                "AND chapter_idx>=? AND chapter_idx<=? ORDER BY chapter_idx ASC, created_at ASC",
                (pid, from_chapter, to_chapter),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM timeline_events WHERE project_id=? "
                "ORDER BY chapter_idx ASC, created_at ASC",
                (pid,),
            ).fetchall()
        return [dict(r) for r in rows]


def delete_timeline_event(tid: str) -> None:
    with _lock, get_conn() as c:
        c.execute("DELETE FROM timeline_events WHERE id=?", (tid,))


# ---------- character_states (角色状态快照) ----------
def upsert_character_state(
    pid: str, character_name: str, current_state: str,
    latest_chapter: Optional[int] = None, change: Optional[str] = None,
) -> str:
    """新增或更新角色状态。若角色已存在,追加 change 到 change_log。"""
    now = _now()
    csid = _uuid()
    with _lock, get_conn() as c:
        existing = c.execute(
            "SELECT * FROM character_states WHERE project_id=? AND character_name=?",
            (pid, character_name),
        ).fetchone()
        if existing:
            log = json.loads(existing["change_log"] or "[]")
            if change:
                log.append({
                    "chapter": latest_chapter,
                    "change": change,
                    "at": now,
                })
            new_state = current_state or existing["current_state"]
            new_chapter = (latest_chapter if latest_chapter is not None
                           else existing["latest_chapter"])
            c.execute(
                "UPDATE character_states SET current_state=?, latest_chapter=?, "
                "change_log=?, updated_at=? WHERE id=?",
                (new_state, new_chapter, json.dumps(log, ensure_ascii=False), now, existing["id"]),
            )
            return existing["id"]
        else:
            log = [{"chapter": latest_chapter, "change": change, "at": now}] if change else []
            c.execute(
                "INSERT INTO character_states(id,project_id,character_name,"
                "current_state,latest_chapter,change_log,updated_at) VALUES(?,?,?,?,?,?,?)",
                (csid, pid, character_name, current_state, latest_chapter,
                 json.dumps(log, ensure_ascii=False), now),
            )
            return csid


def list_character_states(pid: str) -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM character_states WHERE project_id=? "
            "ORDER BY character_name ASC",
            (pid,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_character_state(pid: str, name: str) -> Optional[dict]:
    with get_conn() as c:
        r = c.execute(
            "SELECT * FROM character_states WHERE project_id=? AND character_name=?",
            (pid, name),
        ).fetchone()
        return dict(r) if r else None


def delete_character_state(csid: str) -> None:
    with _lock, get_conn() as c:
        c.execute("DELETE FROM character_states WHERE id=?", (csid,))


# ---------- chapter outline (细纲蓝图,扩展 chapters.outline 字段) ----------
def set_chapter_outline(cid: str, outline_blueprint: str) -> None:
    """更新章节细纲蓝图 (oh-story 细纲格式 markdown)。"""
    update_chapter(cid, outline=outline_blueprint, status="outlined")


def get_chapter_outline(cid: str) -> Optional[str]:
    ch = get_chapter(cid)
    return ch.get("outline") if ch else None


# ==================== runs / run_events (评测可观测性) ====================
def create_run(project_id: str, user_input: str, entry_agent: str) -> str:
    """新建一次 agent loop run, 返回 run_id。"""
    rid = _uuid()
    with _lock, get_conn() as c:
        c.execute(
            "INSERT INTO runs(id,project_id,user_input,entry_agent,status,started_at)"
            " VALUES(?,?,?,?,'running',?)",
            (rid, project_id, user_input, entry_agent, _now()),
        )
    return rid


def add_run_event(
    run_id: str,
    type_: str,
    *,
    agent: Optional[str] = None,
    tool: Optional[str] = None,
    input_: Optional[dict | str] = None,
    output: Optional[dict | str] = None,
    tokens: Optional[int] = None,
    cost: Optional[float] = None,
    cache_hit_tokens: Optional[int] = None,
    cache_miss_tokens: Optional[int] = None,
    duration_ms: Optional[int] = None,
    error: Optional[str] = None,
) -> int:
    """追加一个事件到 run。返回 seq 序号。
    input_/output 接受 dict 或 str, dict 会被 JSON 序列化。
    """
    def _ser(v):
        if v is None:
            return None
        if isinstance(v, str):
            return v
        return json.dumps(v, ensure_ascii=False, default=str)
    # 取当前 seq (max+1)
    with _lock, get_conn() as c:
        row = c.execute(
            "SELECT COALESCE(MAX(seq),-1)+1 AS n FROM run_events WHERE run_id=?", (run_id,)
        ).fetchone()
        seq = row["n"]
        eid = _uuid()
        c.execute(
            "INSERT INTO run_events(id,run_id,seq,ts,type,agent,tool,input,output,tokens,cost,cache_hit_tokens,cache_miss_tokens,duration_ms,error)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, run_id, seq, _now(), type_, agent, tool,
             _ser(input_), _ser(output), tokens, cost, cache_hit_tokens, cache_miss_tokens, duration_ms, error),
        )
        # 增量更新 runs 累计字段 (避免回放时重算)
        if tokens:
            c.execute("UPDATE runs SET total_tokens=total_tokens+? WHERE id=?", (tokens, run_id))
        if cost:
            c.execute("UPDATE runs SET total_cost=total_cost+? WHERE id=?", (cost, run_id))
        if cache_hit_tokens:
            c.execute("UPDATE runs SET total_cache_hit_tokens=total_cache_hit_tokens+? WHERE id=?", (cache_hit_tokens, run_id))
        if cache_miss_tokens:
            c.execute("UPDATE runs SET total_cache_miss_tokens=total_cache_miss_tokens+? WHERE id=?", (cache_miss_tokens, run_id))
        if type_ == "llm_call":
            c.execute("UPDATE runs SET total_steps=total_steps+1 WHERE id=?", (run_id,))
    return seq


def finish_run(run_id: str, status: str = "done", error: Optional[str] = None) -> None:
    """结束一次 run。status: done | error | interrupted"""
    with _lock, get_conn() as c:
        c.execute(
            "UPDATE runs SET status=?, error=?, ended_at=? WHERE id=?",
            (status, error, _now(), run_id),
        )


def list_runs(project_id: str, limit: int = 50) -> list[dict]:
    """列出项目的 run 历史 (最近在前)。"""
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM runs WHERE project_id=? ORDER BY started_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_run(run_id: str) -> Optional[dict]:
    with get_conn() as c:
        r = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(r) if r else None


def list_run_events(run_id: str) -> list[dict]:
    """按 seq 顺序返回 run 的所有事件 (回放用)。"""
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM run_events WHERE run_id=? ORDER BY seq ASC", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def aggregate_project_metrics(project_id: str) -> dict:
    """项目级聚合指标: 总 run 数 / 总 token / 总成本 / 平均耗时 / 工具调用次数。"""
    with get_conn() as c:
        r = c.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(total_tokens),0) tok, "
            "COALESCE(SUM(total_cost),0) cost, "
            "COALESCE(SUM(total_cache_hit_tokens),0) cache_hit, "
            "COALESCE(SUM(total_cache_miss_tokens),0) cache_miss, "
            "COALESCE(AVG(ended_at-started_at),0) avg_dur "
            "FROM runs WHERE project_id=? AND ended_at IS NOT NULL",
            (project_id,),
        ).fetchone()
        tool_n = c.execute(
            "SELECT COUNT(*) n FROM run_events re JOIN runs r ON re.run_id=r.id "
            "WHERE r.project_id=? AND re.type='tool_call'", (project_id,),
        ).fetchone()
        return {
            "total_runs": r["n"],
            "total_tokens": r["tok"],
            "total_cost_usd": round(r["cost"], 4),
            "avg_run_duration_sec": round(r["avg_dur"], 2),
            "total_tool_calls": tool_n["n"],
            "total_cache_hit_tokens": int(r["cache_hit"] or 0),
            "total_cache_miss_tokens": int(r["cache_miss"] or 0),
        }


# ===== 新架构: 角色档案系统 (4号角色师管理) =====

def upsert_character_profile(pid: str, name: str, **fields) -> str:
    """创建或更新角色档案。name 唯一, 已存在则更新。"""
    cid = _uuid()
    now = _now()
    allowed = {"role", "personality", "speech_style", "behavior_logic",
               "motivation", "arc", "growth_state", "meta"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    with _lock, get_conn() as c:
        row = c.execute(
            "SELECT id FROM character_profiles WHERE project_id=? AND name=?",
            (pid, name),
        ).fetchone()
        if row:
            cid = row["id"]
            if sets:
                sets_sql = ", ".join(f"{k}=?" for k in sets)
                c.execute(
                    f"UPDATE character_profiles SET {sets_sql}, updated_at=? WHERE id=?",
                    (*sets.values(), now, cid),
                )
            return cid
        c.execute(
            "INSERT INTO character_profiles (id, project_id, name, role, personality, "
            "speech_style, behavior_logic, motivation, arc, growth_state, meta, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, pid, name, sets.get("role", ""), sets.get("personality", ""),
             sets.get("speech_style", ""), sets.get("behavior_logic", ""),
             sets.get("motivation", ""), sets.get("arc", ""),
             sets.get("growth_state", ""), sets.get("meta", "{}"), now, now),
        )
        return cid


def list_character_profiles(pid: str) -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM character_profiles WHERE project_id=? ORDER BY created_at",
            (pid,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_character_profile(pid: str, name: str) -> Optional[dict]:
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM character_profiles WHERE project_id=? AND name=?",
            (pid, name),
        ).fetchone()
        return dict(row) if row else None


def delete_character_profile(cpid: str) -> None:
    with _lock, get_conn() as c:
        c.execute("DELETE FROM character_profiles WHERE id=?", (cpid,))


# ===== 新架构: 世界观档案系统 (2号架构师管理) =====

def upsert_world_entry(pid: str, category: str, name: str, **fields) -> str:
    """创建或更新世界观档案。category+name 唯一。"""
    wid = _uuid()
    now = _now()
    allowed = {"description", "attributes", "related_chars"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    with _lock, get_conn() as c:
        row = c.execute(
            "SELECT id FROM world_entries WHERE project_id=? AND category=? AND name=?",
            (pid, category, name),
        ).fetchone()
        if row:
            wid = row["id"]
            if sets:
                sets_sql = ", ".join(f"{k}=?" for k in sets)
                c.execute(
                    f"UPDATE world_entries SET {sets_sql}, updated_at=? WHERE id=?",
                    (*sets.values(), now, wid),
                )
            return wid
        c.execute(
            "INSERT INTO world_entries (id, project_id, category, name, description, "
            "attributes, related_chars, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (wid, pid, category, name, sets.get("description", ""),
             sets.get("attributes", "{}"), sets.get("related_chars", "[]"), now, now),
        )
        return wid


def list_world_entries(pid: str, category: Optional[str] = None) -> list[dict]:
    with get_conn() as c:
        if category:
            rows = c.execute(
                "SELECT * FROM world_entries WHERE project_id=? AND category=? ORDER BY created_at",
                (pid, category),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM world_entries WHERE project_id=? ORDER BY category, created_at",
                (pid,),
            ).fetchall()
        return [dict(r) for r in rows]


def delete_world_entry(wid: str) -> None:
    with _lock, get_conn() as c:
        c.execute("DELETE FROM world_entries WHERE id=?", (wid,))


# ===== 新架构: 主线里程碑清单 (2号架构师产出) =====

def add_milestone(pid: str, chapter_idx: int, title: str, description: str = "") -> str:
    mid = _uuid()
    now = _now()
    with _lock, get_conn() as c:
        c.execute(
            "INSERT INTO milestones (id, project_id, chapter_idx, title, description, "
            "status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (mid, pid, chapter_idx, title, description, "pending", now, now),
        )
        return mid


def list_milestones(pid: str, status: Optional[str] = None) -> list[dict]:
    with get_conn() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM milestones WHERE project_id=? AND status=? ORDER BY chapter_idx",
                (pid, status),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM milestones WHERE project_id=? ORDER BY chapter_idx",
                (pid,),
            ).fetchall()
        return [dict(r) for r in rows]


def update_milestone(mid: str, **fields) -> None:
    allowed = {"status", "reached_chapter", "title", "description"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    sets_sql = ", ".join(f"{k}=?" for k in sets)
    with _lock, get_conn() as c:
        c.execute(
            f"UPDATE milestones SET {sets_sql}, updated_at=? WHERE id=?",
            (*sets.values(), _now(), mid),
        )


# ===== 新架构: 风格缓存 (6号资料员维护) =====

def upsert_style_cache(pid: str, chapter_idx: int, features: str, keywords: str) -> str:
    sid = _uuid()
    now = _now()
    with _lock, get_conn() as c:
        row = c.execute(
            "SELECT id FROM style_cache WHERE project_id=? AND chapter_idx=?",
            (pid, chapter_idx),
        ).fetchone()
        if row:
            sid = row["id"]
            c.execute(
                "UPDATE style_cache SET features=?, keywords=?, created_at=? WHERE id=?",
                (features, keywords, now, sid),
            )
            return sid
        c.execute(
            "INSERT INTO style_cache (id, project_id, chapter_idx, features, keywords, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (sid, pid, chapter_idx, features, keywords, now),
        )
        return sid


def list_style_cache(pid: str) -> list[dict]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM style_cache WHERE project_id=? ORDER BY chapter_idx",
            (pid,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_style_cache(pid: str, chapter_idx: int) -> Optional[dict]:
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM style_cache WHERE project_id=? AND chapter_idx=?",
            (pid, chapter_idx),
        ).fetchone()
        return dict(row) if row else None
