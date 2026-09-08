"""bootstrap 组装测试"""
import pytest

from api.bootstrap import build_agent_core, build_llm
from utils.config import Config


@pytest.fixture
def config(tmp_path):
    return Config(config_dir=tmp_path)


class TestBuildAgentCore:
    def test_pipeline_injected_into_tools(self, config, mocker):
        mocker.patch("rag.engine.RAGEngine", side_effect=RuntimeError("无 ChromaDB"))
        core = build_agent_core(config)
        tool = core.registry.get("analyze_stock")
        assert tool is not None
        # 私有属性经 getattr 读取（pyright 对 ToolProtocol 无 _pipeline 声明）
        assert getattr(tool, "_pipeline", None) is not None
        index_tool = core.registry.get("analyze_index")
        snapshot_tool = core.registry.get("get_snapshot")
        assert index_tool is not None and getattr(index_tool, "_pipeline", None) is core.index_pipeline
        assert snapshot_tool is not None and getattr(snapshot_tool, "_pipeline", None) is core.index_pipeline

    def test_rag_unavailable_skipped(self, config, mocker):
        mocker.patch("rag.engine.RAGEngine", side_effect=RuntimeError("无 ChromaDB"))
        core = build_agent_core(config)
        names = {t.name for t in core.registry.list_all()}
        assert "analyze_stock" in names
        assert "rag_search" not in names
        assert "rag_list_sources" not in names

    def test_rag_registered_when_available(self, config, mocker):
        fake_engine = mocker.Mock()
        fake_engine.embedding_name = "test-embedding"
        mocker.patch("rag.engine.RAGEngine", return_value=fake_engine)
        core = build_agent_core(config)
        names = {t.name for t in core.registry.list_all()}
        assert "rag_search" in names
        assert "rag_list_sources" in names

    def test_llm_none_when_no_api_key(self, config, mocker):
        mocker.patch("rag.engine.RAGEngine", side_effect=RuntimeError("无 ChromaDB"))
        core = build_agent_core(config)
        assert core.llm is None


class TestBuildLLM:
    def test_openai_branch_plumbs_timeout_and_retry(self, config, mocker):
        mock_cls = mocker.patch("llm.openai.OpenAIAdapter")
        config.set("llm.api_key", "sk-test")
        config.set("llm.retry_times", 5)
        config.set("llm.timeout_seconds", 30)
        llm = build_llm(config)
        assert llm is mock_cls.return_value
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["retry_times"] == 5
        assert kwargs["timeout"] == 30

    def test_claude_branch_plumbs_timeout_and_retry(self, config, mocker):
        mock_cls = mocker.patch("llm.claude.ClaudeAdapter")
        config.set("llm.api_key", "sk-ant-test")
        config.set("llm.provider", "claude")
        config.set("llm.retry_times", 3)
        config.set("llm.timeout_seconds", 45)
        llm = build_llm(config)
        assert llm is mock_cls.return_value
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["retry_times"] == 3
        assert kwargs["timeout"] == 45

    def test_init_failure_returns_none(self, config, mocker):
        mocker.patch("llm.openai.OpenAIAdapter", side_effect=Exception("初始化失败"))
        config.set("llm.api_key", "sk-test")
        assert build_llm(config) is None

    def test_strict_init_failure_raises_runtime_error(self, config, mocker):
        mocker.patch("llm.openai.OpenAIAdapter", side_effect=Exception("初始化失败"))
        config.set("llm.api_key", "sk-test")
        with pytest.raises(RuntimeError, match="LLM 后端初始化失败"):
            build_llm(config, strict=True)

    def test_strict_agent_core_propagates_llm_initialization_failure(self, config, mocker):
        mocker.patch("llm.openai.OpenAIAdapter", side_effect=Exception("初始化失败"))
        config.set("llm.api_key", "sk-test")
        with pytest.raises(RuntimeError, match="LLM 后端初始化失败"):
            build_agent_core(config, strict_llm=True)
