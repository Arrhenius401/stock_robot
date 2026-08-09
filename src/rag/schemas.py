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

    _LIST_SEP = " | "  # 列表字段分隔符（ChromaDB 元数据仅支持简单类型）

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "source_path": self.source_path,
            "source_hash": self.source_hash,
            "title": self.title,
            "date": self.date or "",
            "symbols": self._LIST_SEP.join(self.symbols) if self.symbols else "",
            "tags": self._LIST_SEP.join(self.tags) if self.tags else "",
            "chunk_index": self.chunk_index,
            "ingested_at": self.ingested_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChunkMetadata":
        raw_symbols = d.get("symbols", "")
        raw_tags = d.get("tags", "")
        return cls(
            source_type=d.get("source_type", ""),
            source_path=d.get("source_path", ""),
            source_hash=d.get("source_hash", ""),
            title=d.get("title", ""),
            date=d.get("date") or None,
            symbols=[s.strip() for s in raw_symbols.split(cls._LIST_SEP) if s.strip()]
                if isinstance(raw_symbols, str) and raw_symbols.strip()
                else raw_symbols if isinstance(raw_symbols, list) else [],
            tags=[t.strip() for t in raw_tags.split(cls._LIST_SEP) if t.strip()]
                if isinstance(raw_tags, str) and raw_tags.strip()
                else raw_tags if isinstance(raw_tags, list) else [],
            chunk_index=d.get("chunk_index", 0),
            ingested_at=d.get("ingested_at", ""),
        )
