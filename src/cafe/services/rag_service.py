"""Cafe services rag service module."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from cafe.config import Settings, get_settings
from cafe.core.observability import observed_span

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
        """Return one vector per input text.

        Args:
            - texts: list[str] - The texts value.

        Returns:
            - return list[list[float]] - The return value.
        """


class FastEmbedder:
    def __init__(self, model: str, dimension: int) -> None:
        """Initialize the instance.

        Args:
            - model: str - The model value.
            - dimension: int - The dimension value.

        Returns:
            - return None - The return value.
        """
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model)
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Handle embed.

        Args:
            - texts: list[str] - The texts value.

        Returns:
            - return list[list[float]] - The return value.
        """
        return [vector.tolist() for vector in self._model.embed(texts)]


def rag_sources(settings: Settings | None = None) -> dict[str, RagSource]:
    """Handle rag sources.

    Args:
        - settings: Settings | None - The settings value.

    Returns:
        - return dict[str, RagSource] - The return value.
    """
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


def chunk_markdown(
    text: str, *, max_chars: int = 1200, overlap: int = 150
) -> list[str]:
    """Small markdown-aware chunker that keeps chunks readable.

    Args:
        - text: str - The text value.
        - max_chars: int - The max chars value.
        - overlap: int - The overlap value.

    Returns:
        - return list[str] - The return value.
    """
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
        """Initialize the instance.

        Args:
            - client: QdrantClient - The client value.
            - embedder: Embedder | None - The embedder value.
            - vector_size: int | None - The vector size value.

        Returns:
            - return None - The return value.
        """
        if embedder is None and vector_size is None:
            raise ValueError("Either embedder or vector_size is required.")

        self._client = client
        self._embedder = embedder
        self._vector_size = vector_size or embedder.dimension

    def create_collection(
        self, collection_name: str, *, recreate: bool = False
    ) -> bool:
        """Handle create collection.

        Args:
            - collection_name: str - The collection name value.
            - recreate: bool - The recreate value.

        Returns:
            - return bool - The return value.
        """
        exists = self._client.collection_exists(collection_name)
        if exists and not recreate:
            return False

        if exists:
            self._client.delete_collection(collection_name)

        self._client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(
                size=self._vector_size, distance=Distance.COSINE
            ),
        )
        return True

    def create_collections(
        self, sources: dict[str, RagSource], *, recreate: bool = False
    ) -> dict[str, bool]:
        """Handle create collections.

        Args:
            - sources: dict[str, RagSource] - The sources value.
            - recreate: bool - The recreate value.

        Returns:
            - return dict[str, bool] - The return value.
        """
        return {
            name: self.create_collection(source.collection_name, recreate=recreate)
            for name, source in sources.items()
        }

    def index_source(self, source: RagSource, *, recreate: bool = True) -> int:
        """Handle index source.

        Args:
            - source: RagSource - The source value.
            - recreate: bool - The recreate value.

        Returns:
            - return int - The return value.
        """
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

    def retrieve(
        self, collection_name: str, query: str, *, limit: int = 5
    ) -> list[RagHit]:
        """Handle retrieve.

        Args:
            - collection_name: str - The collection name value.
            - query: str - The query value.
            - limit: int - The limit value.

        Returns:
            - return list[RagHit] - The return value.
        """
        with observed_span(
            "qdrant",
            "qdrant.retrieve",
            {"collection": collection_name, "limit": limit},
        ) as span:
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
            span.update(result_count=len(hits))
            return hits

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Handle embed.

        Args:
            - texts: list[str] - The texts value.

        Returns:
            - return list[list[float]] - The return value.
        """
        if self._embedder is None:
            raise RuntimeError("An embedder is required for indexing and retrieval.")
        return self._embedder.embed(texts)


@lru_cache(maxsize=8)
def _cached_rag_service(
    qdrant_url: str,
    qdrant_api_key: str,
    embedding_model: str,
    embedding_dimensions: int,
) -> RagService:
    """Handle cached rag service.

    Args:
        - qdrant_url: str - The qdrant url value.
        - qdrant_api_key: str - The qdrant api key value.
        - embedding_model: str - The embedding model value.
        - embedding_dimensions: int - The embedding dimensions value.

    Returns:
        - return RagService - The return value.
    """
    client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key or None)
    embedder = FastEmbedder(model=embedding_model, dimension=embedding_dimensions)
    return RagService(client=client, embedder=embedder)


def build_rag_service(
    settings: Settings | None = None,
    *,
    cached: bool = True,
) -> RagService:
    """Build the rag service.

    Args:
        - settings: Settings | None - The settings value.
        - cached: bool - The cached value.

    Returns:
        - return RagService - The return value.
    """
    s = settings or get_settings()
    if cached:
        return _cached_rag_service(
            s.qdrant_url,
            s.qdrant_api_key,
            s.embedding_model,
            s.embedding_dimensions,
        )
    client = QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key or None)
    embedder = FastEmbedder(model=s.embedding_model, dimension=s.embedding_dimensions)
    return RagService(client=client, embedder=embedder)


def warm_rag_service(settings: Settings | None = None) -> RagService:
    """Handle warm rag service.

    Args:
        - settings: Settings | None - The settings value.

    Returns:
        - return RagService - The return value.
    """
    return build_rag_service(settings=settings, cached=True)


def clear_rag_service_cache() -> None:
    """Handle clear rag service cache.

    Returns:
        - return None - The return value.
    """
    _cached_rag_service.cache_clear()


def create_qdrant_collections(
    *, recreate: bool = False, settings: Settings | None = None
) -> dict[str, bool]:
    """Create all configured Qdrant collections.

    Args:
        - recreate: bool - Whether to recreate existing collections.
        - settings: Settings | None - Optional settings override.

    Returns:
        - return dict[str, bool] - Created status by source name.
    """
    s = settings or get_settings()
    client = QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key or None)
    service = RagService(client=client, vector_size=s.embedding_dimensions)
    return service.create_collections(rag_sources(s), recreate=recreate)


def index_all_sources(
    *, recreate: bool = True, settings: Settings | None = None
) -> dict[str, int]:
    """Index all configured markdown sources into Qdrant.

    Args:
        - recreate: bool - Whether to recreate collections before indexing.
        - settings: Settings | None - Optional settings override.

    Returns:
        - return dict[str, int] - Indexed point counts by source name.
    """
    service = build_rag_service(settings)
    return {
        name: service.index_source(source, recreate=recreate)
        for name, source in rag_sources(settings).items()
    }
