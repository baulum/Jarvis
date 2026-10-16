"""Local text embeddings for semantic memory search, in-process (no Ollama or other server).

Qwen3-Embedding-0.6B as quantised ONNX via fastembed: multilingual (good German), 1024 dimensions
(matches the pgvector column), ~1.1 GB, downloaded once to ``<data_dir>/models/fastembed``.
Inference runs in a worker thread so the event loop stays responsive.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
DEFAULT_MODEL = "Qwen/Qwen3-Embedding-0.6B-Q"


class LocalEmbedder:
    def __init__(self, cache_dir: Path, model: str = DEFAULT_MODEL, dimensions: int = 1024) -> None:
        self.cache_dir = cache_dir
        self.model_name = model
        self.dimensions = dimensions
        self._model: Any = None
        self._lock = asyncio.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self) -> Any:
        from fastembed import TextEmbedding  # heavy import, deferred

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return TextEmbedding(self.model_name, cache_dir=str(self.cache_dir))

    async def _ensure(self) -> Any:
        if self._model is None:
            async with self._lock:
                if self._model is None:
                    logger.info("Loading embedding model %s", self.model_name)
                    self._model = await asyncio.to_thread(self._load)
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        model = await self._ensure()

        def run() -> list[list[float]]:
            return [[float(x) for x in v] for v in model.embed([t[:8000] for t in texts])]

        vectors = await asyncio.to_thread(run)
        if vectors and len(vectors[0]) != self.dimensions:
            raise RuntimeError(f"Embedding-Modell liefert {len(vectors[0])} statt {self.dimensions} Dimensionen")
        return vectors
