import math
import re

from qdrant_client import QdrantClient

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
    ]
    dimension = len(vocabulary) + 1

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        words = re.findall(r"[a-z]+", text.casefold())
        vector = [float(words.count(term)) for term in self.vocabulary]
        vector.append(0.01)

        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector]


def test_indexes_product_doc_into_product_collection():
    source = rag_sources()["product"]
    service = RagService(QdrantClient(":memory:"), KeywordEmbedder())

    count = service.index_source(source)
    hits = service.retrieve(source.collection_name, "espresso coffee", limit=3)

    assert count > 0
    assert hits
    assert hits[0].source == "BTB_Menu_Enhanced.md"
    assert "coffee" in " ".join(hit.text for hit in hits).casefold()


def test_creates_two_agent_collections():
    sources = rag_sources()
    client = QdrantClient(":memory:")
    service = RagService(client, KeywordEmbedder())

    created = service.create_collections(sources)

    assert created == {"product": True, "support": True}
    assert client.collection_exists(sources["product"].collection_name)
    assert client.collection_exists(sources["support"].collection_name)


def test_indexes_support_doc_into_support_collection():
    source = rag_sources()["support"]
    service = RagService(QdrantClient(":memory:"), KeywordEmbedder())

    count = service.index_source(source)
    hits = service.retrieve(source.collection_name, "refund for wrong item", limit=3)

    assert count > 0
    assert hits
    assert hits[0].source == "BTB_Company_Policies.md"
    assert "refund" in " ".join(hit.text for hit in hits).casefold()
