"""Configuration for local or server-backed Qdrant semantic search."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class SemanticConfig:
    collection_name: str = "cad_semantic_chunks"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dimensions: int = 512
    qdrant_path: Path = PROJECT_ROOT / ".data" / "qdrant"
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_grpc_port: int = 6334
    qdrant_prefer_grpc: bool = True

    @classmethod
    def from_env(cls) -> "SemanticConfig":
        configured_path = Path(os.getenv("QDRANT_PATH", ".data/qdrant"))
        if not configured_path.is_absolute():
            configured_path = PROJECT_ROOT / configured_path
        return cls(
            collection_name=os.getenv("QDRANT_COLLECTION", "cad_semantic_chunks"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"),
            embedding_dimensions=int(os.getenv("EMBEDDING_DIMENSIONS", "512")),
            qdrant_path=configured_path,
            qdrant_url=os.getenv("QDRANT_URL") or None,
            qdrant_api_key=os.getenv("QDRANT_API_KEY") or None,
            qdrant_grpc_port=int(os.getenv("QDRANT_GRPC_PORT", "6334")),
            qdrant_prefer_grpc=os.getenv("QDRANT_PREFER_GRPC", "true").lower()
            in {"1", "true", "yes", "on"},
        )
