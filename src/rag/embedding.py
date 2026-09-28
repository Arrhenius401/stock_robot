"""Embedding 提供者 — 模型抽象、bge-small-zh 本地模型、关键词降级"""
import logging
import threading
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384  # bge-small-zh 输出维度

# 模型加载约 30 秒且与配置无关，热重载重建 RAG 时复用同一实例
_shared_provider: "EmbeddingProvider | None" = None
_provider_lock = threading.Lock()

# 未安装时置为 None，由 create_embedding_provider 统一降级
try:
    from sentence_transformers import (  # pyright: ignore[reportMissingImports]
        SentenceTransformer,
    )
except ImportError:
    SentenceTransformer = None


class EmbeddingProvider(ABC):
    """Embedding 模型抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class SentenceTransformersProvider(EmbeddingProvider):
    MODEL_NAME = "BAAI/bge-small-zh"

    def __init__(self):
        if SentenceTransformer is None:
            raise ImportError(
                "sentence-transformers 未安装。"
                "请运行 pip install sentence-transformers"
            )
        # 模型延迟加载：服务启动不应因下载模型而阻塞。
        self._model = None
        self._model_lock = threading.Lock()

    def _load_model(self):
        """首次调用时加载本地 bge-small-zh 模型"""
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    assert SentenceTransformer is not None
                    self._model = SentenceTransformer(self.MODEL_NAME)
        return self._model

    @property
    def name(self) -> str:
        return "bge-small-zh"

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._load_model()
        embeddings = model.encode(texts, normalize_embeddings=True)
        if hasattr(embeddings, "tolist"):
            return embeddings.tolist()
        return [list(row) for row in embeddings]


class KeywordFallbackProvider(EmbeddingProvider):
    @property
    def name(self) -> str:
        return "keyword-fallback"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * EMBEDDING_DIM for _ in texts]


def create_embedding_provider() -> EmbeddingProvider:
    """返回进程内共享的 embedding 提供者（首次加载后跨调用复用）。"""
    global _shared_provider
    if _shared_provider is None:
        with _provider_lock:
            if _shared_provider is None:
                _shared_provider = _build_provider()
    return _shared_provider


def _build_provider() -> EmbeddingProvider:
    try:
        provider = SentenceTransformersProvider()
        logger.info("Embedding 模型已就绪，将在首次检索时加载: bge-small-zh")
        return provider
    except (ImportError, OSError) as e:
        logger.warning(
            "bge-small-zh 不可用 (%s)，降级为关键词检索模式。",
            e,
        )
        return KeywordFallbackProvider()
