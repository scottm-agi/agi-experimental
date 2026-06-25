"""
Shared semantic embedding utilities — thin wrapper around sentence-transformers.

Extracted from delegation_topic_dedup.py (ITR-29 v2) so multiple consumers
can use the in-memory all-MiniLM-L6-v2 model (22M params, ~10-15ms per encode):

  - delegation_topic_dedup.py — delegation message deduplication
  - content.py — Layer 2 semantic content fidelity checking (System 6 Phase 4)

The model is lazy-loaded as a singleton. First call: ~1-2s. Subsequent: instant.
Per-encode: ~10-15ms on CPU for ≤512 chars. Cosine similarity: <0.001ms.

No external API calls. No added cost. Production-proven since ITR-29 v2.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("agix.semantic_embeddings")

# Try numpy import; gracefully degrade if unavailable
try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

# ── Embedding model singleton ──
_embedding_model = None
_model_load_attempted = False


def get_embedding_model():
    """Lazy-load the sentence_transformers model (already in memory from boot).

    Uses all-MiniLM-L6-v2 (22M params) — same model the memory system uses.
    First load: ~1-2s. Subsequent calls: instant (cached singleton).
    Per-encode: ~10-15ms on CPU for 256 chars.

    Returns the model instance, or None if loading fails.
    """
    global _embedding_model, _model_load_attempted
    if _embedding_model is not None:
        return _embedding_model
    if _model_load_attempted:
        return None  # Already failed once — don't retry every call

    _model_load_attempted = True
    try:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("[SEMANTIC EMBEDDINGS] Loaded model: all-MiniLM-L6-v2")
    except Exception as e:
        logger.warning(
            f"[SEMANTIC EMBEDDINGS] Failed to load model: {e}. "
            "Semantic features will be disabled (no-op)."
        )
    return _embedding_model


def compute_embedding_sync(text: str, max_chars: int = 512) -> Optional["np.ndarray"]:
    """Compute embedding synchronously.

    Truncates to first ``max_chars`` characters — the core semantics are
    in the first paragraph. Longer text adds noise, not signal.

    Args:
        text: Input text to embed.
        max_chars: Maximum characters to consider (default 512).

    Returns:
        Normalized embedding vector (numpy array), or None on failure.
    """
    if np is None:
        return None
    model = get_embedding_model()
    if model is None:
        return None
    try:
        truncated = text[:max_chars]
        embedding = model.encode(truncated, normalize_embeddings=True)
        return np.array(embedding, dtype=np.float32)
    except Exception as e:
        logger.warning(f"[SEMANTIC EMBEDDINGS] Embedding failed: {e}")
        return None


async def compute_embedding_async(text: str, max_chars: int = 512) -> Optional["np.ndarray"]:
    """Compute embedding asynchronously — runs in thread pool.

    SentenceTransformer.encode() is CPU-bound (~10-15ms) so this keeps
    the async pipeline responsive.
    """
    try:
        return await asyncio.to_thread(compute_embedding_sync, text, max_chars)
    except Exception as e:
        logger.warning(f"[SEMANTIC EMBEDDINGS] Async embedding failed: {e}")
        return None


def cosine_similarity(a: "np.ndarray", b: "np.ndarray") -> float:
    """Compute cosine similarity between two normalized vectors.

    Both vectors must be L2-normalized (which encode(normalize_embeddings=True)
    guarantees). Returns a float in [-1.0, 1.0].
    """
    if np is None:
        return 0.0
    return float(np.dot(a, b))


def reset_model_for_testing():
    """Reset the singleton model state for testing.

    WARNING: Only use in tests. Allows re-attempting model load.
    """
    global _embedding_model, _model_load_attempted
    _embedding_model = None
    _model_load_attempted = False
