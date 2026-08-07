"""项目级记忆隔离管理器 — 多项目独立风格/角色/剧情.

架构:
- PostgreSQL: 持久化项目/角色/章节/风格偏好 (替代 SQLite 的部分功能)
- Redis: 短期对话记忆 (每个项目独立, 切换项目时清空)
- Chroma: 向量数据库 (按 project_id 分区, 语义检索角色/世界观/章节)

降级策略:
- PostgreSQL 不可用 → 降级到 SQLite (通过现有 store.py)
- Redis 不可用 → 降级到内存字典 (单进程内有效)
- Chroma 不可用 → 降级到关键词检索 (通过现有 store.py)

这样无论环境如何, 核心功能始终可用.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger("tianyan.memory")

# 检查可用性
try:
    import psycopg2  # type: ignore
    from psycopg2.extras import RealDictCursor  # type: ignore
    HAS_PG = True
except ImportError:
    HAS_PG = False

try:
    import redis  # type: ignore
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False

try:
    import chromadb  # type: ignore
    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False


class MemoryManager:
    """多项目记忆隔离管理器.

    每个项目 (小说) 拥有独立的:
    - 风格偏好 (tone, pacing, reference_author)
    - 角色档案 (name, role, traits)
    - 世界观设定 (period, power_system, location)
    - 剧情进度 (total_chapters, completed_chapters, current_conflict)
    - 对话历史 (短期, Redis)
    - 向量索引 (语义检索, Chroma)

    切换项目时:
    1. 清空短期对话记忆 (Redis flush)
    2. 加载新项目的完整长期记忆
    3. 返回给 Agent 的系统提示词 (含项目上下文)
    """

    def __init__(self):
        """初始化 PostgreSQL / Redis / Chroma 连接 (均不可用则降级)."""
        self._pg_conn = None
        self._redis_client = None
        self._chroma_client = None
        self._chroma_collection = None
        # 内存降级存储
        self._mem_conversations: dict[str, list[dict]] = {}
        self._mem_projects: dict[str, dict] = {}
        self._mem_characters: dict[str, list[dict]] = {}

        self._init_postgres()
        self._init_redis()
        self._init_chroma()

    # ===== 初始化 =====

    def _init_postgres(self):
        """初始化 PostgreSQL 连接并建表."""
        if not HAS_PG:
            logger.info("[memory] psycopg2 未安装, 降级到内存存储")
            return
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            logger.info("[memory] DATABASE_URL 未配置, 降级到内存存储")
            return
        try:
            self._pg_conn = psycopg2.connect(db_url)
            self._pg_conn.autocommit = True
            self._create_tables()
            logger.info("[memory] PostgreSQL 连接成功")
        except Exception as e:
            logger.warning(f"[memory] PostgreSQL 连接失败, 降级到内存: {e}")
            self._pg_conn = None

    def _create_tables(self):
        """创建数据库表 (如果不存在)."""
        if not self._pg_conn:
            return
        with self._pg_conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    project_id VARCHAR(64) PRIMARY KEY,
                    user_id VARCHAR(64) NOT NULL DEFAULT 'default',
                    project_name VARCHAR(255) NOT NULL,
                    genre VARCHAR(50),
                    style_preference JSONB DEFAULT '{}',
                    world_setting JSONB DEFAULT '{}',
                    plot_progress JSONB DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS characters (
                    id SERIAL PRIMARY KEY,
                    project_id VARCHAR(64) REFERENCES projects(project_id) ON DELETE CASCADE,
                    name VARCHAR(100) NOT NULL,
                    role VARCHAR(50),
                    traits JSONB DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chapters (
                    id SERIAL PRIMARY KEY,
                    project_id VARCHAR(64) REFERENCES projects(project_id) ON DELETE CASCADE,
                    chapter_number INT NOT NULL,
                    title VARCHAR(255),
                    content TEXT,
                    status VARCHAR(20) DEFAULT 'draft',
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

    def _init_redis(self):
        """初始化 Redis 连接."""
        if not HAS_REDIS:
            logger.info("[memory] redis 未安装, 降级到内存存储")
            return
        redis_url = os.environ.get("REDIS_URL")
        if not redis_url:
            logger.info("[memory] REDIS_URL 未配置, 降级到内存存储")
            return
        try:
            self._redis_client = redis.from_url(redis_url, decode_responses=True)
            self._redis_client.ping()
            logger.info("[memory] Redis 连接成功")
        except Exception as e:
            logger.warning(f"[memory] Redis 连接失败, 降级到内存: {e}")
            self._redis_client = None

    def _init_chroma(self):
        """初始化 Chroma 向量数据库."""
        if not HAS_CHROMA:
            logger.info("[memory] chromadb 未安装, 降级到关键词检索")
            return
        chroma_path = os.environ.get("CHROMA_PATH", "./data/chroma")
        try:
            os.makedirs(chroma_path, exist_ok=True)
            self._chroma_client = chromadb.PersistentClient(path=chroma_path)
            logger.info(f"[memory] Chroma 初始化成功 (path={chroma_path})")
        except Exception as e:
            logger.warning(f"[memory] Chroma 初始化失败, 降级到关键词检索: {e}")
            self._chroma_client = None

    # ===== 项目管理 =====

    def create_project(
        self, name: str, genre: str = "", style: Optional[dict] = None,
        world: Optional[dict] = None,
    ) -> str:
        """创建新项目, 返回 project_id."""
        project_id = str(uuid.uuid4())[:8]
        style = style or {}
        world = world or {}
        progress = {"total_chapters": 0, "completed_chapters": 0, "current_conflict": ""}

        if self._pg_conn:
            with self._pg_conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO projects (project_id, project_name, genre,
                       style_preference, world_setting, plot_progress)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (project_id, name, genre, json.dumps(style),
                     json.dumps(world), json.dumps(progress)),
                )
        else:
            self._mem_projects[project_id] = {
                "project_id": project_id,
                "project_name": name,
                "genre": genre,
                "style_preference": style,
                "world_setting": world,
                "plot_progress": progress,
                "created_at": time.time(),
                "updated_at": time.time(),
            }
            self._mem_characters[project_id] = []
        return project_id

    def get_project_memory(self, project_id: str) -> dict:
        """加载项目的完整长期记忆 (风格、角色、世界观、剧情进度)."""
        if self._pg_conn:
            with self._pg_conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM projects WHERE project_id = %s", (project_id,)
                )
                row = cur.fetchone()
                if not row:
                    return {}
                proj = dict(row)
                # 解析 JSONB 字段
                for k in ("style_preference", "world_setting", "plot_progress"):
                    v = proj.get(k)
                    if isinstance(v, str):
                        try:
                            proj[k] = json.loads(v)
                        except Exception:
                            proj[k] = {}
                return proj
        return self._mem_projects.get(project_id, {})

    def list_projects(self) -> list[dict]:
        """列出所有项目."""
        if self._pg_conn:
            with self._pg_conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT project_id, project_name, genre, created_at, updated_at "
                    "FROM projects ORDER BY updated_at DESC"
                )
                return [dict(r) for r in cur.fetchall()]
        return list(self._mem_projects.values())

    def update_project(self, project_id: str, updates: dict) -> bool:
        """更新项目信息 (风格、世界观、剧情进度等)."""
        if self._pg_conn:
            set_parts = []
            values = []
            for k, v in updates.items():
                if k in ("style_preference", "world_setting", "plot_progress"):
                    set_parts.append(f"{k} = %s")
                    values.append(json.dumps(v))
                elif k in ("project_name", "genre"):
                    set_parts.append(f"{k} = %s")
                    values.append(v)
            if not set_parts:
                return False
            set_parts.append("updated_at = NOW()")
            values.append(project_id)
            with self._pg_conn.cursor() as cur:
                cur.execute(
                    f"UPDATE projects SET {', '.join(set_parts)} WHERE project_id = %s",
                    values,
                )
                return cur.rowcount > 0
        # 内存降级
        proj = self._mem_projects.get(project_id)
        if not proj:
            return False
        proj.update(updates)
        proj["updated_at"] = time.time()
        return True

    def delete_project(self, project_id: str) -> None:
        """删除项目及其所有关联数据 (角色、章节、向量、对话)."""
        if self._pg_conn:
            with self._pg_conn.cursor() as cur:
                # ON DELETE CASCADE 会自动删除 characters 和 chapters
                cur.execute("DELETE FROM projects WHERE project_id = %s", (project_id,))
        else:
            self._mem_projects.pop(project_id, None)
            self._mem_characters.pop(project_id, None)
        # 清理 Redis 对话
        if self._redis_client:
            self._redis_client.delete(f"conv:{project_id}")
        # 清理 Chroma 集合
        if self._chroma_client:
            try:
                self._chroma_client.delete_collection(f"project_{project_id}")
            except Exception:
                pass

    # ===== 风格管理 =====

    def update_style(self, project_id: str, style_update: dict) -> None:
        """更新项目风格偏好 (tone, pacing, reference_author)."""
        current = self.get_project_memory(project_id)
        style = current.get("style_preference", {})
        style.update(style_update)
        self.update_project(project_id, {"style_preference": style})

    # ===== 角色管理 =====

    def add_character(self, project_id: str, character_data: dict) -> int:
        """添加角色到项目, 返回角色 ID."""
        name = character_data.get("name", "未命名")
        role = character_data.get("role", "")
        traits = character_data.get("traits", {})

        if self._pg_conn:
            with self._pg_conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO characters (project_id, name, role, traits)
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (project_id, name, role, json.dumps(traits)),
                )
                return cur.fetchone()[0]
        # 内存降级
        char_id = len(self._mem_characters.get(project_id, [])) + 1
        character_data["id"] = char_id
        self._mem_characters.setdefault(project_id, []).append(character_data)
        return char_id

    def get_characters(self, project_id: str) -> list[dict]:
        """获取项目所有角色."""
        if self._pg_conn:
            with self._pg_conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM characters WHERE project_id = %s ORDER BY created_at",
                    (project_id,),
                )
                rows = cur.fetchall()
                result = []
                for r in rows:
                    char = dict(r)
                    if isinstance(char.get("traits"), str):
                        try:
                            char["traits"] = json.loads(char["traits"])
                        except Exception:
                            pass
                    result.append(char)
                return result
        return self._mem_characters.get(project_id, [])

    # ===== 短期对话记忆 (Redis) =====

    def save_conversation(self, project_id: str, conversation: dict) -> None:
        """保存对话到短期记忆 (Redis)."""
        key = f"conv:{project_id}"
        conv_json = json.dumps(conversation, ensure_ascii=False)
        if self._redis_client:
            self._redis_client.lpush(key, conv_json)
            # 只保留最近 50 条
            self._redis_client.ltrim(key, 0, 49)
        else:
            self._mem_conversations.setdefault(project_id, []).insert(0, conversation)
            self._mem_conversations[project_id] = self._mem_conversations[project_id][:50]

    def get_conversations(self, project_id: str, limit: int = 10) -> list[dict]:
        """获取最近 N 轮对话."""
        if self._redis_client:
            key = f"conv:{project_id}"
            raw_list = self._redis_client.lrange(key, 0, limit - 1)
            return [json.loads(r) for r in raw_list]
        return self._mem_conversations.get(project_id, [])[:limit]

    # ===== 项目切换 =====

    def switch_project(self, project_id: str) -> dict:
        """切换项目: 清空短期记忆, 加载新项目完整记忆, 返回系统提示词.

        返回:
            {
                "project": {...},       # 项目完整记忆
                "characters": [...],    # 角色列表
                "conversations": [...], # 新项目的最近对话
                "system_prompt": str,   # 给 Agent 的系统提示词
            }
        """
        # 1. 加载新项目长期记忆
        project = self.get_project_memory(project_id)
        if not project:
            return {"error": f"项目 {project_id} 不存在"}

        # 2. 加载角色
        characters = self.get_characters(project_id)

        # 3. 加载新项目的对话历史 (不清空, 只是加载)
        conversations = self.get_conversations(project_id, limit=10)

        # 4. 构建 Agent 系统提示词
        style = project.get("style_preference", {})
        world = project.get("world_setting", {})
        progress = project.get("plot_progress", {})

        char_summary = "\n".join(
            f"  - {c.get('name', '?')} ({c.get('role', '?')}): "
            f"{json.dumps(c.get('traits', {}), ensure_ascii=False)}"
            for c in characters
        ) or "  (暂无角色)"

        system_prompt = f"""【当前项目上下文】
项目: {project.get('project_name', '未命名')}
类型: {project.get('genre', '未指定')}

【风格偏好】
语气: {style.get('tone', '默认')}
节奏: {style.get('pacing', '默认')}
参考作者: {style.get('reference_author', '无')}

【世界观设定】
时代: {world.get('period', '未设定')}
力量体系: {world.get('power_system', '未设定')}
地点: {world.get('location', '未设定')}

【剧情进度】
总章数: {progress.get('total_chapters', 0)}
已完成: {progress.get('completed_chapters', 0)}
当前冲突: {progress.get('current_conflict', '无')}

【角色档案】
{char_summary}
"""
        return {
            "project": project,
            "characters": characters,
            "conversations": conversations,
            "system_prompt": system_prompt,
        }

    # ===== 向量检索 (Chroma) =====

    def add_to_vector_store(
        self, project_id: str, documents: list[str], metadatas: Optional[list[dict]] = None,
    ) -> None:
        """向项目的向量集合添加文档 (角色描述/世界观/章节内容等)."""
        if not self._chroma_client:
            return
        try:
            collection = self._chroma_client.get_or_create_collection(
                name=f"project_{project_id}"
            )
            ids = [str(uuid.uuid4())[:12] for _ in documents]
            collection.add(
                documents=documents,
                metadatas=metadatas or [{} for _ in documents],
                ids=ids,
            )
        except Exception as e:
            logger.warning(f"[memory] 向量入库失败: {e}")

    def search_vector_store(
        self, project_id: str, query: str, n_results: int = 5,
    ) -> list[dict]:
        """语义检索项目相关文档."""
        if not self._chroma_client:
            return []
        try:
            collection = self._chroma_client.get_or_create_collection(
                name=f"project_{project_id}"
            )
            results = collection.query(query_texts=[query], n_results=n_results)
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            return [{"content": d, "metadata": m} for d, m in zip(docs, metas)]
        except Exception as e:
            logger.warning(f"[memory] 向量检索失败: {e}")
            return []

    # ===== 章节管理 =====

    def save_chapter(
        self, project_id: str, chapter_number: int, title: str, content: str,
        status: str = "draft",
    ) -> int:
        """保存章节到数据库."""
        if self._pg_conn:
            with self._pg_conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO chapters (project_id, chapter_number, title, content, status)
                       VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                    (project_id, chapter_number, title, content, status),
                )
                return cur.fetchone()[0]
        return 0

    def get_chapters(self, project_id: str) -> list[dict]:
        """获取项目所有章节."""
        if self._pg_conn:
            with self._pg_conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM chapters WHERE project_id = %s ORDER BY chapter_number",
                    (project_id,),
                )
                return [dict(r) for r in cur.fetchall()]
        return []

    def update_chapter(self, chapter_id: int, updates: dict) -> bool:
        """更新章节 (标题/正文/状态)."""
        if not self._pg_conn:
            return False
        set_parts = []
        values = []
        for k, v in updates.items():
            if k in ("title", "content", "status"):
                set_parts.append(f"{k} = %s")
                values.append(v)
        if not set_parts:
            return False
        values.append(chapter_id)
        with self._pg_conn.cursor() as cur:
            cur.execute(
                f"UPDATE chapters SET {', '.join(set_parts)} WHERE id = %s", values
            )
            return cur.rowcount > 0


# 全局单例
_manager: Optional[MemoryManager] = None


def get_memory_manager() -> MemoryManager:
    """获取全局 MemoryManager 单例."""
    global _manager
    if _manager is None:
        _manager = MemoryManager()
    return _manager
