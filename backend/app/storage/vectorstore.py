from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import chromadb
from chromadb.errors import NotFoundError

from app.api.errors import DomainError
from app.config import VectorStoreSettings

_METADATA_KEYS = {
    "doc_id",
    "doc_title",
    "section_path",
    "source_url",
    "chunk_index",
    "source_type",
    "page_number",
}
@dataclass(frozen=True)
class VectorHit:
    id: str
    text: str
    metadata: dict[str, Any]
    similarity: float


class PersistentVectorStore:
    def __init__(self, settings: VectorStoreSettings) -> None:
        self.settings = settings
        self.settings.directory.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.settings.directory))

    def collection_name(self, repository_id: str) -> str:
        if not repository_id:
            raise _index_error("仓库标识不能为空")
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", repository_id)
        if not sanitized.strip("_"):
            raise _index_error("仓库标识不能为空")
        return f"repo_{sanitized}"

    def upsert(
        self,
        repository_id: str,
        ids: list[str],
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
        if not (len(ids) == len(texts) == len(embeddings) == len(metadatas)):
            raise _index_error("向量索引数据长度不一致")
        if not ids:
            return
        try:
            self._collection(repository_id).upsert(
                ids=ids,
                documents=texts,
                embeddings=embeddings,
                metadatas=[self._metadata(metadata) for metadata in metadatas],
            )
        except DomainError:
            raise
        except Exception as error:
            raise _index_error("向量索引写入失败") from error

    def query(self, repository_id: str, embedding: list[float], top_k: int) -> list[VectorHit]:
        if top_k <= 0:
            return []
        try:
            collection = self.client.get_collection(self.collection_name(repository_id))
        except DomainError:
            raise
        except NotFoundError:
            return []
        except Exception as error:
            raise _index_error("向量索引集合不可用") from error
        try:
            result = collection.query(
                query_embeddings=[embedding], n_results=top_k, include=["documents", "metadatas", "distances"]
            )
        except Exception as error:
            raise _index_error("向量索引查询失败") from error
        hits: list[VectorHit] = []
        for identifier, text, metadata, distance in zip(
            result.get("ids", [[]])[0],
            result.get("documents", [[]])[0],
            result.get("metadatas", [[]])[0],
            result.get("distances", [[]])[0],
        ):
            similarity = max(-1.0, min(1.0, 1.0 - float(distance)))
            hits.append(
                VectorHit(
                    id=str(identifier), text=str(text), metadata=dict(metadata or {}), similarity=similarity
                )
            )
        return hits

    def delete(self, repository_id: str, ids: list[str]) -> None:
        if not ids:
            return
        try:
            collection = self.client.get_collection(self.collection_name(repository_id))
        except DomainError:
            raise
        except NotFoundError:
            return
        except Exception as error:
            raise _index_error("向量索引集合不可用") from error
        try:
            collection.delete(ids=ids)
        except Exception as error:
            raise _index_error("向量索引删除失败") from error

    def delete_collection(self, repository_id: str) -> None:
        try:
            self.client.delete_collection(self.collection_name(repository_id))
        except DomainError:
            raise
        except NotFoundError:
            return
        except Exception as error:
            raise _index_error("向量索引集合不可用") from error

    def _collection(self, repository_id: str):
        try:
            return self.client.get_or_create_collection(
                self.collection_name(repository_id), metadata={"hnsw:space": "cosine"}
            )
        except DomainError:
            raise
        except Exception as error:
            raise _index_error("向量索引集合不可用") from error

    @staticmethod
    def _metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in metadata.items()
            if key in _METADATA_KEYS and value is not None and isinstance(value, (str, int, float, bool))
        }


def _index_error(message: str) -> DomainError:
    return DomainError("INDEX_FAILED", message, 503, True, "重试索引操作")
