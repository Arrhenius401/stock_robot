"""ChunkMetadata 数据结构单元测试"""
from rag.schemas import ChunkMetadata


class TestChunkMetadata:
    def test_create_with_required_fields(self):
        meta = ChunkMetadata(
            source_type="research_reports",
            source_path="/data/report.pdf",
            source_hash="abc123def456",
            title="2025年新能源行业策略报告",
            date="2025-01-15",
            symbols=["600519", "000858"],
            tags=["新能源", "策略"],
            chunk_index=0,
            ingested_at="2026-08-07T12:00:00",
        )
        assert meta.source_type == "research_reports"
        assert meta.source_path == "/data/report.pdf"
        assert meta.source_hash == "abc123def456"
        assert meta.title == "2025年新能源行业策略报告"
        assert meta.date == "2025-01-15"
        assert meta.symbols == ["600519", "000858"]
        assert meta.tags == ["新能源", "策略"]
        assert meta.chunk_index == 0
        assert meta.ingested_at == "2026-08-07T12:00:00"

    def test_date_defaults_to_none(self):
        meta = ChunkMetadata(
            source_type="policy_macro",
            source_path="/data/policy.txt",
            source_hash="xyz789",
            title="央行降准公告",
            symbols=[],
            tags=[],
            chunk_index=0,
            ingested_at="2026-08-07T12:00:00",
        )
        assert meta.date is None

    def test_symbols_defaults_to_empty_list(self):
        meta = ChunkMetadata(
            source_type="system_rules",
            source_path="/data/rule.yaml",
            source_hash="hash123",
            title="工具规则",
            chunk_index=0,
            ingested_at="2026-08-07T12:00:00",
        )
        assert meta.symbols == []

    def test_tags_defaults_to_empty_list(self):
        meta = ChunkMetadata(
            source_type="academic",
            source_path="/data/paper.pdf",
            source_hash="hash456",
            title="因子模型研究",
            chunk_index=0,
            ingested_at="2026-08-07T12:00:00",
        )
        assert meta.tags == []

    def test_to_dict_returns_all_fields(self):
        meta = ChunkMetadata(
            source_type="research_reports",
            source_path="/data/r.pdf",
            source_hash="h1",
            title="研报标题",
            date="2026-01-01",
            symbols=["000001"],
            tags=["银行"],
            chunk_index=2,
            ingested_at="2026-08-07T12:00:00",
        )
        d = meta.to_dict()
        assert d["source_type"] == "research_reports"
        assert d["source_hash"] == "h1"
        assert d["chunk_index"] == 2
        assert "symbols" in d

    def test_from_dict_roundtrip(self):
        original = ChunkMetadata(
            source_type="financial_filings",
            source_path="/data/filing.pdf",
            source_hash="h2",
            title="季度报告",
            date=None,
            symbols=["000001"],
            tags=["财报"],
            chunk_index=5,
            ingested_at="2026-08-07T12:00:00",
        )
        d = original.to_dict()
        restored = ChunkMetadata.from_dict(d)
        assert restored.source_type == original.source_type
        assert restored.source_hash == original.source_hash
        assert restored.symbols == original.symbols
        assert restored.date is None
        assert restored.chunk_index == 5
        assert restored.tags == original.tags

    def test_source_hash_is_required(self):
        """验证 source_hash 被设计为必填字段——缺省无法实例化"""
        import pytest
        with pytest.raises(TypeError):
            ChunkMetadata(
                source_type="research_reports",
                source_path="/data/r.pdf",
                title="无哈希",
                symbols=[],
                tags=[],
                chunk_index=0,
                ingested_at="2026-08-07T12:00:00",
            )
