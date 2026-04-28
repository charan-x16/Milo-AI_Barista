from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from cafe.config import Settings, get_settings


DOCS_DIR = Path(__file__).resolve().parents[1] / "Docs"


@dataclass(frozen=True)
class RagSource:
    agent: str
    collection_name: str
    path: Path


@dataclass(frozen=True)
class RagChunk:
    id: str
    text: str
    source: str
    chunk_index: int


@dataclass(frozen=True)
class RagHit:
    text: str
    score: float
    source: str
    chunk_index: int


class Embedder(Protocol):
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text."""


class FastEmbedder:
    def __init__(self, model: str, dimension: int) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model)
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.embed(texts)]


def rag_sources(settings: Settings | None = None) -> dict[str, RagSource]:
    s = settings or get_settings()
    return {
        "product": RagSource(
            agent="product",
            collection_name=s.qdrant_product_collection,
            path=DOCS_DIR / "BTB_Menu_Enhanced.md",
        ),
        "menu_attributes": RagSource(
            agent="product",
            collection_name=s.qdrant_menu_attributes_collection,
            path=DOCS_DIR / "BTB_Menu_Attributes.md",
        ),
        "support": RagSource(
            agent="support",
            collection_name=s.qdrant_support_collection,
            path=DOCS_DIR / "BTB_Company_Policies.md",
        ),
    }


def chunk_markdown(text: str, *, max_chars: int = 1200, overlap: int = 150) -> list[str]:
    """Small markdown-aware chunker that keeps chunks readable."""
    blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
    chunks: list[str] = []
    current = ""

    for block in blocks:
        if current and len(current) + len(block) + 2 > max_chars:
            chunks.append(current.strip())
            tail = current[-overlap:].strip() if overlap else ""
            current = f"{tail}\n\n{block}" if tail else block
        else:
            current = f"{current}\n\n{block}" if current else block

    if current.strip():
        chunks.append(current.strip())

    return chunks


class RagService:
    def __init__(
        self,
        client: QdrantClient,
        embedder: Embedder | None = None,
        *,
        vector_size: int | None = None,
    ) -> None:
        if embedder is None and vector_size is None:
            raise ValueError("Either embedder or vector_size is required.")

        self._client = client
        self._embedder = embedder
        self._vector_size = vector_size or embedder.dimension

    def create_collection(self, collection_name: str, *, recreate: bool = False) -> bool:
        exists = self._client.collection_exists(collection_name)
        if exists and not recreate:
            return False

        if exists:
            self._client.delete_collection(collection_name)

        self._client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=self._vector_size, distance=Distance.COSINE),
        )
        return True

    def create_collections(self, sources: dict[str, RagSource], *, recreate: bool = False) -> dict[str, bool]:
        return {
            name: self.create_collection(source.collection_name, recreate=recreate)
            for name, source in sources.items()
        }

    def index_source(self, source: RagSource, *, recreate: bool = True) -> int:
        text = source.path.read_text(encoding="utf-8")
        chunks = [
            RagChunk(
                id=str(uuid5(NAMESPACE_URL, f"{source.collection_name}:{i}:{chunk}")),
                text=chunk,
                source=source.path.name,
                chunk_index=i,
            )
            for i, chunk in enumerate(chunk_markdown(text))
        ]

        self.create_collection(source.collection_name, recreate=recreate)

        vectors = self._embed([chunk.text for chunk in chunks])
        points = [
            PointStruct(
                id=chunk.id,
                vector=vector,
                payload={
                    "agent": source.agent,
                    "source": chunk.source,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self._client.upsert(collection_name=source.collection_name, points=points)
        return len(points)

    def retrieve(self, collection_name: str, query: str, *, limit: int = 5) -> list[RagHit]:
        query_vector = self._embed([query])[0]
        results = self._client.query_points(
            collection_name=collection_name,
            query=query_vector,
            limit=limit,
            with_payload=True,
        ).points

        hits: list[RagHit] = []
        for point in results:
            payload = point.payload or {}
            hits.append(
                RagHit(
                    text=str(payload.get("text", "")),
                    score=float(point.score),
                    source=str(payload.get("source", "")),
                    chunk_index=int(payload.get("chunk_index", 0)),
                )
            )
        return hits

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if self._embedder is None:
            raise RuntimeError("An embedder is required for indexing and retrieval.")
        return self._embedder.embed(texts)


def build_rag_service(settings: Settings | None = None) -> RagService:
    s = settings or get_settings()
    client = QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key or None)
    embedder = FastEmbedder(model=s.embedding_model, dimension=s.embedding_dimensions)
    return RagService(client=client, embedder=embedder)


def create_qdrant_collections(*, recreate: bool = False, settings: Settings | None = None) -> dict[str, bool]:
    s = settings or get_settings()
    client = QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key or None)
    service = RagService(client=client, vector_size=s.embedding_dimensions)
    return service.create_collections(rag_sources(s), recreate=recreate)


def index_all_sources(*, recreate: bool = True, settings: Settings | None = None) -> dict[str, int]:
    service = build_rag_service(settings)
    return {
        name: service.index_source(source, recreate=recreate)
        for name, source in rag_sources(settings).items()
    }
