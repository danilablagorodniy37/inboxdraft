"""Embedding providers. Only PII-scrubbed text is ever embedded (see pipeline.py)."""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from typing import Protocol

from inboxdraft.config import Settings


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "my",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "which",
        "who",
        "will",
        "with",
        "you",
        "your",
        "please",
        "thanks",
        "thank",
        "hello",
        "hi",
        "do",
        "does",
        "did",
        "can",
        "could",
        "would",
        "should",
        "how",
        "much",
        "many",
        "me",
        "us",
        "am",
    ]
)


class HashingEmbedder:
    """Deterministic bag-of-words hashing. For tests and CI only: no download, no key.

    Retrieval quality is keyword overlap (unigrams and bigrams, stopwords dropped, sublinear
    term frequency), which is enough to exercise the pipeline end to end.
    """

    name = "hashing-256"
    dim = 256
    _token_re = re.compile(r"[a-z0-9£]+")

    def _vector(self, text: str) -> list[float]:
        tokens = [t for t in self._token_re.findall(text.lower()) if t not in _STOPWORDS]
        counts: dict[str, int] = {}
        for gram in tokens + [f"{a}_{b}" for a, b in itertools.pairwise(tokens)]:
            counts[gram] = counts.get(gram, 0) + 1
        vec = [0.0] * self.dim
        for gram, count in counts.items():
            digest = hashlib.blake2b(gram.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[index] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class FastEmbedEmbedder:
    """Local ONNX embeddings via fastembed. Default: BAAI/bge-small-en-v1.5 (384 dims)."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        from fastembed import TextEmbedding

        self.name = model_name
        self._model = TextEmbedding(model_name=model_name)
        self.dim = len(next(iter(self._model.embed(["probe"]))))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vec.tolist() for vec in self._model.embed(texts, batch_size=32)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.query_embed(text))).tolist()


class OpenAIEmbedder:
    def __init__(self, model_name: str, api_key: str | None) -> None:
        from openai import OpenAI

        self.name = model_name
        self._client = OpenAI(api_key=api_key)
        self._model = model_name
        self.dim = len(self.embed_query("probe"))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), 64):
            batch = texts[start : start + 64]
            response = self._client.embeddings.create(model=self._model, input=batch)
            out.extend(item.embedding for item in response.data)
        return out

    def embed_query(self, text: str) -> list[float]:
        response = self._client.embeddings.create(model=self._model, input=[text])
        return response.data[0].embedding


def get_embedder(settings: Settings) -> Embedder:
    provider = settings.embeddings_provider
    if provider == "hashing":
        return HashingEmbedder()
    if provider == "fastembed":
        return FastEmbedEmbedder(settings.embedding_model)
    if provider == "openai":
        model = settings.embedding_model
        if model.startswith("BAAI/"):
            model = "text-embedding-3-small"
        return OpenAIEmbedder(model, settings.openai_api_key)
    raise ValueError(f"unknown EMBEDDINGS_PROVIDER: {provider}")
