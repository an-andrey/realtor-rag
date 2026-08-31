from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb
from sentence_transformers import SentenceTransformer

from .config import (
    DEFAULT_CHROMA_PATH,
    DEFAULT_COLLECTION_NAME,
    DEFAULT_DB_PATH,
    DEFAULT_EMBEDDING_MODEL,
)
from .db import iter_listing_documents


class LocalSentenceTransformerEmbeddingFunction:
    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL) -> None:
        self.model_name = model_name
        self.model = SentenceTransformer(model_name, local_files_only=True)

    @staticmethod
    def name() -> str:
        return "sentence_transformer"

    def get_config(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "local_files_only": True,
            "normalize_embeddings": True,
        }

    def __call__(self, input: list[str]) -> list[list[float]]:
        embeddings = self.model.encode(input, normalize_embeddings=True)
        return embeddings.tolist()

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self(input)


def get_collection(
    persist_path: Path | str = DEFAULT_CHROMA_PATH,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
) -> Any:
    client = chromadb.PersistentClient(path=str(persist_path))
    embedding_fn = LocalSentenceTransformerEmbeddingFunction(embedding_model)
    return client.get_or_create_collection(
        name=collection_name,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )


def rebuild_property_index(
    db_path: Path | str = DEFAULT_DB_PATH,
    persist_path: Path | str = DEFAULT_CHROMA_PATH,
    collection_name: str = DEFAULT_COLLECTION_NAME,
) -> int:
    docs = iter_listing_documents(db_path)
    collection = get_collection(persist_path, collection_name)
    existing = collection.get(include=[])
    if existing.get("ids"):
        collection.delete(ids=existing["ids"])

    if not docs:
        return 0

    collection.add(
        ids=[doc["id"] for doc in docs],
        documents=[doc["text"] for doc in docs],
        metadatas=[doc["metadata"] for doc in docs],
    )
    return len(docs)


def semantic_property_search(
    query: str,
    n_results: int = 5,
    persist_path: Path | str = DEFAULT_CHROMA_PATH,
    collection_name: str = DEFAULT_COLLECTION_NAME,
) -> list[dict[str, Any]]:
    collection = get_collection(persist_path, collection_name)
    results = collection.query(query_texts=[query], n_results=max(1, min(n_results, 8)))
    rows: list[dict[str, Any]] = []
    ids = results.get("ids", [[]])[0]
    docs = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]
    for index, centris_id in enumerate(ids):
        rows.append(
            {
                "centris_id": centris_id,
                "document": docs[index] if index < len(docs) else "",
                "metadata": metadatas[index] if index < len(metadatas) else {},
                "distance": distances[index] if index < len(distances) else None,
            }
        )
    return rows


if __name__ == "__main__":
    count = rebuild_property_index()
    print(f"Indexed {count} properties into {DEFAULT_CHROMA_PATH}")
