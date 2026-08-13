"""IngestionPipeline 单元测试"""
import hashlib
from datetime import datetime

import pytest

from rag.ingestion import IngestionPipeline


class FakeEmbeddingProvider:
    name = "fake"
    def embed(self, texts):
        return [[0.1] * 384 for _ in texts]


class FakeCollection:
    """模拟 ChromaDB Collection"""
    def __init__(self):
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
        return {"documents": [], "metadatas": [], "ids": []}

    def count(self):
        return len(self._docs)


class TestIngestionPipeline:
    @pytest.fixture
    def pipeline(self):
        return IngestionPipeline(embedding_provider=FakeEmbeddingProvider())

    def test_compute_hash_returns_sha256(self, pipeline):
        content = "测试文档内容"
        h = pipeline.compute_hash(content)
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert h == expected
        assert len(h) == 64

    def test_compute_hash_is_deterministic(self, pipeline):
        content = "相同的文档内容"
        assert pipeline.compute_hash(content) == pipeline.compute_hash(content)

    def test_load_document_reads_text_file(self, pipeline, tmp_path):
        doc = tmp_path / "test.txt"
        doc.write_text("这是测试文档内容", encoding="utf-8")
        text = pipeline.load_document(str(doc))
        assert text == "这是测试文档内容"

    def test_load_document_reads_markdown_file(self, pipeline, tmp_path):
        doc = tmp_path / "report.md"
        doc.write_text("# 标题\n\n段落内容", encoding="utf-8")
        text = pipeline.load_document(str(doc))
        assert "# 标题" in text

    def test_load_document_raises_for_unknown_extension(self, pipeline, tmp_path):
        doc = tmp_path / "data.bin"
        doc.write_bytes(b"\x00\x01\x02")
        with pytest.raises(ValueError, match="不支持的文件格式"):
            pipeline.load_document(str(doc))

    def test_ingest_text_adds_to_collection(self, pipeline):
        collection = FakeCollection()
        now = datetime.now().astimezone().isoformat()
        pipeline.ingest_text(
            collection=collection,
            text="测试分块文本。\n\n第二段落内容。",
            source_type="policy_macro",
            source_path="/data/policy.txt",
            title="政策文件",
            date="2026-01-01",
            symbols=["000001"],
            tags=["政策"],
            ingested_at=now,
        )
        assert collection.count() > 0

    def test_ingest_text_skips_empty_content(self, pipeline):
        collection = FakeCollection()
        now = datetime.now().astimezone().isoformat()
        pipeline.ingest_text(
            collection=collection,
            text="   \n  ",
            source_type="policy_macro",
            source_path="/data/empty.txt",
            title="空文件",
            symbols=[],
            tags=[],
            ingested_at=now,
        )
        assert collection.count() == 0

    def test_is_duplicate_checks_hash(self, pipeline):
        content = "已有的文档"
        doc_hash = pipeline.compute_hash(content)

        class DupCollection:
            def get(self, **kwargs):
                return {"metadatas": [{"source_hash": doc_hash}]}

        collection = DupCollection()
        assert pipeline.is_duplicate(collection, doc_hash) is True

    def test_is_not_duplicate_for_new_hash(self, pipeline):
        class NewCollection:
            def get(self, **kwargs):
                return {"metadatas": []}

        collection = NewCollection()
        assert pipeline.is_duplicate(collection, "new_hash") is False

    def test_ingest_file_loads_chunks_and_adds(self, pipeline, tmp_path):
        doc = tmp_path / "report.md"
        doc.write_text("# 研报\n\n## 行业分析\n\n新能源板块表现强劲。\n\n## 风险\n\n市场波动。", encoding="utf-8")

        collection = FakeCollection()
        result = pipeline.ingest_file(
            collection=collection,
            file_path=str(doc),
            source_type="research_reports",
            title="测试研报",
            date="2026-01-01",
            symbols=[],
            tags=[],
        )
        assert result["chunks_count"] > 0
        assert result["source_hash"] is not None
        assert result["status"] == "success"
        assert collection.count() == result["chunks_count"]

    def test_ingest_file_skips_duplicate(self, pipeline, tmp_path):
        doc = tmp_path / "report.md"
        content = "唯一内容测试"
        doc.write_text(content, encoding="utf-8")
        doc_hash = pipeline.compute_hash(content)

        class DupCheckCollection(FakeCollection):
            def get(self, **kwargs):
                return {"metadatas": [{"source_hash": doc_hash}]}

        collection = DupCheckCollection()
        result = pipeline.ingest_file(
            collection=collection,
            file_path=str(doc),
            source_type="research_reports",
            title="重复研报",
            date="2026-01-01",
            symbols=[],
            tags=[],
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "duplicate"

    def test_ingest_directory_processes_all_files(self, pipeline, tmp_path):
        (tmp_path / "a.md").write_text("# A\n\n内容A", encoding="utf-8")
        (tmp_path / "b.md").write_text("# B\n\n内容B", encoding="utf-8")

        collection = FakeCollection()
        results = pipeline.ingest_directory(
            collection=collection,
            directory=str(tmp_path),
            source_type="research_reports",
            title_prefix="批量导入",
            symbols=[],
            tags=[],
        )
        assert len(results) == 2
        assert all(r["status"] in ("success", "skipped") for r in results)
        assert collection.count() >= 2
