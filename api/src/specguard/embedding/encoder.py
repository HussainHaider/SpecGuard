"""Dense and sparse encoders, both local and both ONNX.

fastembed runs its models through onnxruntime, so nothing here pulls in torch and
nothing leaves the process. That keeps the image small, the tests offline and the
embedding cost at zero — for 734 clauses of one legal corpus a hosted embedding API
would buy nothing.

On the dense model: the stack names ``intfloat/multilingual-e5-small``, which fastembed
0.8 does not ship — the only e5 it offers is ``multilingual-e5-large`` at 1024
dimensions and 2.24 GB, ten times the footprint of the alternatives for a corpus this
size. Both are supported here and the choice is config, not code. The default is the
small multilingual MiniLM; set ``DENSE_EMBEDDING_MODEL`` to the e5 to switch, and the
e5 prefix discipline turns itself on when you do.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache

from fastembed import SparseTextEmbedding, TextEmbedding


@dataclass(frozen=True)
class DenseModelSpec:
    """A supported dense model and whether it wants e5's asymmetric prefixes."""

    name: str
    dimensions: int
    uses_e5_prefixes: bool


#: e5 was trained with asymmetric prefixes and quietly loses accuracy without them.
#: Other models were not, and prefixing them just adds a meaningless leading token — so
#: the prefix is a property of the model, never something a caller decides.
DENSE_MODELS: dict[str, DenseModelSpec] = {
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": DenseModelSpec(
        name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        dimensions=384,
        uses_e5_prefixes=False,
    ),
    "intfloat/multilingual-e5-large": DenseModelSpec(
        name="intfloat/multilingual-e5-large",
        dimensions=1024,
        uses_e5_prefixes=True,
    ),
}

DENSE_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
SPARSE_MODEL = "Qdrant/bm25"


@dataclass(frozen=True)
class SparseVector:
    """A bm25 vector in the index/value form Qdrant expects."""

    indices: list[int]
    values: list[float]


@lru_cache(maxsize=2)
def _dense_model(name: str) -> TextEmbedding:
    return TextEmbedding(model_name=name)


@lru_cache(maxsize=1)
def _sparse_model(name: str) -> SparseTextEmbedding:
    return SparseTextEmbedding(model_name=name)


class UnsupportedModelError(ValueError):
    """Raised for a dense model whose dimensions and prefix rules we do not know."""


class Encoder:
    """Encodes clauses for indexing and queries for search."""

    def __init__(self, dense_model: str = DENSE_MODEL, sparse_model: str = SPARSE_MODEL) -> None:
        if dense_model not in DENSE_MODELS:
            supported = ", ".join(sorted(DENSE_MODELS))
            raise UnsupportedModelError(f"{dense_model!r} is not one of: {supported}")
        self.spec = DENSE_MODELS[dense_model]
        self._sparse_name = sparse_model

    @property
    def dimensions(self) -> int:
        """Vector size of the configured dense model, for collection creation."""
        return self.spec.dimensions

    def _passage(self, text: str) -> str:
        return f"passage: {text}" if self.spec.uses_e5_prefixes else text

    def _query(self, text: str) -> str:
        return f"query: {text}" if self.spec.uses_e5_prefixes else text

    def encode_passages(self, texts: Sequence[str]) -> list[list[float]]:
        """Dense-encode clause text for storage."""
        prefixed = [self._passage(text) for text in texts]
        model = _dense_model(self.spec.name)
        return [vector.tolist() for vector in model.embed(prefixed)]

    def encode_query(self, text: str) -> list[float]:
        """Dense-encode a search string."""
        model = _dense_model(self.spec.name)
        vector = next(iter(model.embed([self._query(text)])))
        values: list[float] = vector.tolist()
        return values

    def encode_sparse(self, texts: Sequence[str]) -> list[SparseVector]:
        """bm25-encode clause text. No prefix: bm25 is lexical and a prefix is a term."""
        return list(self._to_sparse(_sparse_model(self._sparse_name).embed(list(texts))))

    def encode_sparse_query(self, text: str) -> SparseVector:
        """bm25-encode a search string, using bm25's query-side weighting."""
        return next(iter(self._to_sparse(_sparse_model(self._sparse_name).query_embed(text))))

    @staticmethod
    def _to_sparse(vectors: Iterable[object]) -> Iterable[SparseVector]:
        for vector in vectors:
            indices: list[int] = vector.indices.tolist()  # type: ignore[attr-defined]
            values: list[float] = vector.values.tolist()  # type: ignore[attr-defined]
            yield SparseVector(indices=indices, values=values)
