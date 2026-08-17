"""pipeline_tools 单元测试"""
import pytest

from agent.pipeline_tools import (
    AnalyzeIndexTool,
    AnalyzeStockTool,
    GetSnapshotTool,
    ScreenStocksTool,
)


class TestAnalyzeStockTool:
    def test_has_correct_metadata(self):
        tool = AnalyzeStockTool()
        assert tool.name == "analyze_stock"
        assert tool.source == "pipeline"
        assert "pipeline" in tool.tags
        assert "stock" in tool.tags
        assert tool.parameters["type"] == "object"
        assert "symbol" in tool.parameters["required"]

    @pytest.mark.asyncio
    async def test_execute_returns_error_when_pipeline_fails(self, mocker):
        mock_pipeline = mocker.Mock()
        mock_pipeline.run.side_effect = Exception("数据源不可用")

        tool = AnalyzeStockTool(pipeline=mock_pipeline)
        result = await tool.execute(symbol="000001")

        assert result.status == "error"
        assert "数据源不可用" in (result.error or "")
        assert (result.metadata or {}).get("source") == "pipeline"

    @pytest.mark.asyncio
    async def test_execute_calls_pipeline_with_correct_target(self, mocker):
        mock_pipeline = mocker.Mock()
        mock_report = mocker.Mock()
        mock_report.code = "000001"
        mock_report.name = "平安银行"
        mock_report.overview = {}
        mock_report.comments = []
        mock_report.dimensions = {}
        mock_pipeline.run.return_value = mocker.Mock(
            reports=[mock_report],
            errors=[],
        )

        tool = AnalyzeStockTool(pipeline=mock_pipeline)
        result = await tool.execute(symbol="000001")

        assert result.status == "success"
        mock_pipeline.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_returns_error_for_empty_symbol(self):
        tool = AnalyzeStockTool()
        result = await tool.execute(symbol="")
        assert result.status == "error"
        assert "代码" in (result.error or "")

    @pytest.mark.asyncio
    async def test_execute_returns_error_when_pipeline_not_injected(self):
        tool = AnalyzeStockTool()
        result = await tool.execute(symbol="000001")
        assert result.status == "error"
        assert "未注入" in (result.error or "")


class TestAnalyzeIndexTool:
    def test_has_correct_metadata(self):
        tool = AnalyzeIndexTool()
        assert tool.name == "analyze_index"
        assert tool.source == "pipeline"
        assert "pipeline" in tool.tags
        assert "index" in tool.tags
        assert tool.parameters["type"] == "object"
        assert "symbol" in tool.parameters["required"]

    @pytest.mark.asyncio
    async def test_execute_returns_error_for_empty_symbol(self):
        tool = AnalyzeIndexTool()
        result = await tool.execute(symbol="")
        assert result.status == "error"
        assert "代码" in (result.error or "")

    @pytest.mark.asyncio
    async def test_execute_returns_error_when_pipeline_fails(self, mocker):
        mock_pipeline = mocker.Mock()
        mock_pipeline.run.side_effect = Exception("IndexPipeline 执行失败")

        tool = AnalyzeIndexTool(index_pipeline=mock_pipeline)
        result = await tool.execute(symbol="000300")

        assert result.status == "error"
        assert "IndexPipeline 执行失败" in (result.error or "")

    @pytest.mark.asyncio
    async def test_execute_returns_analysis_report(self, mocker):
        mock_pipeline = mocker.Mock()
        mock_report = mocker.Mock()
        mock_report.code = "000300"
        mock_report.name = "沪深300"
        mock_report.overview = {"pe_ttm": 12.5}
        mock_report.composite_comment = "谨慎看多"
        mock_report.position_coeff = 0.7
        mock_pipeline.run.return_value = mocker.Mock(
            reports=[mock_report],
            errors=[],
        )

        tool = AnalyzeIndexTool(index_pipeline=mock_pipeline)
        result = await tool.execute(symbol="000300")

        assert result.status == "success"
        assert result.data["code"] == "000300"
        assert result.data["name"] == "沪深300"
        assert result.data["overview"] == {"pe_ttm": 12.5}

    @pytest.mark.asyncio
    async def test_execute_returns_error_when_no_reports(self, mocker):
        mock_pipeline = mocker.Mock()
        mock_pipeline.run.return_value = mocker.Mock(
            reports=[],
            errors=[],
        )

        tool = AnalyzeIndexTool(index_pipeline=mock_pipeline)
        result = await tool.execute(symbol="000300")

        assert result.status == "error"


class TestGetSnapshotTool:
    def test_has_correct_metadata(self):
        tool = GetSnapshotTool()
        assert tool.name == "get_snapshot"
        assert tool.source == "pipeline"
        assert "valuation" in tool.tags
        assert "quick" in tool.tags

    @pytest.mark.asyncio
    async def test_execute_returns_valuation_snapshot(self, mocker):
        mock_pipeline = mocker.Mock()
        mock_pipeline.get_snapshot.return_value = {
            "symbol": "000300",
            "pe_ttm": 12.5,
            "pe_percentile": 0.35,
            "valuation_valid": True,
        }

        tool = GetSnapshotTool(index_pipeline=mock_pipeline)
        result = await tool.execute(symbol="000300")

        assert result.status == "success"
        assert result.data["pe_ttm"] == 12.5
        assert result.data["pe_percentile"] == 0.35

    @pytest.mark.asyncio
    async def test_execute_returns_error_when_snapshot_is_none(self, mocker):
        mock_pipeline = mocker.Mock()
        mock_pipeline.get_snapshot.return_value = None

        tool = GetSnapshotTool(index_pipeline=mock_pipeline)
        result = await tool.execute(symbol="999999")

        assert result.status == "error"
        assert "快照" in (result.error or "") or "不支持" in (result.error or "")

    @pytest.mark.asyncio
    async def test_execute_returns_error_when_pipeline_fails(self, mocker):
        mock_pipeline = mocker.Mock()
        mock_pipeline.get_snapshot.side_effect = Exception("估值数据源不可用")

        tool = GetSnapshotTool(index_pipeline=mock_pipeline)
        result = await tool.execute(symbol="000300")

        assert result.status == "error"
        assert "估值数据源不可用" in (result.error or "")


class TestScreenStocksTool:
    def test_has_correct_metadata(self):
        tool = ScreenStocksTool()
        assert tool.name == "screen_stocks"
        assert tool.source == "pipeline"
        assert "screening" in tool.tags

    @pytest.mark.asyncio
    async def test_execute_returns_error_when_industry_filter_not_found(self, mocker):
        tool = ScreenStocksTool()
        result = await tool.execute(industry="不存在的行业")

        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_execute_returns_error_for_empty_industry(self):
        tool = ScreenStocksTool()
        result = await tool.execute(industry="")
        assert result.status == "error"
        assert "行业" in (result.error or "")
