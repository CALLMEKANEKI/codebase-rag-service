from __future__ import annotations

from typing import Literal

Device = Literal["auto", "cpu", "cuda"]


class SentenceTransformerEmbedder:
    """FP32 SentenceTransformer wrapper with normalized output vectors."""

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        device: Device = "auto",
    ) -> None:
        try:
            import torch
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "Install requirements-ml.txt to use SentenceTransformerEmbedder"
            ) from exc

        if device == "auto":
            selected_device = "cuda" if torch.cuda.is_available() else "cpu"
        elif device == "cuda":
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA was requested but is not available")
            selected_device = "cuda"
        else:
            selected_device = "cpu"

        self._model_name = model_name
        self._device = selected_device
        self._model = SentenceTransformer(
            model_name,
            device=selected_device,
            trust_remote_code=False,
        )
        dimension = self._model.get_sentence_embedding_dimension()
        if dimension is None:
            raise RuntimeError(
                f"Model does not expose an embedding dimension: {model_name}"
            )
        self._dim = int(dimension)

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def device(self) -> str:
        return self._device

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.astype("float32").tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
