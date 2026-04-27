from __future__ import annotations

import argparse

from cafe.services.rag_service import index_all_sources


def main() -> None:
    parser = argparse.ArgumentParser(description="Index cafe RAG docs into Qdrant.")
    parser.add_argument(
        "--no-recreate",
        action="store_true",
        help="Append/update points without recreating collections first.",
    )
    args = parser.parse_args()

    try:
        counts = index_all_sources(recreate=not args.no_recreate)
    except Exception as exc:
        raise SystemExit(f"Could not index RAG documents. Is Qdrant running? {exc}") from exc

    for name, count in counts.items():
        print(f"{name}: indexed {count} chunks")


if __name__ == "__main__":
    main()
