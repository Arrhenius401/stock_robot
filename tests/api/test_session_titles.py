"""会话标题生成测试。"""

import pytest

from api.session_titles import (
    SessionTitleRefiner,
    derive_session_title,
    normalize_generated_title,
)


def test_derive_title_keeps_symbols_and_research_intent():
    """证券代码与估值趋势意图应形成可读标题。"""
    assert derive_session_title("帮我分析 600519 近期估值和技术趋势") == "600519估值与趋势"


def test_derive_title_compacts_comparison_request():
    """比较两个指数时应保留标的和比较意图。"""
    assert derive_session_title("请比较沪深300和中证红利最近表现") == "沪深300与中证红利比较"


def test_generated_title_rejects_multiline_or_overlong_value():
    """模型多行或超长输出不能作为会话标题。"""
    assert normalize_generated_title("第一行\n第二行") is None
    assert normalize_generated_title("过" * 21) is None


def test_generated_title_normalizes_whitespace_and_paired_quotes():
    """模型标题应去引号、压缩空白。"""
    assert normalize_generated_title('  "  贵州茅台   估值分析  "  ') == "贵州茅台 估值分析"


def test_derive_title_uses_safe_fallback_for_unknown_request():
    """未知请求应去口语前缀并安全截断。"""
    assert derive_session_title("请帮我分析一下这个公司的护城河和管理层") == "这个公司的护城河和管理层"


def test_derive_title_defaults_for_empty_message():
    """空请求应生成默认会话标题。"""
    assert derive_session_title("   ") == "新会话"


class _Response:
    def __init__(self, content: str):
        self.content = content


class _SuccessfulModel:
    async def ainvoke(self, messages: list[dict[str, str]]) -> _Response:
        return _Response("  '宁德时代成长展望'  ")


class _FailingModel:
    async def ainvoke(self, messages: list[dict[str, str]]) -> _Response:
        raise RuntimeError("模型不可用")


class _InvalidResultModel:
    async def ainvoke(self, messages: list[dict[str, str]]) -> _Response:
        return _Response("标题一\n标题二")


@pytest.mark.asyncio
async def test_refiner_returns_normalized_model_title():
    """模型有效输出应覆盖本地回退标题。"""
    title = await SessionTitleRefiner(_SuccessfulModel()).refine("分析宁德时代", "宁德时代分析")
    assert title == "宁德时代成长展望"


@pytest.mark.asyncio
async def test_refiner_returns_fallback_without_model():
    """未配置模型时应直接使用本地标题。"""
    title = await SessionTitleRefiner().refine("分析宁德时代", "宁德时代分析")
    assert title == "宁德时代分析"


@pytest.mark.asyncio
async def test_refiner_returns_fallback_when_model_fails():
    """模型调用异常不应阻断标题回退。"""
    title = await SessionTitleRefiner(_FailingModel()).refine("分析宁德时代", "宁德时代分析")
    assert title == "宁德时代分析"


@pytest.mark.asyncio
async def test_refiner_returns_fallback_for_invalid_model_title():
    """模型返回无效标题时应保留本地标题。"""
    title = await SessionTitleRefiner(_InvalidResultModel()).refine("分析宁德时代", "宁德时代分析")
    assert title == "宁德时代分析"
