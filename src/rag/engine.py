"""RAG 引擎入口 — ChromaDB 初始化、Collection 管理、高层 API"""
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from rag.embedding import EmbeddingProvider, create_embedding_provider
from rag.ingestion import IngestionPipeline
from rag.retrieval import RetrievalPipeline
from utils.paths import project_state_dir

logger = logging.getLogger(__name__)


def _patch_onnx_for_chromadb():
    """在 chromadb 导入前注入虚拟 onnxruntime，避免原生 DLL 加载失败。
    RAGEngine 使用自己的 embedding_provider，不依赖 chromadb 的默认 EmbeddingFunction。
    """
    if "onnxruntime" in sys.modules:
        return  # 已加载真实 onnxruntime，无需修补
    fake = ModuleType("onnxruntime.virtual")
    for attr in (
        "InferenceSession", "SessionOptions", "GraphOptimizationLevel",
        "get_available_providers", "get_device",
    ):
        setattr(fake, attr, None)
    sys.modules["onnxruntime"] = fake
    sys.modules["onnxruntime.capi"] = fake
    sys.modules["onnxruntime.capi._pybind_state"] = fake
    sys.modules["onnxruntime.transformers"] = fake


_patch_onnx_for_chromadb()

COLLECTION_NAMES = [
    "research_reports",
    "financial_filings",
    "policy_macro",
    "academic",
    "history_reports",
    "system_rules",
]

class RAGEngine:
    def __init__(
        self,
        persist_dir: str | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ):
        import chromadb

        persist_path = persist_dir or str(project_state_dir() / "chroma")
        Path(persist_path).mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=persist_path)
        self._embedding = embedding_provider or create_embedding_provider()
        self._ingestion = IngestionPipeline(self._embedding)
        self._retrieval = RetrievalPipeline(self._embedding)
        # chromadb Collection 无类型标注，统一按 Any 处理
        self._collections: dict[str, Any] = {}

        self._init_collections()
        logger.info(
            "RAG 引擎已初始化 (embedding=%s, persist=%s)",
            self._embedding.name,
            persist_path,
        )

    def get_collection(self, name: str) -> Any:
        if name not in self._collections:
            safe_name = name if name in COLLECTION_NAMES else "research_reports"
            self._collections[name] = self._client.get_or_create_collection(
                name=safe_name,
                metadata={"hnsw:space": "cosine"},
                embedding_function=None,
            )
        return self._collections[name]

    def _init_collections(self):
        for name in COLLECTION_NAMES:
            self._collections[name] = self._client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
                embedding_function=None,
            )

    def search(
        self,
        collection_name: str,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[dict]:
        collection = self.get_collection(collection_name)
        return self._retrieval.search(collection, query, top_k, filters)

    def ingest_file(
        self,
        file_path: str,
        source_type: str,
        title: str = "",
        date: str = "",
        symbols: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        collection = self.get_collection(source_type)
        return self._ingestion.ingest_file(
            collection=collection,
            file_path=file_path,
            source_type=source_type,
            title=title,
            date=date,
            symbols=symbols,
            tags=tags,
        )

    def ingest_text(
        self,
        text: str,
        source_type: str,
        source_path: str,
        title: str,
        date: str | None = None,
        symbols: list[str] | None = None,
        tags: list[str] | None = None,
        ingested_at: str = "",
    ) -> dict:
        from datetime import datetime
        collection = self.get_collection(source_type)
        return self._ingestion.ingest_text(
            collection=collection,
            text=text,
            source_type=source_type,
            source_path=source_path,
            title=title,
            date=date,
            symbols=symbols or [],
            tags=tags or [],
            ingested_at=ingested_at or datetime.now().astimezone().isoformat(),
        )

    def ingest_directory(
        self,
        directory: str,
        source_type: str,
        title_prefix: str = "",
        symbols: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> list[dict]:
        collection = self.get_collection(source_type)
        return self._ingestion.ingest_directory(
            collection=collection,
            directory=directory,
            source_type=source_type,
            title_prefix=title_prefix,
            symbols=symbols,
            tags=tags,
        )

    def list_sources(self) -> list[dict]:
        sources = []
        for name in COLLECTION_NAMES:
            collection = self.get_collection(name)
            try:
                data = collection.get()
                seen = set()
                for meta in data.get("metadatas", []):
                    source_hash = meta.get("source_hash", "")
                    if source_hash and source_hash not in seen:
                        seen.add(source_hash)
                        sources.append({
                            "collection": name,
                            "title": meta.get("title", ""),
                            "source_path": meta.get("source_path", ""),
                            "source_hash": source_hash,
                            "date": meta.get("date"),
                            "symbols": meta.get("symbols", []),
                            "tags": meta.get("tags", []),
                            "chunks_count": sum(
                                1 for m in data.get("metadatas", [])
                                if m.get("source_hash") == source_hash
                            ),
                        })
            except Exception:  # noqa: BLE001 — 单知识库读取失败不阻断清单
                logger.debug("读取知识库 %s 失败", name)
        return sources

    def collection_stats(self) -> list[dict]:
        stats = []
        for name in COLLECTION_NAMES:
            collection = self.get_collection(name)
            try:
                count = collection.count()
            except Exception:  # noqa: BLE001 — 计数失败按 0 处理
                logger.debug("统计知识库 %s 失败", name)
                count = 0
            stats.append({"name": name, "count": count})
        return stats

    def delete_by_source_type(self, source_type: str) -> dict:
        if source_type not in COLLECTION_NAMES:
            return {"deleted": 0, "error": f"未知 source_type: {source_type}"}
        try:
            self._client.delete_collection(name=source_type)
            self._collections[source_type] = self._client.get_or_create_collection(
                name=source_type,
                metadata={"hnsw:space": "cosine"},
                embedding_function=None,
            )
            return {"deleted": -1, "note": f"Collection {source_type} 已重建（全量删除）"}
        except Exception as e:  # noqa: BLE001 — chromadb 异常统一转错误结果
            return {"deleted": 0, "error": str(e)}

    def delete_by_symbol(self, symbol: str) -> dict:
        deleted = 0
        for name in COLLECTION_NAMES:
            collection = self.get_collection(name)
            try:
                data = collection.get()
                for i, meta in enumerate(data.get("metadatas", [])):
                    if symbol in meta.get("symbols", []):
                        chunk_id = data.get("ids", [])[i]
                        collection.delete(ids=[chunk_id])
                        deleted += 1
            except Exception:  # noqa: BLE001 — 单知识库删除失败不阻断其余
                logger.debug("按代码删除知识库 %s 失败", name)
        return {"deleted": deleted}

    def delete_before_date(self, before_date: str) -> dict:
        deleted = 0
        for name in COLLECTION_NAMES:
            collection = self.get_collection(name)
            try:
                data = collection.get()
                for i, meta in enumerate(data.get("metadatas", [])):
                    doc_date = meta.get("date", "")
                    if doc_date and doc_date < before_date:
                        chunk_id = data.get("ids", [])[i]
                        collection.delete(ids=[chunk_id])
                        deleted += 1
            except Exception:  # noqa: BLE001 — 单知识库删除失败不阻断其余
                logger.debug("按日期删除知识库 %s 失败", name)
        return {"deleted": deleted}

    @property
    def embedding_name(self) -> str:
        return self._embedding.name
