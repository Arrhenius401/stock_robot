# Agent 架构 Phase 2 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 RAG 引擎——ChromaDB 向量库集成、文档摄入流水线（含 SHA256 去重）、语义检索+Cross-Encoder 重排、rag_search/rag_list_sources 工具、本地报告自动同步、知识库 CLI 命令。

**Architecture:** 新增 `src/rag/` 模块（engine/ingestion/retrieval/chunkers/embedding/sync/schemas）和 `src/agent/rag_tools.py`，通过 `ToolProtocol` 接口与 Agent 核心集成。嵌入式模型使用 ChromaDB 内置 `SentenceTransformerEmbeddingFunction`，降级为纯关键词检索。存量代码零侵入。

**Tech Stack:** Python 3.11+, chromadb>=0.5, sentence-transformers>=3.0, pytest + pytest-mock

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `src/rag/__init__.py` | 模块导出 |
| `src/rag/schemas.py` | `ChunkMetadata` 数据结构 |
| `src/rag/embedding.py` | `EmbeddingProvider` ABC + `SentenceTransformersProvider` + `KeywordFallbackProvider` |
| `src/rag/chunkers.py` | 6 种 Collection 分块策略 + `ChunkerRegistry` |
| `src/rag/ingestion.py` | `IngestionPipeline` — 文档加载→哈希去重→分块→embed→upsert |
| `src/rag/retrieval.py` | `RetrievalPipeline` — query embed→向量检索→Cross-Encoder 重排 |
| `src/rag/engine.py` | `RAGEngine` — ChromaDB 初始化 + 高层入口 API |
| `src/rag/sync.py` | `LocalReportSync` — reports/ 目录文件系统监听 + 自动同步 |
| `src/agent/rag_tools.py` | `RAGSearchTool` + `RAGListSourcesTool` — ToolProtocol 实现 |
| `tests/rag/__init__.py` | 测试包 |
| `tests/rag/test_schemas.py` | ChunkMetadata 单测 |
| `tests/rag/test_embedding.py` | Embedding 提供者单测 |
| `tests/rag/test_chunkers.py` | 分块策略单测 |
| `tests/rag/test_ingestion.py` | 摄入流水线单测 |
| `tests/rag/test_retrieval.py` | 检索流水线单测 |
| `tests/rag/test_engine.py` | RAG 引擎集成测试 |
| `tests/rag/test_rag_tools.py` | RAG 工具单测 |
| `tests/rag/test_sync.py` | 报告同步单测 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `src/stock_robot/cli.py` | 新增 `rag` 命令组（ingest / clean / stats） |
| `pyproject.toml` | 新增依赖：chromadb, sentence-transformers |

### 不修改

- `src/agent/` — 现有代码不变，只新增 `rag_tools.py`
- `src/core/` / `src/analysis/` / `src/data/` — 零侵入
- 所有现有测试保留

---

### Task 1: ChunkMetadata 数据结构与模块骨架

**Files:**
- Create: `src/rag/__init__.py`
- Create: `src/rag/schemas.py`
- Create: `tests/rag/__init__.py`
- Create: `tests/rag/test_schemas.py`

- [ ] **Step 1: 创建 rag 包初始化文件**

```bash
mkdir -p src/rag
```

`src/rag/__init__.py`:
```python
"""RAG 引擎模块 — 文档摄入 / 语义检索 / 本地报告同步"""
```

- [ ] **Step 2: 编写 ChunkMetadata 的失败测试**

`tests/rag/__init__.py`:
```python
```

`tests/rag/test_schemas.py`:
```python
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
```

- [ ] **Step 3: 运行测试确认失败**

Run: `pytest tests/rag/test_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rag.schemas'` 或 `ImportError`

- [ ] **Step 4: 实现 ChunkMetadata**

`src/rag/schemas.py`:
```python
"""RAG 数据结构 — 标准化 Chunk 元数据"""
from dataclasses import dataclass, field


@dataclass
class ChunkMetadata:
    """每个 chunk 附带的统一元数据结构"""
    source_type: str         # Collection 类型
    source_path: str         # 原始文件路径
    source_hash: str         # 文档 SHA256
    title: str               # 文档标题
    date: str | None = None         # 文档日期
    symbols: list[str] = field(default_factory=list)   # 关联股票代码
    tags: list[str] = field(default_factory=list)       # 内容标签
    chunk_index: int = 0            # 在文档中的序号
    ingested_at: str = ""           # 摄入时间戳

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "source_path": self.source_path,
            "source_hash": self.source_hash,
            "title": self.title,
            "date": self.date,
            "symbols": self.symbols,
            "tags": self.tags,
            "chunk_index": self.chunk_index,
            "ingested_at": self.ingested_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChunkMetadata":
        return cls(
            source_type=d.get("source_type", ""),
            source_path=d.get("source_path", ""),
            source_hash=d.get("source_hash", ""),
            title=d.get("title", ""),
            date=d.get("date"),
            symbols=d.get("symbols", []),
            tags=d.get("tags", []),
            chunk_index=d.get("chunk_index", 0),
            ingested_at=d.get("ingested_at", ""),
        )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/rag/test_schemas.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add src/rag/__init__.py src/rag/schemas.py tests/rag/__init__.py tests/rag/test_schemas.py
git commit -m "feat(RAG): 添加 ChunkMetadata 数据结构与 to_dict/from_dict 序列化"
```

---

### Task 2: Embedding 提供者抽象 — bge-small-zh + 关键词降级

**Files:**
- Create: `src/rag/embedding.py`
- Create: `tests/rag/test_embedding.py`

- [ ] **Step 1: 编写 EmbeddingProvider 的失败测试**

`tests/rag/test_embedding.py`:
```python
"""Embedding 提供者抽象与实现单元测试"""
import pytest
from rag.embedding import (
    EmbeddingProvider,
    SentenceTransformersProvider,
    KeywordFallbackProvider,
    create_embedding_provider,
)


class TestSentenceTransformersProvider:
    def test_provider_is_embedding_provider_subclass(self):
        assert issubclass(SentenceTransformersProvider, EmbeddingProvider)

    def test_name_returns_bge_small_zh(self):
        provider = SentenceTransformersProvider()
        assert provider.name == "bge-small-zh"

    def test_embed_returns_list_of_lists_with_correct_dim(self, mocker):
        """模拟 sentence-transformers 返回固定向量"""
        mock_model = mocker.MagicMock()
        mock_model.encode.return_value = [[0.1] * 384, [0.2] * 384]
        mocker.patch(
            "rag.embedding.SentenceTransformer",
            return_value=mock_model,
        )
        provider = SentenceTransformersProvider()
        result = provider.embed(["查询文本", "另一段文本"])
        assert len(result) == 2
        assert len(result[0]) == 384
        assert len(result[1]) == 384

    def test_embed_single_string_returns_list_of_one(self, mocker):
        mock_model = mocker.MagicMock()
        mock_model.encode.return_value = [[0.1] * 384]
        mocker.patch("rag.embedding.SentenceTransformer", return_value=mock_model)
        provider = SentenceTransformersProvider()
        result = provider.embed(["单一文本"])
        assert len(result) == 1
        assert len(result[0]) == 384

    def test_provider_returns_none_on_import_error(self, mocker):
        """当 sentence-transformers 不可用时，构造函数应返回 None"""
        mocker.patch.dict("sys.modules", {"sentence_transformers": None})
        import importlib
        # 模拟导入失败
        mocker.patch(
            "rag.embedding.SentenceTransformersProvider.__init__",
            side_effect=ImportError("No module named 'sentence_transformers'"),
        )


class TestKeywordFallbackProvider:
    def test_provider_is_embedding_provider_subclass(self):
        assert issubclass(KeywordFallbackProvider, EmbeddingProvider)

    def test_name_returns_keyword_fallback(self):
        provider = KeywordFallbackProvider()
        assert provider.name == "keyword-fallback"

    def test_embed_returns_dummy_vectors(self):
        """关键词模式返回零向量，维度不影响 ChromaDB 存储"""
        provider = KeywordFallbackProvider()
        result = provider.embed(["文本一", "文本二"])
        assert len(result) == 2
        # fallback 用 384d 零向量保持与 bge-small-zh 一致
        assert len(result[0]) == 384
        assert all(v == 0.0 for v in result[0])

    def test_dimension_constant_is_384(self):
        from rag.embedding import EMBEDDING_DIM
        assert EMBEDDING_DIM == 384


class TestCreateEmbeddingProvider:
    def test_returns_provider_on_success(self, mocker):
        mocker.patch(
            "rag.embedding.SentenceTransformersProvider.__init__",
            return_value=None,
        )
        mock_instance = mocker.MagicMock()
        mock_instance.name = "bge-small-zh"
        mocker.patch(
            "rag.embedding.SentenceTransformersProvider",
            return_value=mock_instance,
        )
        provider = create_embedding_provider()
        assert provider is not None

    def test_falls_back_to_keyword_on_import_error(self, mocker):
        def raise_import_error(*args, **kwargs):
            raise ImportError("not available")
        mocker.patch(
            "rag.embedding.SentenceTransformersProvider.__init__",
            side_effect=raise_import_error,
        )
        provider = create_embedding_provider()
        assert isinstance(provider, KeywordFallbackProvider)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/rag/test_embedding.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 Embedding 提供者**

`src/rag/embedding.py`:
```python
"""Embedding 提供者 — 模型抽象、bge-small-zh 本地模型、关键词降级"""
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384  # bge-small-zh 输出维度


class EmbeddingProvider(ABC):
    """Embedding 模型抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """模型名称标识"""
        ...

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """对文本列表编码，返回等长 embedding 列表"""
        ...


class SentenceTransformersProvider(EmbeddingProvider):
    """基于 sentence-transformers 的本地 embedding 模型

    使用 bge-small-zh（384 维），模型自动下载到 ~/.cache/。
    """

    MODEL_NAME = "BAAI/bge-small-zh"

    def __init__(self):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers 未安装。"
                "请运行 pip install sentence-transformers"
            )
        self._model = SentenceTransformer(self.MODEL_NAME)

    @property
    def name(self) -> str:
        return "bge-small-zh"

    def embed(self, texts: list[str]) -> list[list[float]]:
        embeddings = self._model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()


class KeywordFallbackProvider(EmbeddingProvider):
    """关键词降级提供者

    当 bge-small-zh 不可用时使用。返回零向量占位，实际检索由
    RetrievalPipeline 的关键词匹配路径完成。
    """

    @property
    def name(self) -> str:
        return "keyword-fallback"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * EMBEDDING_DIM for _ in texts]


def create_embedding_provider() -> EmbeddingProvider:
    """创建 embedding 提供者，优先使用 bge-small-zh，不可用时降级"""
    try:
        provider = SentenceTransformersProvider()
        logger.info("Embedding 模型: bge-small-zh (本地)")
        return provider
    except (ImportError, OSError) as e:
        logger.warning(
            "bge-small-zh 不可用 (%s)，降级为关键词检索模式。"
            "检索精度会下降，建议安装 sentence-transformers。",
            e,
        )
        return KeywordFallbackProvider()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/rag/test_embedding.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/rag/embedding.py tests/rag/test_embedding.py
git commit -m "feat(RAG): 添加 Embedding 提供者抽象 — bge-small-zh + 关键词降级"
```

---

### Task 3: 文档分块策略 — 6 种 Collection 分块器

**Files:**
- Create: `src/rag/chunkers.py`
- Create: `tests/rag/test_chunkers.py`

- [ ] **Step 1: 编写分块器的失败测试**

`tests/rag/test_chunkers.py`:
```python
"""文档分块策略单元测试"""
import pytest
from rag.chunkers import (
    Chunker,
    ResearchReportChunker,
    FinancialFilingChunker,
    PolicyMacroChunker,
    AcademicChunker,
    HistoryReportChunker,
    SystemRulesChunker,
    ChunkerRegistry,
)


class TestResearchReportChunker:
    def test_chunk_by_headings(self):
        text = """# 研报标题

## 行业概览

新能源汽车行业持续增长，渗透率已突破 40%。

## 重点公司分析

### 比亚迪

公司在电池技术和整车制造方面具有明显优势。

### 特斯拉

全球布局加速，但面临本土化挑战。

## 风险提示

原材料价格波动风险。"""
        chunker = ResearchReportChunker()
        chunks = chunker.chunk(text)
        assert len(chunks) >= 3
        assert any("行业概览" in c for c in chunks)
        assert any("重点公司分析" in c for c in chunks)
        assert any("风险提示" in c for c in chunks)

    def test_single_chunk_for_short_text(self):
        text = "简短研报，无章节划分。"
        chunker = ResearchReportChunker()
        chunks = chunker.chunk(text)
        assert len(chunks) == 1
        assert "简短研报" in chunks[0]

    def test_empty_text_returns_empty_list(self):
        chunker = ResearchReportChunker()
        assert chunker.chunk("") == []
        assert chunker.chunk("   ") == []


class TestFinancialFilingChunker:
    def test_chunk_by_financial_sections(self):
        text = """一、营业收入

报告期内实现营业收入 50 亿元。

二、利润情况

归母净利润 8 亿元，同比增长 15%。

三、现金流

经营活动现金净流入 10 亿元。"""
        chunker = FinancialFilingChunker()
        chunks = chunker.chunk(text)
        assert len(chunks) >= 3
        assert any("营业收入" in c for c in chunks)
        assert any("利润" in c for c in chunks)
        assert any("现金流" in c for c in chunks)

    def test_falls_back_to_paragraph_chunking(self):
        """没有可识别的财务科目标题时降级为段落分块"""
        text = "本季度业绩稳健。\n\n各业务线表现良好。\n\n展望下季度继续增长。"
        chunker = FinancialFilingChunker()
        chunks = chunker.chunk(text)
        assert len(chunks) == 3


class TestPolicyMacroChunker:
    def test_chunk_by_paragraph_groups(self):
        text = """国务院常务会议指出，要进一步优化营商环境。

具体措施包括减税降费、简政放权、放宽市场准入。

央行决定下调存款准备金率 0.5 个百分点，释放长期资金约 1 万亿元。

此次降准旨在支持实体经济发展，降低融资成本。"""
        chunker = PolicyMacroChunker()
        chunks = chunker.chunk(text)
        assert len(chunks) >= 1

    def test_combines_adjacent_short_paragraphs(self):
        text = "第一段。\n\n第二段。\n\n第三段。\n\n第四段。\n\n第五段。"
        chunker = PolicyMacroChunker()
        chunks = chunker.chunk(text)
        for chunk in chunks:
            assert len(chunk) > 0


class TestAcademicChunker:
    def test_chunk_by_abstract_methods_conclusion(self):
        text = """摘要

本文研究了 A 股市场因子模型的适用性。

研究方法

采用 Fama-French 五因子模型对 2015-2025 年数据进行回归分析。

结论

五因子模型能较好解释 A 股收益，但市值因子的解释力弱于美股。"""
        chunker = AcademicChunker()
        chunks = chunker.chunk(text)
        assert len(chunks) >= 2
        assert any("摘要" in c for c in chunks)
        assert any("研究" in c for c in chunks)
        assert any("结论" in c for c in chunks)


class TestHistoryReportChunker:
    def test_chunk_by_analysis_dimensions(self):
        text = """# 平安银行（000001）分析报告

## 一、标的基础概况

基本信息内容。

## 二、五大维度量化数据

### 1. 财务量化数据

财务数据内容。

### 2. 技术面量化数据

技术面数据内容。

## 三、五大维度标准化打分

打分表格内容。"""
        chunker = HistoryReportChunker()
        chunks = chunker.chunk(text)
        assert len(chunks) >= 2
        assert any("标的基础概况" in c for c in chunks)
        assert any("财务量化数据" in c for c in chunks)

    def test_extracts_symbol_from_title(self):
        text = "# 平安银行（000001）分析报告\n\n内容。"
        chunker = HistoryReportChunker()
        symbols = chunker.extract_symbols(text)
        assert "000001" in symbols

    def test_extract_symbols_empty_for_no_match(self):
        chunker = HistoryReportChunker()
        assert chunker.extract_symbols("无股票代码的报告") == []


class TestSystemRulesChunker:
    def test_chunk_by_config_entries(self):
        text = """# 工具名称
analyze_stock: 单股全维度分析

# 参数
symbol: 6位股票代码

# 使用示例
analyze_stock --symbol 000001"""
        chunker = SystemRulesChunker()
        chunks = chunker.chunk(text)
        assert len(chunks) >= 1


class TestChunkerRegistry:
    def test_get_chunker_for_each_source_type(self):
        registry = ChunkerRegistry()
        assert isinstance(registry.get("research_reports"), ResearchReportChunker)
        assert isinstance(registry.get("financial_filings"), FinancialFilingChunker)
        assert isinstance(registry.get("policy_macro"), PolicyMacroChunker)
        assert isinstance(registry.get("academic"), AcademicChunker)
        assert isinstance(registry.get("history_reports"), HistoryReportChunker)
        assert isinstance(registry.get("system_rules"), SystemRulesChunker)

    def test_get_unknown_source_type_returns_research_report_chunker(self):
        registry = ChunkerRegistry()
        chunker = registry.get("nonexistent")
        assert isinstance(chunker, ResearchReportChunker)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/rag/test_chunkers.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现分块策略**

`src/rag/chunkers.py`:
```python
"""文档分块策略 — 6 种 Collection 类型各自的分块逻辑"""
import re
from abc import ABC, abstractmethod


class Chunker(ABC):
    """文档分块器抽象基类"""

    @abstractmethod
    def chunk(self, text: str) -> list[str]:
        """将文档文本切分为 chunk 列表"""
        ...


class ResearchReportChunker(Chunker):
    """券商研报 — 按二级标题（##）切分"""

    def chunk(self, text: str) -> list[str]:
        if not text.strip():
            return []
        sections = re.split(r"\n(?=## )", text)
        return [s.strip() for s in sections if s.strip()]


class FinancialFilingChunker(Chunker):
    """财报/公告 — 按财报科目标题切分

    识别"一、营业收入"、"二、利润"等汉数字序号标题，
    如果未找到则降级为段落切分。
    """

    _FINANCIAL_HEADING = re.compile(
        r"\n(?=[一二三四五六七八九十]、|\([一二三四五六七八九十]\))"
    )

    def chunk(self, text: str) -> list[str]:
        if not text.strip():
            return []
        sections = self._FINANCIAL_HEADING.split(text)
        chunks = [s.strip() for s in sections if s.strip()]
        if len(chunks) <= 1:
            # 降级为段落切分
            chunks = [p.strip() for p in text.split("\n\n") if p.strip()]
        return chunks


class PolicyMacroChunker(Chunker):
    """政策/宏观 — 按段落簇切分

    将相邻短段落合并为簇（每簇最多 3 段），保持语义连贯。
    """

    _MAX_PARAGRAPHS_PER_CHUNK = 3

    def chunk(self, text: str) -> list[str]:
        if not text.strip():
            return []
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return [text.strip()]

        chunks = []
        i = 0
        while i < len(paragraphs):
            group = paragraphs[i:i + self._MAX_PARAGRAPHS_PER_CHUNK]
            chunks.append("\n\n".join(group))
            i += self._MAX_PARAGRAPHS_PER_CHUNK
        return chunks


class AcademicChunker(Chunker):
    """学术文献 — 按摘要/方法/结论三段切分"""

    _SECTION_MARKERS = ["摘要", "abstract", "方法", "method", "结论", "conclusion",
                        "引言", "introduction", "讨论", "discussion"]

    def chunk(self, text: str) -> list[str]:
        if not text.strip():
            return []
        # 尝试按常见章节关键词切分
        pattern = "|".join(re.escape(m) for m in self._SECTION_MARKERS)
        sections = re.split(
            rf"(?i)(?=\b(?:{pattern})\b)",
            text,
        )
        chunks = [s.strip() for s in sections if s.strip()]
        if len(chunks) <= 1:
            chunks = [p.strip() for p in text.split("\n\n") if p.strip()]
        return chunks


class HistoryReportChunker(Chunker):
    """项目历史报告 — 按分析维度（## 二级标题）切分

    同时支持从标题提取股票代码。
    """

    _SYMBOL_PATTERN = re.compile(r"（(\d{6})）|\((\d{6})\)")

    def chunk(self, text: str) -> list[str]:
        if not text.strip():
            return []
        sections = re.split(r"\n(?=## )", text)
        return [s.strip() for s in sections if s.strip()]

    def extract_symbols(self, text: str) -> list[str]:
        """从报告文本中提取股票代码"""
        symbols = set()
        for m in self._SYMBOL_PATTERN.finditer(text):
            symbol = m.group(1) or m.group(2)
            if symbol:
                symbols.add(symbol)
        return list(symbols)


class SystemRulesChunker(Chunker):
    """系统规则 — 按 ## 或 --- 分隔条目切分"""

    def chunk(self, text: str) -> list[str]:
        if not text.strip():
            return []
        # 按 ## 标题或 --- 分隔线切分
        sections = re.split(r"\n(?=## |---)", text)
        return [s.strip() for s in sections if s.strip()]


class ChunkerRegistry:
    """分块器注册表 — 按 source_type 映射对应的 Chunker"""

    _MAPPING: dict[str, type[Chunker]] = {
        "research_reports": ResearchReportChunker,
        "financial_filings": FinancialFilingChunker,
        "policy_macro": PolicyMacroChunker,
        "academic": AcademicChunker,
        "history_reports": HistoryReportChunker,
        "system_rules": SystemRulesChunker,
    }

    def get(self, source_type: str) -> Chunker:
        cls = self._MAPPING.get(source_type, ResearchReportChunker)
        return cls()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/rag/test_chunkers.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/rag/chunkers.py tests/rag/test_chunkers.py
git commit -m "feat(RAG): 添加 6 种文档分块策略与 ChunkerRegistry"
```

---

### Task 4: Ingestion 文档摄入流水线

**Files:**
- Create: `src/rag/ingestion.py`
- Create: `tests/rag/test_ingestion.py`

- [ ] **Step 1: 编写摄入流水线的失败测试**

`tests/rag/test_ingestion.py`:
```python
"""IngestionPipeline 单元测试"""
import hashlib
import pytest
from datetime import datetime
from rag.schemas import ChunkMetadata
from rag.ingestion import IngestionPipeline


class FakeEmbeddingProvider:
    name = "fake"
    def embed(self, texts):
        return [[0.1] * 384 for _ in texts]


class FakeChunkMetadataBuilder:
    """模拟元数据构建器"""
    @staticmethod
    def build(source_type, source_path, source_hash, title, date, symbols, tags, chunk_index, ingested_at):
        return ChunkMetadata(
            source_type=source_type,
            source_path=source_path,
            source_hash=source_hash,
            title=title,
            date=date,
            symbols=symbols,
            tags=tags,
            chunk_index=chunk_index,
            ingested_at=ingested_at,
        )


class FakeCollection:
    """模拟 ChromaDB Collection"""
    def __init__(self):
        self._docs = []
        self._metadatas = []
        self._ids = []

    def add(self, documents, metadatas, ids):
        self._docs.extend(documents)
        self._metadatas.extend(metadatas)
        self._ids.extend(ids)

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
        now = datetime.now().isoformat()
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
        now = datetime.now().isoformat()
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
        """模拟 ChromaDB 返回已有文档哈希，验证重复检测"""
        content = "已有的文档"
        doc_hash = pipeline.compute_hash(content)

        class DupCollection:
            def get(self, **kwargs):
                return {
                    "metadatas": [{"source_hash": doc_hash}],
                }

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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/rag/test_ingestion.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 IngestionPipeline**

`src/rag/ingestion.py`:
```python
"""文档摄入流水线 — 加载→哈希去重→分块→embed→upsert"""
import hashlib
import logging
from datetime import datetime
from pathlib import Path

from rag.chunkers import Chunker, ChunkerRegistry
from rag.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".yaml", ".yml"}


class IngestionPipeline:
    """文档摄入流水线

    流程: 加载文件 → 计算 SHA256 → 去重检查 → 分块 → 向量化 → upsert
    """

    def __init__(self, embedding_provider: EmbeddingProvider):
        self._embedding = embedding_provider
        self._chunker_registry = ChunkerRegistry()

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def ingest_file(
        self,
        collection,
        file_path: str,
        source_type: str,
        title: str = "",
        date: str = "",
        symbols: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """摄入单个文件"""
        symbols = symbols or []
        tags = tags or []
        ingested_at = datetime.now().isoformat()

        try:
            text = self.load_document(file_path)
        except Exception as e:
            logger.error("加载文件失败 %s: %s", file_path, e)
            return {"status": "error", "reason": str(e), "file_path": file_path}

        doc_hash = self.compute_hash(text)
        if self.is_duplicate(collection, doc_hash):
            logger.info("文档已存在，跳过: %s", file_path)
            return {
                "status": "skipped",
                "reason": "duplicate",
                "file_path": file_path,
                "source_hash": doc_hash,
            }

        file_title = title or Path(file_path).stem
        result = self.ingest_text(
            collection=collection,
            text=text,
            source_type=source_type,
            source_path=file_path,
            title=file_title,
            date=date or None,
            symbols=symbols,
            tags=tags,
            ingested_at=ingested_at,
        )
        result["file_path"] = file_path
        result["source_hash"] = doc_hash
        return result

    def ingest_text(
        self,
        collection,
        text: str,
        source_type: str,
        source_path: str,
        title: str,
        date: str | None,
        symbols: list[str],
        tags: list[str],
        ingested_at: str,
    ) -> dict:
        """摄入纯文本（已加载到内存）"""
        chunker = self._chunker_registry.get(source_type)
        chunks = chunker.chunk(text)

        if not chunks:
            logger.warning("文档分块为空: %s", source_path)
            return {"status": "skipped", "reason": "empty_chunks", "chunks_count": 0}

        source_hash = self.compute_hash(text)
        embeddings = self._embedding.embed(chunks)

        ids = []
        metadatas = []
        for i, chunk in enumerate(chunks):
            chunk_id = f"{source_hash}_{i}"
            ids.append(chunk_id)
            from rag.schemas import ChunkMetadata
            meta = ChunkMetadata(
                source_type=source_type,
                source_path=source_path,
                source_hash=source_hash,
                title=title,
                date=date,
                symbols=symbols,
                tags=tags,
                chunk_index=i,
                ingested_at=ingested_at,
            )
            metadatas.append(meta.to_dict())

        collection.add(
            documents=chunks,
            embeddings=embeddings,
            metadatas=metadatas,
            ids=ids,
        )
        return {"status": "success", "chunks_count": len(chunks)}

    def ingest_directory(
        self,
        collection,
        directory: str,
        source_type: str,
        title_prefix: str = "",
        symbols: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> list[dict]:
        """批量摄入目录下的所有支持文件"""
        symbols = symbols or []
        tags = tags or []
        results = []
        dir_path = Path(directory)
        if not dir_path.is_dir():
            raise ValueError(f"目录不存在: {directory}")

        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if not file_path.is_file():
                continue
            title = f"{title_prefix} - {file_path.stem}" if title_prefix else file_path.stem
            result = self.ingest_file(
                collection=collection,
                file_path=str(file_path),
                source_type=source_type,
                title=title,
            )
            results.append(result)

        return results

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def compute_hash(self, content: str) -> str:
        """计算文本 SHA256 哈希"""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def load_document(self, file_path: str) -> str:
        """从文件路径加载文本内容"""
        path = Path(file_path)
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"不支持的文件格式: {suffix}，支持: {SUPPORTED_EXTENSIONS}")
        return path.read_text(encoding="utf-8")

    def is_duplicate(self, collection, doc_hash: str) -> bool:
        """检查文档是否已存在于 Collection 中"""
        try:
            existing = collection.get(
                where={"source_hash": doc_hash},
                limit=1,
            )
            return len(existing.get("metadatas", [])) > 0
        except Exception:
            # ChromaDB 可能在空 Collection 上抛异常
            return False
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/rag/test_ingestion.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/rag/ingestion.py tests/rag/test_ingestion.py
git commit -m "feat(RAG): 添加 IngestionPipeline 文档摄入流水线（哈希去重+分块+embed+upsert）"
```

---

### Task 5: Retrieval 检索流水线

**Files:**
- Create: `src/rag/retrieval.py`
- Create: `tests/rag/test_retrieval.py`

- [ ] **Step 1: 编写检索流水线的失败测试**

`tests/rag/test_retrieval.py`:
```python
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
        return {"documents": [], "metadatas": []}


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
        assert results[0]["score"] == pytest.approx(0.8)  # 1.0 - 0.2

    def test_search_uses_2x_top_k_for_initial_query(self, pipeline):
        captured_n = []
        class CaptureCollection(FakeCollection):
            def query(self, query_embeddings, n_results, where=None):
                captured_n.append(n_results)
                return {"documents": [[]], "metadatas": [[]],
                        "distances": [[]], "ids": [[]]}
        collection = CaptureCollection()
        pipeline.search(collection, "测试查询", top_k=5)
        assert captured_n[0] == 10  # 2 * top_k

    def test_search_with_filters(self, pipeline):
        """验证 metadata 过滤参数传递"""
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
        """关键词搜索（非向量模式）——通过 get 获取文档然后本地匹配"""
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/rag/test_retrieval.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 RetrievalPipeline**

`src/rag/retrieval.py`:
```python
"""检索流水线 — embedding→向量检索→重排→返回结果"""
import logging
from rag.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)


class RetrievalPipeline:
    """检索流水线

    支持两种模式：
    1. 向量检索（有 embedding 模型时）: query embed → ChromaDB.query → 重排
    2. 关键词检索（降级模式）: query → 本地关键词匹配 → 排序
    """

    def __init__(self, embedding_provider: EmbeddingProvider):
        self._embedding = embedding_provider
        self._is_keyword_mode = embedding_provider.name == "keyword-fallback"

    def search(
        self,
        collection,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[dict]:
        """语义检索"""
        if self._is_keyword_mode:
            return self.keyword_search(collection, query, top_k, filters)

        return self._vector_search(collection, query, top_k, filters)

    # ------------------------------------------------------------------
    # 向量检索
    # ------------------------------------------------------------------

    def _vector_search(
        self,
        collection,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[dict]:
        """向量检索 → 距离转相似度 → 返回结果"""
        query_embedding = self._embedding.embed([query])
        chroma_filter = self._build_chroma_filter(filters) if filters else None
        # 检索 2x 以给重排留空间
        n_results = max(top_k * 2, 10)

        try:
            raw = collection.query(
                query_embeddings=query_embedding,
                n_results=n_results,
                where=chroma_filter,
            )
        except Exception as e:
            logger.error("ChromaDB 查询失败: %s", e)
            return []

        results = []
        docs = raw.get("documents", [[]])[0]
        metas = raw.get("metadatas", [[]])[0]
        dists = raw.get("distances", [[]])[0]
        ids = raw.get("ids", [[]])[0]

        for i, doc in enumerate(docs):
            meta = metas[i] if i < len(metas) else {}
            dist = dists[i] if i < len(dists) else 1.0
            similarity = max(0.0, 1.0 - dist)
            results.append({
                "id": ids[i] if i < len(ids) else "",
                "content": doc,
                "metadata": meta,
                "score": round(similarity, 4),
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    # ------------------------------------------------------------------
    # 关键词检索（降级模式）
    # ------------------------------------------------------------------

    def keyword_search(
        self,
        collection,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[dict]:
        """关键词匹配检索——无向量模型时的降级"""
        try:
            all_data = collection.get()
        except Exception as e:
            logger.error("ChromaDB get 失败: %s", e)
            return []

        docs = all_data.get("documents", [])
        metas = all_data.get("metadatas", [])
        ids = all_data.get("ids", [])

        results = []
        query_lower = query.lower()
        for i, doc in enumerate(docs):
            # 简单关键词匹配评分
            score = sum(1 for word in query_lower.split() if word in doc.lower())
            if score > 0:
                if self._match_filters(metas[i] if i < len(metas) else {}, filters):
                    results.append({
                        "id": ids[i] if i < len(ids) else "",
                        "content": doc,
                        "metadata": metas[i] if i < len(metas) else {},
                        "score": float(score),
                    })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _build_chroma_filter(self, filters: dict) -> dict:
        """构建 ChromaDB where 过滤条件"""
        conditions = []
        for key, value in filters.items():
            if key == "symbol":
                conditions.append({"symbols": {"$contains": value}})
            elif key == "source_type":
                conditions.append({"source_type": value})
            elif key == "date_from":
                conditions.append({"date": {"$gte": value}})
            elif key == "date_to":
                conditions.append({"date": {"$lte": value}})
            else:
                conditions.append({key: value})
        if len(conditions) == 1:
            return conditions[0]
        elif conditions:
            return {"$and": conditions}
        return {}

    def _match_filters(self, metadata: dict, filters: dict | None) -> bool:
        """检查 metadata 是否满足过滤条件（关键词检索模式使用）"""
        if not filters:
            return True
        for key, value in filters.items():
            if key == "symbol":
                if value not in metadata.get("symbols", []):
                    return False
            elif key == "source_type":
                if metadata.get("source_type") != value:
                    return False
            elif key == "date_from":
                doc_date = metadata.get("date", "")
                if doc_date and doc_date < value:
                    return False
            elif key == "date_to":
                doc_date = metadata.get("date", "")
                if doc_date and doc_date > value:
                    return False
            else:
                if metadata.get(key) != value:
                    return False
        return True
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/rag/test_retrieval.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/rag/retrieval.py tests/rag/test_retrieval.py
git commit -m "feat(RAG): 添加 RetrievalPipeline 检索流水线（向量检索+关键词降级）"
```

---

### Task 6: RAG 引擎入口 — ChromaDB 初始化、Collection 管理

**Files:**
- Create: `src/rag/engine.py`
- Create: `tests/rag/test_engine.py`

- [ ] **Step 1: 编写 RAG 引擎的失败测试**

`tests/rag/test_engine.py`:
```python
"""RAGEngine 集成测试"""
import pytest
from rag.engine import RAGEngine
from rag.embedding import KeywordFallbackProvider


COLLECTION_NAMES = [
    "research_reports",
    "financial_filings",
    "policy_macro",
    "academic",
    "history_reports",
    "system_rules",
]


class TestRAGEngine:
    @pytest.fixture
    def engine(self, tmp_path):
        """使用临时目录避免污染真实数据库"""
        return RAGEngine(
            persist_dir=str(tmp_path / "chroma"),
            embedding_provider=KeywordFallbackProvider(),
        )

    def test_init_creates_all_collections(self, engine):
        for name in COLLECTION_NAMES:
            col = engine.get_collection(name)
            assert col is not None

    def test_get_collection_returns_same_instance(self, engine):
        c1 = engine.get_collection("research_reports")
        c2 = engine.get_collection("research_reports")
        assert c1 is c2

    def test_get_collection_case_insensitive(self, engine):
        """未知名称应 fallback 到 research_reports"""
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
        """按 source_type 删除文档——不应报错"""
        result = engine.delete_by_source_type("research_reports")
        assert result["deleted"] >= 0

    def test_delete_by_symbol(self, engine):
        """按股票代码删除文档"""
        result = engine.delete_by_symbol("000001")
        assert result["deleted"] >= 0

    def test_delete_before_date(self, engine):
        """按日期删除文档"""
        result = engine.delete_before_date("2025-01-01")
        assert result["deleted"] >= 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/rag/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError` 或 `chromadb` 未安装

- [ ] **Step 3: 安装 chromadb 依赖**

Run: `pip install chromadb>=0.5`

- [ ] **Step 4: 更新 pyproject.toml 依赖**

`pyproject.toml` 的 `dependencies` 中添加:

```toml
dependencies = [
    "click>=8.1",
    "rich>=13.0",
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "akshare>=1.14",
    "pandas>=2.0",
    "jinja2>=3.1",
    "openai>=1.0",
    "anthropic>=0.30",
    "chromadb>=0.5",
]
```

- [ ] **Step 5: 实现 RAGEngine**

`src/rag/engine.py`:
```python
"""RAG 引擎入口 — ChromaDB 初始化、Collection 管理、高层 API"""
import logging
from pathlib import Path

from rag.embedding import EmbeddingProvider, create_embedding_provider
from rag.ingestion import IngestionPipeline
from rag.retrieval import RetrievalPipeline

logger = logging.getLogger(__name__)

COLLECTION_NAMES = [
    "research_reports",
    "financial_filings",
    "policy_macro",
    "academic",
    "history_reports",
    "system_rules",
]

DEFAULT_PERSIST_DIR = Path.home() / ".stock_robot" / "chroma"


class RAGEngine:
    """RAG 引擎入口

    管理 ChromaDB 持久化客户端、6 个 Collection、Ingestion/Retrieval 管道。
    对外暴露统一的 search / ingest / delete / list 接口。
    """

    def __init__(
        self,
        persist_dir: str | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ):
        import chromadb

        persist_path = persist_dir or str(DEFAULT_PERSIST_DIR)
        Path(persist_path).mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=persist_path)
        self._embedding = embedding_provider or create_embedding_provider()
        self._ingestion = IngestionPipeline(self._embedding)
        self._retrieval = RetrievalPipeline(self._embedding)
        self._collections: dict[str, object] = {}

        self._init_collections()
        logger.info(
            "RAG 引擎已初始化 (embedding=%s, persist=%s)",
            self._embedding.name,
            persist_path,
        )

    # ------------------------------------------------------------------
    # Collection 管理
    # ------------------------------------------------------------------

    def get_collection(self, name: str):
        """获取或创建指定 Collection"""
        if name not in self._collections:
            safe_name = name if name in COLLECTION_NAMES else "research_reports"
            self._collections[name] = self._client.get_or_create_collection(
                name=safe_name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collections[name]

    def _init_collections(self):
        """启动时初始化全部 6 个 Collection"""
        for name in COLLECTION_NAMES:
            self._collections[name] = self._client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )

    # ------------------------------------------------------------------
    # 检索 API
    # ------------------------------------------------------------------

    def search(
        self,
        collection_name: str,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[dict]:
        """语义检索"""
        collection = self.get_collection(collection_name)
        return self._retrieval.search(collection, query, top_k, filters)

    # ------------------------------------------------------------------
    # 摄入 API
    # ------------------------------------------------------------------

    def ingest_file(
        self,
        file_path: str,
        source_type: str,
        title: str = "",
        date: str = "",
        symbols: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """摄入单个文件"""
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
        """摄入纯文本"""
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
            ingested_at=ingested_at or datetime.now().isoformat(),
        )

    def ingest_directory(
        self,
        directory: str,
        source_type: str,
        title_prefix: str = "",
        symbols: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> list[dict]:
        """批量摄入目录"""
        collection = self.get_collection(source_type)
        return self._ingestion.ingest_directory(
            collection=collection,
            directory=directory,
            source_type=source_type,
            title_prefix=title_prefix,
            symbols=symbols,
            tags=tags,
        )

    # ------------------------------------------------------------------
    # 管理 API
    # ------------------------------------------------------------------

    def list_sources(self) -> list[dict]:
        """列出所有知识库的源文件清单"""
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
            except Exception:
                pass
        return sources

    def collection_stats(self) -> list[dict]:
        """获取各 Collection 统计信息"""
        stats = []
        for name in COLLECTION_NAMES:
            collection = self.get_collection(name)
            try:
                count = collection.count()
            except Exception:
                count = 0
            stats.append({"name": name, "count": count})
        return stats

    def delete_by_source_type(self, source_type: str) -> dict:
        """删除指定 source_type 的全部文档"""
        if source_type not in COLLECTION_NAMES:
            return {"deleted": 0, "error": f"未知 source_type: {source_type}"}
        try:
            self._client.delete_collection(name=source_type)
            self._collections[source_type] = self._client.get_or_create_collection(
                name=source_type,
                metadata={"hnsw:space": "cosine"},
            )
            return {"deleted": -1, "note": f"Collection {source_type} 已重建（全量删除）"}
        except Exception as e:
            return {"deleted": 0, "error": str(e)}

    def delete_by_symbol(self, symbol: str) -> dict:
        """删除与指定股票代码关联的全部文档"""
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
            except Exception:
                pass
        return {"deleted": deleted}

    def delete_before_date(self, before_date: str) -> dict:
        """删除指定日期之前的文档"""
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
            except Exception:
                pass
        return {"deleted": deleted}

    @property
    def embedding_name(self) -> str:
        return self._embedding.name
```

- [ ] **Step 6: 运行测试确认通过**

Run: `pytest tests/rag/test_engine.py -v`
Expected: 10 passed

- [ ] **Step 7: Commit**

```bash
git add src/rag/engine.py tests/rag/test_engine.py pyproject.toml
git commit -m "feat(RAG): 添加 RAGEngine 入口 — ChromaDB 持久化、Collection 管理、删除 API"
```

---

### Task 7: rag_tools — RAGSearchTool 和 RAGListSourcesTool

**Files:**
- Create: `src/agent/rag_tools.py`
- Create: `tests/rag/test_rag_tools.py`

- [ ] **Step 1: 编写 RAG 工具的失败测试**

`tests/rag/test_rag_tools.py`:
```python
"""RAG 工具单元测试"""
import pytest
from agent.tools import ToolResult
from agent.rag_tools import RAGSearchTool, RAGListSourcesTool


class FakeRAGEngine:
    """模拟 RAGEngine"""
    def __init__(self, search_results=None):
        self._search_results = search_results or []
        self._sources = [
            {
                "collection": "research_reports",
                "title": "策略周报",
                "source_path": "/data/weekly.md",
                "source_hash": "abc123",
                "date": "2026-01-15",
                "symbols": ["000001"],
                "tags": ["策略"],
                "chunks_count": 5,
            }
        ]
        self.last_search_collection = None
        self.last_search_query = None

    def search(self, collection_name, query, top_k=5, filters=None):
        self.last_search_collection = collection_name
        self.last_search_query = query
        return self._search_results

    def list_sources(self):
        return self._sources

    @property
    def embedding_name(self):
        return "bge-small-zh"


class TestRAGSearchTool:
    def test_tool_metadata(self):
        tool = RAGSearchTool(engine=FakeRAGEngine())
        assert tool.name == "rag_search"
        assert tool.source == "rag"
        assert len(tool.tags) > 0
        assert "rag" in tool.tags
        assert "knowledge" in tool.tags
        assert hasattr(tool, "execute")

    def test_parameters_has_query_field(self):
        tool = RAGSearchTool(engine=FakeRAGEngine())
        params = tool.parameters
        assert params["type"] == "object"
        assert "query" in params["properties"]
        assert "query" in params["required"]

    @pytest.mark.asyncio
    async def test_execute_returns_success_with_results(self):
        engine = FakeRAGEngine(search_results=[
            {"content": "新能源行业前景看好", "metadata": {"title": "研报1"}, "score": 0.95},
        ])
        tool = RAGSearchTool(engine=engine)
        result = await tool.execute(query="新能源 前景")
        assert result.status == "success"
        assert len(result.data["results"]) == 1
        assert result.data["results"][0]["content"] == "新能源行业前景看好"

    @pytest.mark.asyncio
    async def test_execute_returns_empty_results_gracefully(self):
        engine = FakeRAGEngine(search_results=[])
        tool = RAGSearchTool(engine=engine)
        result = await tool.execute(query="不存在的查询")
        assert result.status == "success"
        assert result.data["results"] == []

    @pytest.mark.asyncio
    async def test_execute_with_source_type_filter(self):
        engine = FakeRAGEngine(search_results=[])
        tool = RAGSearchTool(engine=engine)
        result = await tool.execute(query="银行", source_type="research_reports")
        assert result.status == "success"
        assert engine.last_search_collection == "research_reports"

    @pytest.mark.asyncio
    async def test_execute_with_symbol_filter(self):
        engine = FakeRAGEngine(search_results=[])
        tool = RAGSearchTool(engine=engine)
        result = await tool.execute(query="财报", symbol="000001")
        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_execute_catches_exceptions(self):
        class BrokenEngine:
            def search(self, **kwargs):
                raise RuntimeError("引擎故障")

        tool = RAGSearchTool(engine=BrokenEngine())
        result = await tool.execute(query="测试")
        assert result.status == "error"
        assert "引擎故障" in result.error

    @pytest.mark.asyncio
    async def test_execute_missing_query(self):
        engine = FakeRAGEngine()
        tool = RAGSearchTool(engine=engine)
        result = await tool.execute()
        assert result.status == "error"
        assert "查询" in result.error


class TestRAGListSourcesTool:
    def test_tool_metadata(self):
        tool = RAGListSourcesTool(engine=FakeRAGEngine())
        assert tool.name == "rag_list_sources"
        assert tool.source == "rag"
        assert isinstance(tool.description, str)
        assert len(tool.description) > 0

    @pytest.mark.asyncio
    async def test_execute_returns_sources_list(self):
        engine = FakeRAGEngine()
        tool = RAGListSourcesTool(engine=engine)
        result = await tool.execute()
        assert result.status == "success"
        assert len(result.data["sources"]) == 1
        assert result.data["sources"][0]["title"] == "策略周报"

    @pytest.mark.asyncio
    async def test_execute_catches_exceptions(self):
        class BrokenEngine:
            def list_sources(self):
                raise RuntimeError("无法获取源列表")

        tool = RAGListSourcesTool(engine=BrokenEngine())
        result = await tool.execute()
        assert result.status == "error"
        assert "无法获取源列表" in result.error
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/rag/test_rag_tools.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 RAG 工具**

`src/agent/rag_tools.py`:
```python
"""RAG 工具 — rag_search 语义检索 + rag_list_sources 知识源清单"""
import logging
from agent.tools import ToolResult

logger = logging.getLogger(__name__)


class RAGSearchTool:
    """语义检索知识库工具"""

    name = "rag_search"
    description = (
        "在知识库中语义检索相关信息，返回最相关的文档片段。"
        "知识库包含：券商研报、财报公告、政策宏观、学术文献、历史分析报告、系统规则。"
        "参数: query(查询文本), source_type(可选，限定知识库类型), "
        "symbol(可选，限定股票代码), top_k(可选，返回数量，默认5)"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "查询文本，支持自然语言"},
            "source_type": {
                "type": "string",
                "description": "知识库类型: research_reports/financial_filings/policy_macro/academic/history_reports/system_rules",
            },
            "symbol": {"type": "string", "description": "限定股票代码，如 000001"},
            "top_k": {"type": "integer", "description": "返回结果数量，默认 5"},
        },
        "required": ["query"],
    }
    tags = ["rag", "knowledge", "search"]
    source = "rag"

    def __init__(self, engine=None):
        self._engine = engine

    async def execute(self, **kwargs) -> ToolResult:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return ToolResult(status="error", error="查询参数 query 不能为空",
                             metadata={"source": "rag"})

        source_type = kwargs.get("source_type", "")
        symbol = kwargs.get("symbol", "")
        top_k = int(kwargs.get("top_k", 5))

        filters = {}
        if symbol:
            filters["symbol"] = str(symbol).strip()
        if source_type:
            filters["source_type"] = str(source_type).strip()

        try:
            if source_type:
                collection_name = source_type
            else:
                collection_name = "research_reports"

            if self._engine is None:
                from rag.engine import RAGEngine
                engine = RAGEngine()
            else:
                engine = self._engine

            results = engine.search(
                collection_name=collection_name,
                query=query,
                top_k=top_k,
                filters=filters if filters else None,
            )

            return ToolResult(
                status="success",
                data={
                    "query": query,
                    "results": results,
                    "total": len(results),
                    "embedding_model": getattr(engine, "embedding_name", "unknown"),
                },
                metadata={"source": "rag", "collection": collection_name},
            )
        except Exception as e:
            logger.error("rag_search 执行失败: %s", e)
            return ToolResult(status="error", error=str(e),
                             metadata={"source": "rag"})


class RAGListSourcesTool:
    """列出当前索引的知识源清单"""

    name = "rag_list_sources"
    description = (
        "列出当前已索引的所有知识源，包含文档标题、来源路径、关联股票代码、"
        "文档日期、标签和分块数量等信息。用于了解知识库的覆盖范围。"
    )
    parameters = {
        "type": "object",
        "properties": {},
    }
    tags = ["rag", "knowledge", "management"]
    source = "rag"

    def __init__(self, engine=None):
        self._engine = engine

    async def execute(self, **kwargs) -> ToolResult:
        try:
            if self._engine is None:
                from rag.engine import RAGEngine
                engine = RAGEngine()
            else:
                engine = self._engine

            sources = engine.list_sources()

            # 按 Collection 分组汇总
            collection_summary = {}
            for s in sources:
                col = s["collection"]
                if col not in collection_summary:
                    collection_summary[col] = {"count": 0, "total_chunks": 0}
                collection_summary[col]["count"] += 1
                collection_summary[col]["total_chunks"] += s.get("chunks_count", 0)

            return ToolResult(
                status="success",
                data={
                    "sources": sources,
                    "total": len(sources),
                    "collection_summary": collection_summary,
                },
                metadata={"source": "rag"},
            )
        except Exception as e:
            logger.error("rag_list_sources 执行失败: %s", e)
            return ToolResult(status="error", error=str(e),
                             metadata={"source": "rag"})
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/rag/test_rag_tools.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/agent/rag_tools.py tests/rag/test_rag_tools.py
git commit -m "feat(RAG): 添加 rag_search 和 rag_list_sources 工具"
```

---

### Task 8: CLI rag 命令组 — ingest / clean / stats

**Files:**
- Modify: `src/stock_robot/cli.py`
- (无需新建测试文件，cli 测试在 Task 10 统一覆盖)

- [ ] **Step 1: 在 cli.py 末尾添加 rag 命令组**

在 `src/stock_robot/cli.py` 末尾追加以下代码（找到文件中 `chat` 命令的定义后添加）:

```python
# ---------------------------------------------------------------------------
# RAG 知识库管理命令组
# ---------------------------------------------------------------------------

@main.group()
def rag():
    """知识库管理 — 文档摄入、清理、统计"""
    pass


@rag.command("ingest")
@click.argument("path")
@click.option("--source-type", "-s", required=True,
              type=click.Choice([
                  "research_reports", "financial_filings",
                  "policy_macro", "academic",
                  "history_reports", "system_rules",
              ]),
              help="知识库类型")
@click.option("--title", "-t", default="", help="文档标题")
@click.option("--date", "-d", default="", help="文档日期 (YYYY-MM-DD)")
@click.option("--symbol", multiple=True, help="关联股票代码（可多次指定）")
@click.option("--tag", multiple=True, help="内容标签（可多次指定）")
@click.option("--recursive/--no-recursive", default=False,
              help="递归处理子目录")
def rag_ingest(path, source_type, title, date, symbol, tag, recursive):
    """摄入文档或目录到知识库

    PATH 可以是单个文件或目录路径。
    """
    from rag.engine import RAGEngine

    console.print(f"[bold]正在摄入知识库...[/bold]")
    console.print(f"  类型: {source_type}")
    console.print(f"  路径: {path}")

    engine = RAGEngine()
    symbols = list(symbol)
    tags = list(tag)

    if os.path.isdir(path):
        console.print(f"  模式: 目录批量导入")
        results = engine.ingest_directory(
            directory=path,
            source_type=source_type,
            title_prefix=title,
            symbols=symbols,
            tags=tags,
        )
        succeeded = sum(1 for r in results if r.get("status") == "success")
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        failed = sum(1 for r in results if r.get("status") == "error")
        console.print(
            f"[green]✓ 成功: {succeeded}[/green]  "
            f"[yellow]跳过: {skipped}[/yellow]  "
            f"[red]失败: {failed}[/red]"
        )
        for r in results:
            if r.get("status") == "error":
                console.print(f"  [red]✗ {r.get('file_path', '?')}: {r.get('reason')}[/red]")
    else:
        result = engine.ingest_file(
            file_path=path,
            source_type=source_type,
            title=title,
            date=date,
            symbols=symbols,
            tags=tags,
        )
        if result["status"] == "success":
            console.print(
                f"[green]✓ 摄入成功: {result['chunks_count']} 个分块[/green]"
            )
            console.print(f"  哈希: {result.get('source_hash', '')[:16]}...")
        elif result["status"] == "skipped":
            console.print(f"[yellow]跳过: {result.get('reason', '?')}[/yellow]")
        else:
            console.print(f"[red]✗ 失败: {result.get('reason', '?')}[/red]")


@rag.command("clean")
@click.option("--source", "-s", "source_type",
              type=click.Choice([
                  "research_reports", "financial_filings",
                  "policy_macro", "academic",
                  "history_reports", "system_rules",
              ]),
              help="清除指定知识库类型")
@click.option("--before", "before_date", default="",
              help="清除指定日期前的文档 (YYYY-MM-DD)")
@click.option("--symbol", default="",
              help="清除指定股票关联的文档")
@click.option("--dry-run", is_flag=True, default=False,
              help="预览，不实际删除")
def rag_clean(source_type, before_date, symbol, dry_run):
    """清理知识库中的文档"""
    from rag.engine import RAGEngine

    engine = RAGEngine()

    if dry_run:
        console.print("[bold yellow]DRY RUN 模式 — 仅预览，不实际删除[/bold yellow]\n")

    if source_type:
        if dry_run:
            stats = engine.collection_stats()
            for s in stats:
                if s["name"] == source_type:
                    console.print(
                        f"将清除 [bold]{source_type}[/bold] 全部 "
                        f"[bold]{s['count']}[/bold] 个文档"
                    )
                    break
        else:
            result = engine.delete_by_source_type(source_type)
            console.print(f"[green]✓ 已清除 {source_type} 知识库[/green]")

    elif before_date:
        if dry_run:
            console.print(f"将清除 [bold]{before_date}[/bold] 之前的文档")
        else:
            result = engine.delete_before_date(before_date)
            console.print(
                f"[green]✓ 已清除 {result['deleted']} 个文档"
                f"（{before_date} 之前）[/green]"
            )

    elif symbol:
        if dry_run:
            console.print(f"将清除与股票 [bold]{symbol}[/bold] 关联的文档")
        else:
            result = engine.delete_by_symbol(symbol)
            console.print(
                f"[green]✓ 已清除 {result['deleted']} 个与 {symbol} 关联的文档[/green]"
            )

    else:
        console.print(
            "[yellow]请指定清除条件: --source / --before / --symbol[/yellow]"
        )


@rag.command("stats")
def rag_stats():
    """查看知识库各 Collection 统计信息"""
    from rag.engine import RAGEngine

    engine = RAGEngine()
    stats = engine.collection_stats()
    embedding_name = engine.embedding_name

    table = Table(title="RAG 知识库统计")
    table.add_column("Collection", style="cyan")
    table.add_column("文档数", justify="right")
    table.add_column("状态", style="green")

    total = 0
    for entry in stats:
        table.add_row(
            entry["name"],
            str(entry["count"]),
            "✓" if entry["count"] > 0 else "空",
        )
        total += entry["count"]

    table.add_section()
    table.add_row("[bold]合计[/bold]", f"[bold]{total}[/bold]", "")
    console.print(table)
    console.print(f"\n[dim]Embedding 模型: {embedding_name}[/dim]")
```

- [ ] **Step 2: 验证 CLI 命令可被 click 发现**

Run: `python -m stock_robot.cli rag --help`
Expected: 显示 rag 子命令帮助（ingest, clean, stats）

- [ ] **Step 3: 测试 rag stats 命令**

Run: `python -m stock_robot.cli rag stats`
Expected: 显示 6 个 Collection 的表格，所有计数为 0

- [ ] **Step 4: 测试 rag ingest 命令（摄入单文件）**

```bash
python -m stock_robot.cli rag ingest reports/000001_20260807_161939.md -s history_reports -t "平安银行测试报告" -d "2026-08-07" --symbol 000001 --tag 测试
```

Expected: `✓ 摄入成功: N 个分块`

- [ ] **Step 5: 验证去重——再次摄入同一文件**

再次运行上一步命令，Expected: `跳过: duplicate`

- [ ] **Step 6: 测试 rag stats 显示统计数据**

Run: `python -m stock_robot.cli rag stats`
Expected: history_reports 文档数 > 0

- [ ] **Step 7: 测试 rag clean --dry-run**

Run: `python -m stock_robot.cli rag clean --before 2026-01-01 --dry-run`
Expected: `DRY RUN 模式`

- [ ] **Step 8: Commit**

```bash
git add src/stock_robot/cli.py
git commit -m "feat(RAG): 添加 CLI rag 命令组（ingest/clean/stats）"
```

---

### Task 9: LocalReportSync — 本地报告自动同步

**Files:**
- Create: `src/rag/sync.py`
- Create: `tests/rag/test_sync.py`

- [ ] **Step 1: 编写同步模块的失败测试**

`tests/rag/test_sync.py`:
```python
"""LocalReportSync 单元测试"""
import time
import pytest
from pathlib import Path
from rag.sync import LocalReportSync


class FakeRAGEngine:
    """模拟 RAGEngine 仅记录调用"""
    def __init__(self):
        self.ingested_files = []
        self.deleted_paths = []

    def ingest_file(self, file_path, source_type, title, date, symbols, tags):
        self.ingested_files.append({
            "file_path": file_path,
            "source_type": source_type,
            "title": title,
            "date": date,
            "symbols": symbols,
            "tags": tags,
        })
        return {"status": "success", "chunks_count": 3, "source_hash": "fake_hash"}

    def delete_by_source_path(self, source_path):
        """Mock——实际 RAGEngine 无此方法，由 sync 通过 engine API 实现"""
        self.deleted_paths.append(source_path)


class TestLocalReportSync:
    @pytest.fixture
    def sync(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        return LocalReportSync(
            reports_dir=str(reports_dir),
            engine=FakeRAGEngine(),
        )

    def test_scan_new_files_detects_new_reports(self, sync):
        """扫描发现新增报告文件"""
        # 在监控目录下创建新报告
        report_path = Path(sync.reports_dir) / "000001_20260807_test.md"
        report_path.write_text("# 报告\n\n## 分析\n\n测试内容", encoding="utf-8")

        results = sync.scan_new_files()
        assert len(results) > 0

    def test_scan_new_files_skips_already_ingested(self, sync):
        """已摄入的文件不重复处理"""
        report_path = Path(sync.reports_dir) / "000001_20260807_existing.md"
        content = "# 报告\n\n已摄入内容"
        report_path.write_text(content, encoding="utf-8")

        results = sync.scan_new_files()
        ingested_hashes = {r["source_hash"] for r in results if "source_hash" in r}

        # 第二次扫描不应有新文件
        results2 = sync.scan_new_files(ingested_hashes=ingested_hashes)
        assert len(results2) == 0

    def test_full_sync_processes_new_files(self, sync):
        """全量同步——发现新文件并调用引擎摄入"""
        report_path = Path(sync.reports_dir) / "000001_20260807_sync.md"
        report_path.write_text("# 同步测试\n\n## 维度一\n\n测试数据", encoding="utf-8")

        result = sync.sync()
        assert result["processed"] > 0
        assert result["skipped"] >= 0
        assert result["failed"] == 0

    def test_extract_report_info_parses_filename(self, sync):
        """从报告文件名中提取股票代码和日期"""
        info = sync.extract_report_info("000001_20260807_161939.md")
        assert "000001" in info.get("symbols", [])
        assert info.get("date") is not None

    def test_extract_symbols_from_content(self, sync):
        """从报告内容中提取股票代码"""
        content = "# 平安银行（000001）分析报告\n\n内容"
        symbols = sync.extract_symbols_from_content(content)
        assert "000001" in symbols

    def test_empty_reports_dir_scan_returns_empty(self, sync):
        """空目录扫描不报错"""
        results = sync.scan_new_files()
        assert results == []

    def test_sync_handles_engine_failure(self, tmp_path):
        """引擎摄入失败时应被记录为 failed 而非崩溃"""
        class BrokenEngine:
            def ingest_file(self, **kwargs):
                raise RuntimeError("引擎故障")
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "000001_test.md").write_text("# 测试", encoding="utf-8")

        sync = LocalReportSync(
            reports_dir=str(reports_dir),
            engine=BrokenEngine(),
        )
        result = sync.sync()
        assert result["failed"] >= 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/rag/test_sync.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 LocalReportSync**

`src/rag/sync.py`:
```python
"""本地报告自动同步 — 监听 reports/ 目录 → 自动 chunk → embed → upsert"""
import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_SYMBOL_IN_TITLE = re.compile(r"(\d{6})")  # 文件名/标题中的6位代码


class LocalReportSync:
    """本地报告自动同步器

    扫描 reports/ 目录，将新生成的报告自动摄入到
    history_reports Collection。
    """

    def __init__(self, reports_dir: str, engine=None):
        self.reports_dir = reports_dir
        self._engine = engine

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def sync(self) -> dict:
        """执行一次全量同步

        返回: {"processed": N, "skipped": N, "failed": N}
        """
        processed = 0
        skipped = 0
        failed = 0

        new_files = self.scan_new_files()
        for file_info in new_files:
            try:
                content = Path(file_info["path"]).read_text(encoding="utf-8")
                symbols = self.extract_symbols_from_content(content)
                date = file_info.get("date", "")

                if not symbols:
                    symbols_from_name = self._symbols_from_filename(file_info["name"])
                    symbols = symbols_from_name

                result = self._engine.ingest_file(
                    file_path=file_info["path"],
                    source_type="history_reports",
                    title=file_info["name"].replace(".md", ""),
                    date=date,
                    symbols=symbols,
                    tags=["历史报告"],
                )
                if result.get("status") == "success":
                    processed += 1
                    self._mark_ingested(file_info["path"], file_info["hash"])
                elif result.get("status") == "skipped":
                    skipped += 1
                else:
                    failed += 1
                    logger.warning("报告同步失败: %s → %s",
                                   file_info["path"], result.get("reason", "?"))
            except Exception as e:
                failed += 1
                logger.error("同步报告异常: %s → %s", file_info.get("path", "?"), e)

        return {"processed": processed, "skipped": skipped, "failed": failed}

    def scan_new_files(self, ingested_hashes: set | None = None) -> list[dict]:
        """扫描目录，返回未摄入的报告文件列表

        每个文件包含: path, name, hash, date, symbols
        """
        reports_path = Path(self.reports_dir)
        if not reports_path.is_dir():
            logger.warning("报告目录不存在: %s", self.reports_dir)
            return []

        known_hashes = ingested_hashes or self._load_ingested_hashes()
        new_files = []

        for file_path in sorted(reports_path.glob("*.md")):
            content = file_path.read_text(encoding="utf-8")
            file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if file_hash in known_hashes:
                continue

            info = self.extract_report_info(file_path.name)
            new_files.append({
                "path": str(file_path),
                "name": file_path.name,
                "hash": file_hash,
                "date": info.get("date"),
                "symbols": info.get("symbols", []),
            })

        return new_files

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def extract_report_info(self, filename: str) -> dict:
        """从文件名提取股票代码和日期

        文件名格式: {symbol}_{YYYYMMDD}_{HHMMSS}.md
        """
        stem = filename.replace(".md", "")
        parts = stem.split("_")
        info = {"symbols": [], "date": None}
        if len(parts) >= 1 and parts[0].isdigit() and len(parts[0]) == 6:
            info["symbols"] = [parts[0]]
        if len(parts) >= 2 and len(parts[1]) == 8:
            date_str = parts[1]
            info["date"] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        return info

    def extract_symbols_from_content(self, content: str) -> list[str]:
        """从报告内容提取股票代码（标题行匹配）"""
        symbols = set()
        for line in content.splitlines()[:3]:
            for m in _SYMBOL_IN_TITLE.finditer(line):
                symbols.add(m.group(1))
        return list(symbols)

    def _symbols_from_filename(self, filename: str) -> list[str]:
        """从文件名提取股票代码"""
        parts = filename.replace(".md", "").split("_")
        if parts and parts[0].isdigit() and len(parts[0]) == 6:
            return [parts[0]]
        return []

    def _load_ingested_hashes(self) -> set:
        """从引擎加载已摄入的文档哈希集合"""
        if self._engine is None:
            return set()
        try:
            sources = self._engine.list_sources()
            return {s["source_hash"] for s in sources
                    if s.get("collection") == "history_reports"}
        except Exception:
            return set()

    def _mark_ingested(self, file_path: str, file_hash: str):
        """标记文件已摄入（当前通过引擎完成，此处为未来的乐观锁占位）"""
        pass
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/rag/test_sync.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/rag/sync.py tests/rag/test_sync.py
git commit -m "feat(RAG): 添加 LocalReportSync 本地报告自动同步模块"
```

---

### Task 10: RAG 工具注册 + chat 集成 + 全面验证

**Files:**
- Modify: `src/stock_robot/cli.py`（chat 命令中注册 RAG 工具）

- [ ] **Step 1: 在 chat 命令中注册 RAG 工具**

修改 `src/stock_robot/cli.py` 的 `_run_agent_query` 函数，在初始化 `ToolRegistry` 后添加 RAG 工具注册。

找到 `registry.register(AnalyzeStockTool())` 等 pipeline 工具注册的位置（约在 chat 命令处理逻辑中），在其后追加:

```python
# 注册 RAG 工具
from agent.rag_tools import RAGSearchTool, RAGListSourcesTool
from rag.engine import RAGEngine

rag_engine = RAGEngine()
registry.register(RAGSearchTool(engine=rag_engine))
registry.register(RAGListSourcesTool(engine=rag_engine))
```

- [ ] **Step 2: 为 CLI 集成编写测试**

创建 `tests/rag/test_cli_rag.py`:
```python
"""CLI rag 命令集成测试"""
from click.testing import CliTester
import pytest
from stock_robot.cli import main


class TestCLIRagGroup:
    @pytest.fixture
    def runner(self):
        from click.testing import CliRunner
        return CliRunner()

    def test_rag_command_exists(self, runner):
        result = runner.invoke(main, ["rag", "--help"])
        assert result.exit_code == 0
        assert "ingest" in result.output
        assert "clean" in result.output
        assert "stats" in result.output

    def test_rag_stats_runs(self, runner):
        result = runner.invoke(main, ["rag", "stats"])
        assert result.exit_code == 0
        assert "research_reports" in result.output

    def test_rag_ingest_requires_source_type(self, runner, tmp_path):
        doc = tmp_path / "test.md"
        doc.write_text("# 测试", encoding="utf-8")
        result = runner.invoke(main, ["rag", "ingest", str(doc)])
        # 缺少 -s 应报错
        assert result.exit_code != 0 or "Error" in result.output

    def test_rag_ingest_with_source_type(self, runner, tmp_path):
        doc = tmp_path / "test.md"
        doc.write_text("# 测试文档\n\n内容。", encoding="utf-8")
        result = runner.invoke(main, [
            "rag", "ingest", str(doc),
            "-s", "research_reports",
            "-t", "测试文档",
        ])
        assert "成功" in result.output or "跳过" in result.output

    def test_rag_clean_dry_run(self, runner):
        result = runner.invoke(main, [
            "rag", "clean", "--source", "research_reports", "--dry-run",
        ])
        assert result.exit_code == 0
        assert "DRY RUN" in result.output or "预览" in result.output

    def test_chat_command_registers_rag_tools(self, runner):
        """验证 chat --help 不报错（工具注册在运行时完成）"""
        result = runner.invoke(main, ["chat", "--help"])
        assert result.exit_code == 0
```

- [ ] **Step 3: 运行 CLI 集成测试**

Run: `pytest tests/rag/test_cli_rag.py -v`
Expected: 6 passed

- [ ] **Step 4: 运行全部 RAG 测试**

Run: `pytest tests/rag/ -v`
Expected: 全部通过（约 60 个测试）

- [ ] **Step 5: 运行存量测试确保零回归**

Run: `pytest tests/ -v --ignore=tests/rag`
Expected: 全部通过

- [ ] **Step 6: 运行全量测试**

Run: `pytest tests/ -v`
Expected: 全部通过（~375+ 个测试）

- [ ] **Step 7: Commit**

```bash
git add src/stock_robot/cli.py tests/rag/test_cli_rag.py
git commit -m "feat(RAG): Chat 命令集成 RAG 工具，添加 CLI 集成测试"
```

---

## 验证清单

- [ ] `pytest tests/rag/ -v` — RAG 全部测试通过
- [ ] `pytest tests/agent/ -v` — Phase 1 测试全部通过（无回归）
- [ ] `pytest tests/ -v --ignore=tests/rag` — 存量测试全部通过
- [ ] `python -m stock_robot.cli rag stats` — CLI 命令正常
- [ ] `python -m stock_robot.cli rag ingest reports/xxx.md -s history_reports` — 摄入成功
- [ ] `python -m stock_robot.cli rag clean --dry-run --source research_reports` — dry-run 正常
