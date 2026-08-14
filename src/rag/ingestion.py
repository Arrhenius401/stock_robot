"""文档摄入流水线 — 加载→哈希去重→分块→embed→upsert"""
import hashlib
import logging
from datetime import datetime
from pathlib import Path

from rag.chunkers import ChunkerRegistry
from rag.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".yaml", ".yml"}


class IngestionPipeline:
    def __init__(self, embedding_provider: EmbeddingProvider):
        self._embedding = embedding_provider
        self._chunker_registry = ChunkerRegistry()

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
        symbols = symbols or []
        tags = tags or []
        ingested_at = datetime.now().astimezone().isoformat()

        try:
            text = self.load_document(file_path)
        except Exception as e:  # noqa: BLE001 — 文件加载异常统一转错误结果
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
        symbols: list[str],
        tags: list[str],
        ingested_at: str,
        date: str | None = None,
    ) -> dict:
        chunker = self._chunker_registry.get(source_type)
        chunks = chunker.chunk(text)

        if not chunks:
            logger.warning("文档分块为空: %s", source_path)
            return {"status": "skipped", "reason": "empty_chunks", "chunks_count": 0}

        source_hash = self.compute_hash(text)
        embeddings = self._embedding.embed(chunks)

        ids = []
        metadatas = []
        for i in range(len(chunks)):
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

    def compute_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def load_document(self, file_path: str) -> str:
        path = Path(file_path)
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"不支持的文件格式: {suffix}，支持: {SUPPORTED_EXTENSIONS}")
        return path.read_text(encoding="utf-8")

    def is_duplicate(self, collection, doc_hash: str) -> bool:
        try:
            existing = collection.get(
                where={"source_hash": doc_hash},
                limit=1,
            )
            return len(existing.get("metadatas", [])) > 0
        except Exception:  # noqa: BLE001 — 查重失败视为不重复
            logger.debug("文档查重失败: %s", doc_hash)
            return False
