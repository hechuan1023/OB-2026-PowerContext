import threading
from typing import Any

import pyseekdb
from pyseekdb import HNSWConfiguration

from .config import get_settings
from .embeddings import get_embedder
from .ingest import ParsedDocument

INCLUDE = ["documents", "metadatas", "distances"]


def build_where(year_from: int | None, year_to: int | None, kind: str | None, doc_id: str | None = None) -> dict | None:
    conditions: list[dict] = []
    if year_from:
        conditions.append({"year": {"$gte": int(year_from)}})
    if year_to:
        conditions.append({"year": {"$lte": int(year_to)}})
    if kind:
        conditions.append({"kind": kind})
    if doc_id:
        conditions.append({"doc_id": doc_id})
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def rows_of(result: dict[str, Any], *, cosine_distance: bool = False) -> list[dict[str, Any]]:
    ids = (result.get("ids") or [[]])[0]
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    rows = []
    for index, chunk_id in enumerate(ids):
        metadata = metadatas[index] if index < len(metadatas) else {}
        distance = distances[index] if index < len(distances) else None
        numeric = distance if isinstance(distance, (int, float)) else None
        rows.append(
            {
                "chunk_id": chunk_id,
                "doc_id": metadata.get("doc_id", ""),
                "title": metadata.get("title", ""),
                "year": metadata.get("year", 0),
                "kind": metadata.get("kind", ""),
                "tags": metadata.get("tags", ""),
                "source": metadata.get("source", ""),
                "locator": metadata.get("locator", ""),
                "text": documents[index] if index < len(documents) else "",
                "score": round(1 - numeric, 4) if cosine_distance and numeric is not None else (
                    round(numeric, 4) if numeric is not None else None
                ),
                "score_label": "相似度" if cosine_distance else "检索得分",
            }
        )
    return rows


class SeekdbStore:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._collection = None
        self._lock = threading.RLock()

    @property
    def collection(self):
        if self._collection is None:
            with self._lock:
                if self._collection is None:
                    settings = self._settings
                    client = pyseekdb.Client(
                        host=settings.seekdb_host,
                        port=settings.seekdb_port,
                        tenant=settings.seekdb_tenant,
                        database=settings.seekdb_database,
                        user=settings.seekdb_user,
                        password=settings.seekdb_password,
                    )
                    self._collection = client.get_or_create_collection(
                        name=settings.collection_name,
                        configuration=HNSWConfiguration(dimension=settings.embedding_dimension),
                        embedding_function=None,
                    )
        return self._collection

    def ping(self) -> dict[str, Any]:
        with self._lock:
            return {"collection": self._settings.collection_name, "chunks": self.collection.count()}

    def add_document(self, document: ParsedDocument) -> int:
        if not document.chunks:
            return 0
        vectors = get_embedder().embed([chunk.text for chunk in document.chunks])
        collection = self.collection
        with self._lock:
            collection.add(
                ids=[f"{document.doc_id}:{index}" for index in range(len(document.chunks))],
                embeddings=vectors,
                documents=[chunk.text for chunk in document.chunks],
                metadatas=[
                    {
                        "doc_id": document.doc_id,
                        "title": document.title,
                        "year": document.year,
                        "kind": document.kind,
                        "tags": document.tags,
                        "source": document.source,
                        "locator": chunk.locator,
                        "chunk_index": index,
                    }
                    for index, chunk in enumerate(document.chunks)
                ],
            )
            collection.refresh_index()
        return len(document.chunks)

    def list_documents(self) -> list[dict[str, Any]]:
        documents: dict[str, dict[str, Any]] = {}
        offset = 0
        with self._lock:
            while True:
                batch = self.collection.get(include=["metadatas"], limit=500, offset=offset)
                ids = batch.get("ids") or []
                metadatas = batch.get("metadatas") or []
                if not ids:
                    break
                for metadata in metadatas:
                    doc_id = metadata.get("doc_id", "")
                    entry = documents.setdefault(
                        doc_id,
                        {
                            "doc_id": doc_id,
                            "title": metadata.get("title", ""),
                            "year": metadata.get("year", 0),
                            "kind": metadata.get("kind", ""),
                            "tags": metadata.get("tags", ""),
                            "source": metadata.get("source", ""),
                            "chunks": 0,
                        },
                    )
                    entry["chunks"] += 1
                offset += len(ids)
                if len(ids) < 500:
                    break
        return sorted(documents.values(), key=lambda item: item["title"])

    def delete_document(self, doc_id: str) -> None:
        with self._lock:
            self.collection.delete(where={"doc_id": doc_id})
            self.collection.refresh_index()

    def delete_by_source(self, source: str) -> None:
        with self._lock:
            self.collection.delete(where={"source": source})

    def search(
        self,
        query: str,
        *,
        mode: str = "hybrid",
        limit: int = 6,
        year_from: int | None = None,
        year_to: int | None = None,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        where = build_where(year_from, year_to, kind)
        collection = self.collection
        if mode == "fulltext":
            with self._lock:
                result = collection.hybrid_search(
                    query={"where_document": {"$contains": query}, "where": where, "n_results": limit},
                    n_results=limit,
                    include=INCLUDE,
                )
            return rows_of(result)

        vector = get_embedder().embed([query])[0]
        if mode == "vector":
            with self._lock:
                result = collection.query(
                    query_embeddings=[vector], where=where, n_results=limit, include=INCLUDE
                )
            return rows_of(result, cosine_distance=True)

        with self._lock:
            result = collection.hybrid_search(
                query={"where_document": {"$contains": query}, "where": where, "n_results": max(limit, 10)},
                knn={"query_embeddings": [vector], "where": where, "n_results": max(limit, 10)},
                rank={"rrf": {}},
                n_results=limit,
                include=INCLUDE,
            )
        return rows_of(result)


_store: SeekdbStore | None = None


def get_store() -> SeekdbStore:
    global _store
    if _store is None:
        _store = SeekdbStore()
    return _store
