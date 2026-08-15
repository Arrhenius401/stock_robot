"""bootstrap 组装测试"""
import pytest

from api.bootstrap import build_agent_core
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
