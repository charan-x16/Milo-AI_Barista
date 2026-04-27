from __future__ import annotations

import argparse

from cafe.services.rag_service import create_qdrant_collections, rag_sources


def main() -> None:
    parser = argparse.ArgumentParser(description="Create Qdrant collections for cafe RAG.")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete and recreate existing collections.",
    )
    args = parser.parse_args()

    sources = rag_sources()
    try:
        created = create_qdrant_collections(recreate=args.recreate)
    except Exception as exc:
        raise SystemExit(f"Could not create Qdrant collections. Is Qdrant running? {exc}") from exc

    for name, was_created in created.items():
        collection = sources[name].collection_name
        status = "created" if was_created else "already exists"
        print(f"{name}: {collection} {status}")


if __name__ == "__main__":
    main()
