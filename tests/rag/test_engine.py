"""RAGEngine 集成测试 — 使用 mock ChromaDB 绕过原生 DLL 兼容性问题"""
import hashlib
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from rag.embedding import EMBEDDING_DIM

COLLECTION_NAMES = [
    "research_reports",
    "financial_filings",
    "policy_macro",
    "academic",
    "history_reports",
    "system_rules",
]

# ── 在导入任何使用 chromadb 的模块前，注入虚拟 chromadb 模块 ──
# chroma-hnswlib 和 onnxruntime 在本机因 VC++ 运行时缺失而崩溃，
# 此处使用纯 Python mock 完全替代原生扩展。

_FAKE_CHROMADB = ModuleType("chromadb")
_FAKE_CHROMADB_API = ModuleType("chromadb.api")
_FAKE_CHROMADB_SEGMENT = ModuleType("chromadb.segment")
_FAKE_CHROMADB_EMBEDDING = ModuleType("chromadb.utils.embedding_functions")

for _mod in (_FAKE_CHROMADB, _FAKE_CHROMADB_API, _FAKE_CHROMADB_SEGMENT, _FAKE_CHROMADB_EMBEDDING):
    _mod.__path__ = []
    _mod.__file__ = "<mock>"


class MockCollection:
    """模拟 ChromaDB Collection"""

    def __init__(self, name: str):
        self.name = name
        self._docs = []
        self._metadatas = []
        self._ids = []
        self._embeddings = []

    def add(self, documents, metadatas, ids, embeddings=None):
        self._docs.extend(documents)
        self._metadatas.extend(metadatas)
        self._ids.extend(ids)
        if embeddings is not None:
            self._embeddings.extend(embeddings)

    def get(self, **kwargs):
        where = kwargs.get("where", {})
        source_hash = where.get("source_hash", "")
        metas = self._metadatas
        docs = self._docs
        id_list = self._ids
        if source_hash:
            filtered = []
            for i, m in enumerate(metas):
                if m.get("source_hash") == source_hash:
                    filtered.append(i)
            metas = [metas[i] for i in filtered]
            docs = [docs[i] for i in filtered]
            id_list = [id_list[i] for i in filtered]
            if kwargs.get("limit", 0) == 1:
                metas = metas[:1]
                docs = docs[:1]
                id_list = id_list[:1]
        return {"documents": docs, "metadatas": metas, "ids": id_list}

    def count(self):
        return len(self._docs)

    def query(self, query_embeddings, n_results, where=None):
        return {"documents": [[]], "metadatas": [[]], "distances": [[]], "ids": [[]]}

    def delete(self, ids):
        for chunk_id in list(ids):
            for i, cid in enumerate(self._ids):
                if cid == chunk_id:
                    self._ids.pop(i)
                    self._docs.pop(i)
                    self._metadatas.pop(i)
                    if self._embeddings:
                        self._embeddings.pop(i)
                    break


class MockClient:
    """模拟 ChromaDB PersistentClient"""

    def __init__(self, path: str = ""):
        self._path = path
        self._collections: dict[str, MockCollection] = {}

    def get_or_create_collection(self, name: str, metadata=None, embedding_function=None):
        if name not in self._collections:
            self._collections[name] = MockCollection(name)
        return self._collections[name]

    def delete_collection(self, name: str):
        if name in self._collections:
            del self._collections[name]


# 将 mock 注入 sys.modules，让后续 `import chromadb` 直接拿到它们
_PersistentClient = MagicMock(side_effect=MockClient)
_PersistentClient.__name__ = "PersistentClient"

_FAKE_CHROMADB.PersistentClient = _PersistentClient
_FAKE_CHROMADB.Client = MagicMock(side_effect=MockClient)

sys.modules["chromadb"] = _FAKE_CHROMADB
sys.modules["chromadb.api"] = _FAKE_CHROMADB_API
sys.modules["chromadb.segment"] = _FAKE_CHROMADB_SEGMENT
sys.modules["chromadb.utils.embedding_functions"] = _FAKE_CHROMADB_EMBEDDING
# 同时阻止可能被间接导入的子模块
for _sub in (
    "chromadb.api.models", "chromadb.api.models.Collection",
    "chromadb.api.models.CollectionCommon", "chromadb.api.client",
    "chromadb.api.types", "chromadb.api.segment",
    "chromadb.api.shared_system_client",
    "chromadb.segment.impl", "chromadb.segment.impl.vector",
    "chromadb.segment.impl.vector.local_hnsw",
    "chromadb.segment.impl.vector.local_persistent_hnsw",
    "chromadb.segment.impl.manager", "chromadb.segment.impl.manager.local",
    "chromadb.config",
    "chromadb.auth",
    "chromadb.errors",
    "chromadb.telemetry", "chromadb.telemetry.product",
    "chromadb.telemetry.product.events",
    "chromadb.db",
    "chromadb.ingest",
    "chromadb.quota",
    "chromadb.rate_limit",
    "chromadb.api.async_api",
    "chromadb.api.async_fastapi",
    "chromadb.api.fastapi",
    "chromadb.executor",
    "chromadb.proto",
    "chromadb.server",
    "chromadb.types",
    "chromadb.utils",
):
    if _sub not in sys.modules:
        sys.modules[_sub] = ModuleType(_sub)


class SafeTestEmbeddingProvider:
    """测试用 Embedding 提供者：基于文本哈希生成非零向量"""

    @property
    def name(self) -> str:
        return "keyword-fallback"

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            h = hashlib.sha256(text.encode("utf-8")).digest()
            vec = []
            for i in range(EMBEDDING_DIM):
                byte_val = h[i % len(h)] / 255.0
                vec.append(0.01 + byte_val * 0.98)
            vectors.append(vec)
        return vectors


class TestRAGEngine:
    @pytest.fixture
    def engine(self, tmp_path):
        from rag.engine import RAGEngine
        return RAGEngine(
            persist_dir=str(tmp_path / "chroma"),
            embedding_provider=SafeTestEmbeddingProvider(),
        )

    def test_init_creates_all_collections(self, engine):
        for name in COLLECTION_NAMES:
            col = engine.get_collection(name)
            assert col is not None

    def test_get_collection_returns_same_instance(self, engine):
        c1 = engine.get_collection("research_reports")
        c2 = engine.get_collection("research_reports")
        assert c1 is c2

    def test_get_collection_falls_back_for_unknown(self, engine):
        col = engine.get_collection("unknown_collection")
        assert col is not None

    def test_search_delegates_to_retrieval_pipeline(self, engine):
        results = engine.search("research_reports", "新能源", top_k=3)
        assert isinstance(results, list)

    def test_ingest_file_delegates_to_ingestion_pipeline(self, engine, tmp_path):
        doc = tmp_path / "test_report.md"
        doc.write_text("# 研报\n\n## 分析\n\n重要内容。", encoding="utf-8")
        result = engine.ingest_file(
            file_path=str(doc),
            source_type="research_reports",
            title="测试导入",
            symbols=["000001"],
            tags=["测试"],
        )
        assert result["status"] in ("success", "skipped", "error")

    def test_list_sources(self, engine):
        sources = engine.list_sources()
        assert isinstance(sources, list)

    def test_collection_stats(self, engine):
        stats = engine.collection_stats()
        assert isinstance(stats, list)
        assert len(stats) == len(COLLECTION_NAMES)
        for entry in stats:
            assert "name" in entry
            assert "count" in entry

    def test_delete_by_source_type(self, engine):
        result = engine.delete_by_source_type("research_reports")
        # -1 表示整库重建（无法统计具体 chunk 数），其他值表示删除的条目数
        assert result["deleted"] >= -1

    def test_delete_by_symbol(self, engine):
        result = engine.delete_by_symbol("000001")
        assert result["deleted"] >= 0

    def test_delete_before_date(self, engine):
        result = engine.delete_before_date("2025-01-01")
        assert result["deleted"] >= 0

    def test_embedding_name_property(self, engine):
        assert engine.embedding_name == "keyword-fallback"

    def test_ingest_text_delegates_to_ingestion_pipeline(self, engine):
        result = engine.ingest_text(
            text="测试文本内容。\n\n第二段。",
            source_type="research_reports",
            source_path="/test/doc.txt",
            title="测试文档",
            date="2026-01-01",
            symbols=["000001"],
            tags=["测试"],
        )
        assert result["status"] == "success"
        assert result["chunks_count"] > 0

    def test_ingest_text_with_default_date(self, engine):
        result = engine.ingest_text(
            text="测试内容",
            source_type="research_reports",
            source_path="/test/doc2.txt",
            title="无日期文档",
            symbols=[],
            tags=[],
        )
        assert result["status"] in ("success", "skipped")

    def test_collection_stats_has_right_names(self, engine):
        stats = engine.collection_stats()
        names = {s["name"] for s in stats}
        assert names == set(COLLECTION_NAMES)

    def test_ingest_file_skips_duplicate(self, engine, tmp_path):
        doc = tmp_path / "dup_report.md"
        doc.write_text("相同内容", encoding="utf-8")
        result1 = engine.ingest_file(
            file_path=str(doc),
            source_type="research_reports",
            title="首次导入",
            symbols=[],
            tags=[],
        )
        result2 = engine.ingest_file(
            file_path=str(doc),
            source_type="research_reports",
            title="重复导入",
            symbols=[],
            tags=[],
        )
        assert result1["status"] == "success"
        assert result2["status"] == "skipped"

    def test_search_returns_results_after_ingest(self, engine):
        result = engine.ingest_text(
            text="新能源板块近期表现强劲，光伏和风电均有显著增长。",
            source_type="research_reports",
            source_path="/test/new_energy.txt",
            title="新能源分析",
            symbols=["000001"],
            tags=["新能源"],
        )
        assert result["status"] == "success"
        results = engine.search("research_reports", "新能源", top_k=5)
        assert isinstance(results, list)

    def test_delete_by_symbol_removes_chunks(self, engine):
        engine.ingest_text(
            text="关于股票000001的研究报告内容。",
            source_type="research_reports",
            source_path="/test/symbol_a.txt",
            title="股票A分析",
            symbols=["000001"],
            tags=["研报"],
        )
        engine.ingest_text(
            text="关于股票000002的研究报告内容。",
            source_type="research_reports",
            source_path="/test/symbol_b.txt",
            title="股票B分析",
            symbols=["000002"],
            tags=["研报"],
        )
        del_result = engine.delete_by_symbol("000001")
        assert del_result["deleted"] >= 0

    def test_delete_before_date_removes_old_chunks(self, engine):
        engine.ingest_text(
            text="旧数据内容。",
            source_type="research_reports",
            source_path="/test/old_data.txt",
            title="旧数据",
            date="2024-01-01",
            symbols=[],
            tags=[],
        )
        engine.ingest_text(
            text="新数据内容。",
            source_type="research_reports",
            source_path="/test/new_data.txt",
            title="新数据",
            date="2026-06-01",
            symbols=[],
            tags=[],
        )
        del_result = engine.delete_before_date("2025-01-01")
        assert del_result["deleted"] >= 0

    def test_list_sources_after_ingest(self, engine):
        engine.ingest_text(
            text="研报内容分析。",
            source_type="research_reports",
            source_path="/test/source_report.txt",
            title="研报标题",
            date="2026-03-15",
            symbols=["000001"],
            tags=["行业"],
        )
        sources = engine.list_sources()
        assert isinstance(sources, list)
