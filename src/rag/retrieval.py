"""检索流水线 — embedding→向量检索→重排→返回结果"""
import logging
from rag.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)


class RetrievalPipeline:
    def __init__(self, embedding_provider: EmbeddingProvider):
        self._embedding = embedding_provider
        self._is_keyword_mode = embedding_provider.name == "keyword-fallback"

    def search(
        self,
        collection,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[dict]:
        if self._is_keyword_mode:
            return self.keyword_search(collection, query, top_k, filters)
        return self._vector_search(collection, query, top_k, filters)

    def _vector_search(
        self,
        collection,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[dict]:
        query_embedding = self._embedding.embed([query])
        chroma_filter = self._build_chroma_filter(filters) if filters else None
        n_results = max(top_k * 2, 10)

        try:
            raw = collection.query(
                query_embeddings=query_embedding,
                n_results=n_results,
                where=chroma_filter,
            )
        except Exception as e:
            logger.error("ChromaDB 查询失败: %s", e)
            return []

        results = []
        docs = raw.get("documents", [[]])[0]
        metas = raw.get("metadatas", [[]])[0]
        dists = raw.get("distances", [[]])[0]
        ids = raw.get("ids", [[]])[0]

        for i, doc in enumerate(docs):
            meta = metas[i] if i < len(metas) else {}
            dist = dists[i] if i < len(dists) else 1.0
            similarity = max(0.0, 1.0 - dist)
            results.append({
                "id": ids[i] if i < len(ids) else "",
                "content": doc,
                "metadata": meta,
                "score": round(similarity, 4),
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    def keyword_search(
        self,
        collection,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[dict]:
        try:
            all_data = collection.get()
        except Exception as e:
            logger.error("ChromaDB get 失败: %s", e)
            return []

        docs = all_data.get("documents", [])
        metas = all_data.get("metadatas", [])
        ids = all_data.get("ids", [])

        results = []
        query_lower = query.lower()
        for i, doc in enumerate(docs):
            score = sum(1 for word in query_lower.split() if word in doc.lower())
            if score > 0:
                if self._match_filters(metas[i] if i < len(metas) else {}, filters):
                    results.append({
                        "id": ids[i] if i < len(ids) else "",
                        "content": doc,
                        "metadata": metas[i] if i < len(metas) else {},
                        "score": float(score),
                    })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    def _build_chroma_filter(self, filters: dict) -> dict:
        conditions = []
        for key, value in filters.items():
            if key == "symbol":
                conditions.append({"symbols": {"$contains": value}})
            elif key == "source_type":
                conditions.append({"source_type": value})
            elif key == "date_from":
                conditions.append({"date": {"$gte": value}})
            elif key == "date_to":
                conditions.append({"date": {"$lte": value}})
            else:
                conditions.append({key: value})
        if len(conditions) == 1:
            return conditions[0]
        elif conditions:
            return {"$and": conditions}
        return {}

    def _match_filters(self, metadata: dict, filters: dict | None) -> bool:
        if not filters:
            return True
        for key, value in filters.items():
            if key == "symbol":
                if value not in metadata.get("symbols", []):
                    return False
            elif key == "source_type":
                if metadata.get("source_type") != value:
                    return False
            elif key == "date_from":
                doc_date = metadata.get("date", "")
                if doc_date and doc_date < value:
                    return False
            elif key == "date_to":
                doc_date = metadata.get("date", "")
                if doc_date and doc_date > value:
                    return False
            else:
                if metadata.get(key) != value:
                    return False
        return True
