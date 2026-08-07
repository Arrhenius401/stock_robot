"""文档分块策略 — 6 种 Collection 类型各自的分块逻辑"""
import re
from abc import ABC, abstractmethod


class Chunker(ABC):
    """文档分块器抽象基类"""

    @abstractmethod
    def chunk(self, text: str) -> list[str]:
        ...


class ResearchReportChunker(Chunker):
    """券商研报 — 按二级标题（##）切分"""

    def chunk(self, text: str) -> list[str]:
        if not text.strip():
            return []
        sections = re.split(r"\n(?=## )", text)
        return [s.strip() for s in sections if s.strip()]


class FinancialFilingChunker(Chunker):
    """财报/公告 — 按财报科目标题切分，找不到则降级为段落切分"""

    _FINANCIAL_HEADING = re.compile(
        r"\n(?=[一二三四五六七八九十]、|\([一二三四五六七八九十]\))"
    )

    def chunk(self, text: str) -> list[str]:
        if not text.strip():
            return []
        sections = self._FINANCIAL_HEADING.split(text)
        chunks = [s.strip() for s in sections if s.strip()]
        if len(chunks) <= 1:
            chunks = [p.strip() for p in text.split("\n\n") if p.strip()]
        return chunks


class PolicyMacroChunker(Chunker):
    """政策/宏观 — 按段落簇切分（每簇最多 3 段）"""

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
    """学术文献 — 按摘要/方法/结论等章节切分"""

    _SECTION_MARKERS = ["摘要", "abstract", "方法", "method", "结论", "conclusion",
                        "引言", "introduction", "讨论", "discussion"]

    def chunk(self, text: str) -> list[str]:
        if not text.strip():
            return []
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
    """项目历史报告 — 按分析维度（## 二级标题）切分，支持提取股票代码"""

    _SYMBOL_PATTERN = re.compile(r"（(\d{6})）|\((\d{6})\)")

    def chunk(self, text: str) -> list[str]:
        if not text.strip():
            return []
        sections = re.split(r"\n(?=## )", text)
        return [s.strip() for s in sections if s.strip()]

    def extract_symbols(self, text: str) -> list[str]:
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
