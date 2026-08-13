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


class TestKeywordFallbackProvider:
    def test_provider_is_embedding_provider_subclass(self):
        assert issubclass(KeywordFallbackProvider, EmbeddingProvider)

    def test_name_returns_keyword_fallback(self):
        provider = KeywordFallbackProvider()
        assert provider.name == "keyword-fallback"

    def test_embed_returns_dummy_vectors(self):
        provider = KeywordFallbackProvider()
        result = provider.embed(["文本一", "文本二"])
        assert len(result) == 2
        assert len(result[0]) == 384
        assert all(v == 0.0 for v in result[0])

    def test_dimension_constant_is_384(self):
        from rag.embedding import EMBEDDING_DIM
        assert EMBEDDING_DIM == 384


class TestCreateEmbeddingProvider:
    def test_returns_provider_on_success(self, mocker):
        mock_instance = mocker.MagicMock()
        mock_instance.name = "bge-small-zh"
        mocker.patch(
            "rag.embedding.SentenceTransformersProvider",
            return_value=mock_instance,
        )
        provider = create_embedding_provider()
        assert provider is not None
        assert provider.name == "bge-small-zh"

    def test_falls_back_to_keyword_on_import_error(self, mocker):
        mocker.patch(
            "rag.embedding.SentenceTransformersProvider.__init__",
            side_effect=ImportError("not available"),
        )
        provider = create_embedding_provider()
        assert isinstance(provider, KeywordFallbackProvider)
