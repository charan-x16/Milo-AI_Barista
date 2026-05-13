"""Tests test rag service module."""

import math
import re

from qdrant_client import QdrantClient

from cafe.config import Settings
from cafe.services import rag_service
from cafe.services.rag_service import RagService, rag_sources


class KeywordEmbedder:
    vocabulary = [
        "espresso",
        "coffee",
        "cappuccino",
        "refund",
        "wrong",
        "allergen",
        "payment",
        "wifi",
        "vegan",
        "oat",
        "caffeine",
        "sweetness",
        "milk",
    ]
    dimension = len(vocabulary) + 1

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Verify embed.

        Args:
            - texts: list[str] - The texts value.

        Returns:
            - return list[list[float]] - The return value.
        """
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        """Verify embed one.

        Args:
            - text: str - The text value.

        Returns:
            - return list[float] - The return value.
        """
        words = re.findall(r"[a-z]+", text.casefold())
        vector = [float(words.count(term)) for term in self.vocabulary]
        vector.append(0.01)

        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector]


def test_indexes_product_doc_into_product_collection():
    """Verify indexes product doc into product collection.

    Returns:
        - return None - The return value.
    """
    source = rag_sources()["product"]
    service = RagService(QdrantClient(":memory:"), KeywordEmbedder())

    count = service.index_source(source)
    hits = service.retrieve(source.collection_name, "espresso coffee", limit=3)

    assert count > 0
    assert hits
    assert hits[0].source == "BTB_Menu_Enhanced.md"
    assert "coffee" in " ".join(hit.text for hit in hits).casefold()


def test_creates_agent_collections():
    """Verify creates agent collections.

    Returns:
        - return None - The return value.
    """
    sources = rag_sources()
    client = QdrantClient(":memory:")
    service = RagService(client, KeywordEmbedder())

    created = service.create_collections(sources)

    assert created == {"product": True, "menu_attributes": True, "support": True}
    assert client.collection_exists(sources["product"].collection_name)
    assert client.collection_exists(sources["menu_attributes"].collection_name)
    assert client.collection_exists(sources["support"].collection_name)


def test_indexes_support_doc_into_support_collection():
    """Verify indexes support doc into support collection.

    Returns:
        - return None - The return value.
    """
    source = rag_sources()["support"]
    service = RagService(QdrantClient(":memory:"), KeywordEmbedder())

    count = service.index_source(source)
    hits = service.retrieve(source.collection_name, "refund for wrong item", limit=3)

    assert count > 0
    assert hits
    assert hits[0].source == "BTB_Company_Policies.md"
    assert "refund" in " ".join(hit.text for hit in hits).casefold()


def test_indexes_menu_attributes_doc_into_attributes_collection():
    """Verify indexes menu attributes doc into attributes collection.

    Returns:
        - return None - The return value.
    """
    source = rag_sources()["menu_attributes"]
    service = RagService(QdrantClient(":memory:"), KeywordEmbedder())

    count = service.index_source(source)
    hits = service.retrieve(source.collection_name, "caffeine sweetness milk", limit=3)

    assert count > 0
    assert hits
    assert hits[0].source == "BTB_Menu_Attributes.md"
    assert "caffeine" in " ".join(hit.text for hit in hits).casefold()


def test_build_rag_service_uses_cache_for_same_settings(monkeypatch):
    """Verify repeated RAG construction uses the process cache.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - This test has no return value.
    """

    class FakeQdrantClient:
        def __init__(self, **kwargs):
            """Initialize the fake client.

            Args:
                - kwargs: Any - The kwargs value.

            Returns:
                - return None - This helper has no return value.
            """
            self.kwargs = kwargs

    class FakeEmbedder:
        def __init__(self, *, model, dimension):
            """Initialize the fake embedder.

            Args:
                - model: Any - The model value.
                - dimension: Any - The dimension value.

            Returns:
                - return None - This helper has no return value.
            """
            self.model = model
            self.dimension = dimension

        def embed(self, texts):
            """Embed text.

            Args:
                - texts: Any - The texts value.

            Returns:
                - return list[list[float]] - The fake vectors.
            """
            return [[0.0] for _text in texts]

    rag_service.clear_rag_service_cache()
    monkeypatch.setattr(rag_service, "QdrantClient", FakeQdrantClient)
    monkeypatch.setattr(rag_service, "FastEmbedder", FakeEmbedder)
    settings = Settings(
        _env_file=None,
        qdrant_url="http://qdrant.test",
        qdrant_api_key="key",
        embedding_model="fake",
        embedding_dimensions=1,
    )

    first = rag_service.build_rag_service(settings)
    second = rag_service.build_rag_service(settings)

    assert first is second
