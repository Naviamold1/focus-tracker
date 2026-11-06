"""Local tier (spec §4.2.5): MiniLM ONNX embeddings scored against the goal and distraction anchors."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from focus_tracker.schema import Category

log = logging.getLogger(__name__)

ANCHORS = (
    "watching entertainment videos",
    "scrolling social media",
    "playing games",
    "online shopping",
    "celebrity gossip",
)
MAX_LOCAL_CONFIDENCE = (
    0.79  # never Jev-grade; the engine therefore always doubles dwell on local verdicts
)


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:
        """Rows are L2-normalized, so a dot product is the cosine."""
        ...


class OnnxEmbedder:
    """all-MiniLM-L6-v2 via onnxruntime + tokenizers: mean pooling, L2 normalize."""

    def __init__(self, model_dir: Path) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self._tok.enable_truncation(max_length=128)
        self._tok.enable_padding()
        self._session = ort.InferenceSession(
            str(model_dir / "model.onnx"), providers=["CPUExecutionProvider"]
        )
        self._inputs = {i.name for i in self._session.get_inputs()}

    def embed(self, texts: list[str]) -> np.ndarray:
        enc = self._tok.encode_batch(texts)
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feeds = {
            "input_ids": ids,
            "attention_mask": mask,
            "token_type_ids": np.zeros_like(ids),
        }
        hidden = self._session.run(
            None, {k: v for k, v in feeds.items() if k in self._inputs}
        )[0]
        m = mask[..., None].astype(np.float32)
        pooled = (hidden * m).sum(1) / np.maximum(m.sum(1), 1e-9)
        return pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9)


def load_embedder(model_dir: Path) -> Embedder | None:
    """None (logged once) when model files are missing or fail to load."""
    if (
        not (model_dir / "model.onnx").exists()
        or not (model_dir / "tokenizer.json").exists()
    ):
        log.warning(
            "local model not found in %s; run scripts/fetch_model.py. Local tier returns neutral.",
            model_dir,
        )
        return None
    try:
        return OnnxEmbedder(model_dir)
    except Exception:
        log.exception("failed to load local model; local tier returns neutral")
        return None


@dataclass(frozen=True, slots=True)
class LocalVerdict:
    category: Category
    confidence: float


class LocalScorer:
    """g = cos(surface, goal), d = max cos(surface, anchors).

    g >= g_hi -> productive; d - g >= margin -> distraction; else neutral.
    Confidence grows with the distance from the nearest decision boundary.
    """

    def __init__(self, embedder: Embedder | None, g_hi: float, margin: float) -> None:
        self._embedder = embedder
        self.g_hi = g_hi
        self.margin = margin
        self._cache: dict[str, np.ndarray] = {}
        self._anchors: np.ndarray | None = None

    def _vec(self, embedder: Embedder, text: str) -> np.ndarray:
        if text not in self._cache:
            if len(self._cache) > 2048:
                self._cache.clear()
            self._cache[text] = embedder.embed([text])[0]
        return self._cache[text]

    def similarities(self, goal: str, text: str) -> tuple[float, float] | None:
        """(g, d), or None when no model is loaded."""
        if self._embedder is None:
            return None
        if self._anchors is None:
            self._anchors = self._embedder.embed(list(ANCHORS))
        v = self._vec(self._embedder, text)
        return float(v @ self._vec(self._embedder, goal)), float(
            (self._anchors @ v).max()
        )

    def score(self, goal: str, text: str) -> LocalVerdict:
        sims = self.similarities(goal, text)
        return LocalVerdict("neutral", 0.1) if sims is None else self.verdict(*sims)

    def verdict(self, g: float, d: float) -> LocalVerdict:
        if g >= self.g_hi:
            category, gap = "productive", g - self.g_hi
        elif d - g >= self.margin:
            category, gap = "distraction", (d - g) - self.margin
        else:
            category, gap = "neutral", min(self.g_hi - g, self.margin - (d - g))
        return LocalVerdict(
            category, float(np.clip(0.4 + gap * 4, 0.3, MAX_LOCAL_CONFIDENCE))
        )
