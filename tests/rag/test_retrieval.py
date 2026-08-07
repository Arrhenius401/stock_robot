"""RetrievalPipeline 单元测试"""
import pytest
from rag.retrieval import RetrievalPipeline


class FakeCollection:
    """模拟 ChromaDB Collection"""
    def __init__(self, query_results=None):
        self._query_results = query_results or {
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
            "ids": [[]],
        }

    def query(self, query_embeddings, n_results, where=None):
        return self._query_results

    def get(self, **kwargs):
        return {"documents": [], "metadatas": [], "ids": []}


class FakeEmbeddingProvider:
    name = "fake"
    def embed(self, texts):
        return [[0.2] * 384 for _ in texts]


class TestRetrievalPipeline:
    @pytest.fixture
    def pipeline(self):
        return RetrievalPipeline(embedding_provider=FakeEmbeddingProvider())

    def test_search_returns_formatted_results(self, pipeline):
        collection = FakeCollection(query_results={
            "documents": [["这是第一个检索结果", "第二个结果内容"]],
            "metadatas": [[
                {"source_type": "research_reports", "title": "研报A", "source_hash": "h1", "chunk_index": 0, "source_path": "/data/a.md"},
                {"source_type": "research_reports", "title": "研报B", "source_hash": "h2", "chunk_index": 1, "source_path": "/data/b.md"},
            ]],
            "distances": [[0.2, 0.5]],
            "ids": [["h1_0", "h2_1"]],
        })
        results = pipeline.search(collection, "新能源行业前景", top_k=5)
        assert len(results) == 2
        assert results[0]["content"] == "这是第一个检索结果"
        assert results[0]["metadata"]["title"] == "研报A"
        assert results[0]["score"] == pytest.approx(0.8)

    def test_search_uses_2x_top_k_for_initial_query(self, pipeline):
        captured_n = []
        class CaptureCollection(FakeCollection):
            def query(self, query_embeddings, n_results, where=None):
                captured_n.append(n_results)
                return {"documents": [[]], "metadatas": [[]],
                        "distances": [[]], "ids": [[]]}
        collection = CaptureCollection()
        pipeline.search(collection, "测试查询", top_k=5)
        assert captured_n[0] == 10

    def test_search_with_filters(self, pipeline):
        captured_where = []
        class CaptureCollection(FakeCollection):
            def query(self, query_embeddings, n_results, where=None):
                captured_where.append(where)
                return {"documents": [[]], "metadatas": [[]],
                        "distances": [[]], "ids": [[]]}
        collection = CaptureCollection()
        pipeline.search(
            collection, "查询", top_k=5,
            filters={"source_type": "research_reports"},
        )
        assert captured_where[0] is not None
        assert "source_type" in captured_where[0]

    def test_search_with_symbol_filter(self, pipeline):
        captured_where = []
        class CaptureCollection(FakeCollection):
            def query(self, query_embeddings, n_results, where=None):
                captured_where.append(where)
                return {"documents": [[]], "metadatas": [[]],
                        "distances": [[]], "ids": [[]]}
        collection = CaptureCollection()
        pipeline.search(collection, "基本面分析", top_k=5, filters={"symbol": "000001"})
        assert captured_where[0] is not None

    def test_search_empty_results(self, pipeline):
        collection = FakeCollection(query_results={
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
            "ids": [[]],
        })
        results = pipeline.search(collection, "不存在的查询", top_k=5)
        assert results == []

    def test_keyword_search_fallback(self, pipeline):
        class KeywordCollection(FakeCollection):
            def get(self, **kwargs):
                return {
                    "documents": ["新能源板块表现强劲", "银行板块走弱", "新能源车销量增长"],
                    "metadatas": [
                        {"source_type": "research_reports", "title": "报告1", "source_hash": "h1"},
                        {"source_type": "research_reports", "title": "报告2", "source_hash": "h2"},
                        {"source_type": "research_reports", "title": "报告3", "source_hash": "h3"},
                    ],
                    "ids": ["id1", "id2", "id3"],
                }

        collection = KeywordCollection()
        results = pipeline.keyword_search(collection, "新能源", top_k=5)
        assert len(results) >= 2
        assert any("新能源" in r["content"] for r in results)
