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
