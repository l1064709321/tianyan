"""向量检索模块 (改进: 原版用关键词匹配, 精度低)

功能:
- 基于 embedding 的语义检索 (替代关键词匹配)
- 支持混合检索: 向量相似度 + 关键词权重
- 支持多种 embedding 后端: OpenAI / 硅基流动 / 本地模型

灵感来源:
- happy-llm 的 TinyRAG 架构
- LangChain 的向量检索模式
- AutoGen 的 Memory 协议
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

import numpy as np

from .config import get_settings

logger = logging.getLogger("tianyan")

# 缓存 embedding 结果 (避免重复调用 API)
_embedding_cache: dict[str, list[float]] = {}


class BaseEmbedding:
    """Embedding 基类"""

    async def get_embedding(self, text: str) -> list[float]:
        raise NotImplementedError

    @staticmethod
    def cosine_similarity(v1: list[float], v2: list[float]) -> float:
        """计算余弦相似度"""
        a = np.array(v1, dtype=np.float32)
        b = np.array(v2, dtype=np.float32)
        if not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):
            return 0.0
        dot = np.dot(a, b)
        norm = np.linalg.norm(a) * np.linalg.norm(b)
        return float(dot / norm) if norm > 0 else 0.0


class OpenAICompatibleEmbedding(BaseEmbedding):
    """OpenAI 兼容的 Embedding API (支持硅基流动/OpenRouter 等)"""

    def __init__(self, api_key: str = "", base_url: str = "", model: str = "BAAI/bge-m3"):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    async def get_embedding(self, text: str) -> list[float]:
        cache_key = f"{self.model}:{text[:100]}"
        if cache_key in _embedding_cache:
            return _embedding_cache[cache_key]

        try:
            import httpx
            headers = {"Authorization": f"Bearer {self.api_key}"}
            payload = {"input": text.replace("\n", " "), "model": self.model}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.base_url}/embeddings",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                embedding = data["data"][0]["embedding"]
                _embedding_cache[cache_key] = embedding
                return embedding
        except Exception as e:
            logger.warning(f"[向量] Embedding API 调用失败: {e}")
            return []


class LocalTFIDFEmbedding(BaseEmbedding):
    """本地 TF-IDF 降级方案 (无需 API, 纯 Python 实现)"""

    def __init__(self):
        self._idf_cache: dict[str, float] = {}

    async def get_embedding(self, text: str) -> list[float]:
        """用字符级 n-gram 生成简单向量 (256 维)"""
        dims = 256
        vec = [0.0] * dims
        # 字符级 2-gram 哈希
        for i in range(len(text) - 1):
            bigram = text[i:i+2]
            h = hash(bigram) % dims
            vec[h] += 1.0
        # L2 归一化
        norm = sum(v*v for v in vec) ** 0.5
        if norm > 0:
            vec = [v/norm for v in vec]
        return vec


def get_embedding_model() -> BaseEmbedding:
    """获取 embedding 模型 (优先 API, 降级到本地)"""
    s = get_settings()

    # 尝试从配置中获取 embedding 相关信息
    for m in s.models:
        if m.api_key and m.api_base:
            # 优先使用硅基流动的免费 embedding 模型
            if "siliconflow" in (m.api_base or ""):
                return OpenAICompatibleEmbedding(
                    api_key=m.api_key,
                    base_url=m.api_base.replace("/v1", ""),
                    model="BAAI/bge-m3",
                )

    # 降级到本地 TF-IDF
    logger.info("[向量] 无可用 Embedding API, 降级到本地 TF-IDF")
    return LocalTFIDFEmbedding()


class VectorStore:
    """向量数据库 (轻量级, 无外部依赖)"""

    def __init__(self):
        self.documents: list[str] = []
        self.metadata: list[dict] = []
        self.vectors: list[list[float]] = []

    def add(self, text: str, metadata: dict = None, vector: list[float] = None):
        """添加文档"""
        self.documents.append(text)
        self.metadata.append(metadata or {})
        if vector:
            self.vectors.append(vector)

    async def build_index(self, embedding_model: BaseEmbedding = None):
        """为所有文档构建向量索引"""
        if not embedding_model:
            embedding_model = get_embedding_model()

        for i, doc in enumerate(self.documents):
            if i >= len(self.vectors) or not self.vectors[i]:
                vec = await embedding_model.get_embedding(doc)
                if i < len(self.vectors):
                    self.vectors[i] = vec
                else:
                    self.vectors.append(vec)

    async def search(
        self,
        query: str,
        embedding_model: BaseEmbedding = None,
        top_k: int = 5,
        min_score: float = 0.1,
    ) -> list[dict]:
        """语义检索"""
        if not embedding_model:
            embedding_model = get_embedding_model()

        query_vec = await embedding_model.get_embedding(query)
        if not query_vec:
            # embedding 失败, 降级到关键词
            return self._keyword_search(query, top_k)

        results = []
        for i, doc_vec in enumerate(self.vectors):
            if not doc_vec:
                continue
            score = BaseEmbedding.cosine_similarity(query_vec, doc_vec)
            if score >= min_score:
                results.append({
                    "text": self.documents[i],
                    "metadata": self.metadata[i],
                    "score": round(score, 4),
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def _keyword_search(self, query: str, top_k: int) -> list[dict]:
        """关键词降级检索"""
        query_lower = query.lower()
        tokens = [t for t in query_lower.split() if len(t) > 1]

        scored = []
        for i, doc in enumerate(self.documents):
            doc_lower = doc.lower()
            score = sum(doc_lower.count(t) for t in tokens)
            if score > 0:
                scored.append({
                    "text": doc,
                    "metadata": self.metadata[i],
                    "score": round(score / max(len(doc), 1) * 100, 4),
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def save(self, path: str):
        """持久化到文件"""
        data = {
            "documents": self.documents,
            "metadata": self.metadata,
            "vectors": self.vectors,
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def load(self, path: str):
        """从文件加载"""
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.documents = data.get("documents", [])
        self.metadata = data.get("metadata", [])
        self.vectors = data.get("vectors", [])


async def hybrid_retrieve(
    pid: str,
    query: str,
    top_k: int = 6,
    use_vector: bool = True,
) -> str:
    """混合检索: 向量 + 关键词, 返回拼接的上下文文本。

    改进: 原版只有关键词匹配, 现在加上向量语义检索。
    """
    from . import store

    chunks = store.list_chunks(pid)
    chapters = store.list_chapters(pid)

    if not chunks and not chapters:
        return "(无可用上文,将自由创作)"

    results = []

    if use_vector and chunks:
        # 向量检索
        try:
            vs = VectorStore()
            for ch in chunks:
                vs.add(ch["text"], {"source": ch["source"], "idx": ch["idx"]})

            embedding_model = get_embedding_model()
            await vs.build_index(embedding_model)
            vector_results = await vs.search(query, embedding_model, top_k=top_k)

            for r in vector_results:
                results.append({
                    "text": r["text"],
                    "source": r["metadata"].get("source", ""),
                    "score": r["score"],
                    "method": "vector",
                })
        except Exception as e:
            logger.warning(f"[向量检索] 失败: {e}, 降级到关键词")

    # 关键词检索 (补充)
    if len(results) < top_k:
        from .tools import _keyword_score
        keyword_chunks = []
        for ch in chunks:
            sc = _keyword_score(query, ch["text"])
            if sc > 0:
                keyword_chunks.append((sc, ch))
        keyword_chunks.sort(key=lambda x: x[0], reverse=True)

        for sc, ch in keyword_chunks[:top_k - len(results)]:
            # 去重
            if not any(r["text"] == ch["text"] for r in results):
                results.append({
                    "text": ch["text"],
                    "source": ch["source"],
                    "score": round(sc, 4),
                    "method": "keyword",
                })

    # 最近章节尾部 (保证续写连续性)
    if chapters:
        last = chapters[-1]
        if last.get("content"):
            tail = last["content"][-1500:]
            results.append({
                "text": tail,
                "source": f"最近章节《{last['title']}》结尾",
                "score": 1.0,
                "method": "recency",
            })

    # 拼接结果
    parts = []
    for r in results[:top_k]:
        src = r.get("source", "")
        method = r.get("method", "")
        parts.append(f"〔来源: {src} | 方法: {method} | 相关度: {r['score']}〕\n{r['text']}")

    return "\n\n".join(parts) if parts else "(无可用上文)"
