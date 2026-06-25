"""
Topic-Based Delegation Dedup — ITR-29 (v2: Semantic Embeddings)

Detects when the orchestrator re-delegates semantically-identical tasks
with DIFFERENT wording. Uses embedding-based cosine similarity instead
of hardcoded keyword extraction for intelligent, universal detection.

Architecture: 2-Layer Detection (per user rules)
- Layer 1 (existing): compute_task_hash — fast, exact text dedup
- Layer 2 (this module): embedding similarity — semantic, universal dedup

v1 used hardcoded _API_KEYWORDS (claude, stripe, etc.) which caused
false positives when project domain matched keywords. v2 uses the
sentence_transformers model already loaded in the container to compute
semantic similarity between delegation messages — no hardcoded keywords.

Production failure this fixes (MSR_Smoke_1780675145):
  4 delegations of "fix Perplexity 401" with different wording,
  producing 4 different text hashes, zero loop detection.

ISSUE-11 (MSR_Ph3_1781268752): v1's "claude" keyword caused false
positive HARD BLOCK — every delegation brief mentioned "claude" in
tech stack reference, hitting hard_limit=5 and killing progress.

Performance:
  - Model: all-MiniLM-L6-v2 (22M params, 384-dim embeddings)
  - First load: ~1-2s (one-time, model already loaded at boot by memory system)
  - Per-encode: ~10-15ms on CPU for 512 chars (run in thread pool via asyncio.to_thread)
  - Cosine similarity: <0.001ms (numpy dot product on 384 floats)
  - Delegation frequency: every 30-120s → 0.01% CPU utilization
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import numpy as np
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger("agix.delegation_topic_dedup")

# ── Similarity threshold for "same topic" detection ──
# Calibrated from production delegation messages (2026-06-12):
#   Same-topic rewrites (cluster mode): 0.58 - 0.90
#     Best pair:  "Resolve auth 401 failure" vs "Handle auth issue" = 0.90
#     Worst pair: "Applying env fixes via Python" vs "Retrieving secrets" = 0.58
#   Cross-topic pairs: 0.00 - 0.45
#     Max: "Build discovery page UI" vs "Design UI mockups" = 0.45
# 0.60 catches all same-topic rewrites with 0.15 margin above cross-topic max.
# The cluster approach (match against ANY member) provides transitive chaining.
_SIMILARITY_THRESHOLD = 0.60

# ── Canonical embedding utilities (DUP-1/4 consolidation) ──
# All embedding/model/cosine logic lives in semantic_embeddings.py.
# These aliases preserve the private-name API used throughout this file.
from python.helpers.semantic_embeddings import (
    compute_embedding_sync as _compute_embedding_sync,
    compute_embedding_async as _compute_embedding_async,
    cosine_similarity as _cosine_similarity,
    get_embedding_model as _get_embedding_model,
)


# ── HTTP error code pattern (kept for error-context enrichment) ──
_ERROR_CODE_PATTERN = re.compile(r"\b(4\d{2}|5\d{2})\b")


@dataclass
class TopicFingerprint:
    """A fingerprint representing the topic of a delegation message.

    v2: Uses embedding vector for semantic similarity instead of keyword hash.
    The hash field is kept for backward compatibility with persistence.
    """
    topics: set = field(default_factory=set)
    error_codes: set = field(default_factory=set)
    files: set = field(default_factory=set)
    hash: str = ""
    embedding: Optional[np.ndarray] = field(default=None, repr=False)

    def __post_init__(self):
        # v2: Hash is computed from embedding for dedup key
        if self.embedding is not None:
            raw = self.embedding.tobytes()[:16]
            self.hash = hashlib.md5(raw).hexdigest()[:12]
        elif self.error_codes:
            # Fallback: error-code-only hash (if embedding failed)
            components = sorted(f"err:{c}" for c in self.error_codes)
            raw = "|".join(components)
            self.hash = hashlib.md5(raw.encode()).hexdigest()[:12]
        else:
            self.hash = ""

    def __repr__(self) -> str:
        has_emb = self.embedding is not None
        return (
            f"TopicFingerprint(errors={sorted(self.error_codes)}, "
            f"has_embedding={has_emb}, hash={self.hash})"
        )


def extract_topic_fingerprint(message: str) -> TopicFingerprint:
    """Extract a topic fingerprint from a delegation message (sync).

    v2: Computes embedding synchronously. Use extract_topic_fingerprint_async
    for non-blocking production use.

    Args:
        message: The delegation message text.

    Returns:
        TopicFingerprint with embedding vector and error codes.
    """
    if not message or not message.strip():
        return TopicFingerprint()

    error_codes: set = set()
    for match in _ERROR_CODE_PATTERN.finditer(message):
        error_codes.add(match.group(1))

    embedding = _compute_embedding_sync(message)

    return TopicFingerprint(
        error_codes=error_codes,
        embedding=embedding,
    )


async def extract_topic_fingerprint_async(message: str) -> TopicFingerprint:
    """Extract a topic fingerprint asynchronously (production path).

    Runs embedding computation in thread pool via asyncio.to_thread().
    """
    if not message or not message.strip():
        return TopicFingerprint()

    error_codes: set = set()
    for match in _ERROR_CODE_PATTERN.finditer(message):
        error_codes.add(match.group(1))

    embedding = await _compute_embedding_async(message)

    return TopicFingerprint(
        error_codes=error_codes,
        embedding=embedding,
    )


class TopicDedupTracker:
    """Track delegation topics using semantic similarity to detect loops.

    v2: Uses embedding cosine similarity instead of keyword fingerprint hashing.
    When two delegation messages have cosine similarity >= _SIMILARITY_THRESHOLD,
    they are considered the same topic regardless of wording.

    Provides both sync check() and async check_async() for flexibility:
    - check(): Sync — for tests and simple callers
    - check_async(): Async — for production hooks (runs embedding in thread pool)

    When the same topic appears `threshold` times for an agent, returns a
    diagnostic string. At `hard_limit`, returns a HARD BLOCK diagnostic.
    """

    # U-9: Design-phase profiles that are exempt from topic fingerprinting.
    _DESIGN_PROFILES = frozenset({"frontend"})

    def __init__(self, threshold: int = 3, hard_limit: int = 5):
        self.threshold = threshold
        self.hard_limit = hard_limit
        # agent_id → list of TopicCluster
        # Each cluster: {"centroid": np.ndarray, "embeddings": [np.ndarray, ...],
        #                "preview": str, "count": int}
        self._topics: dict[str, list] = defaultdict(list)
        # agent_id → topic_index → list of original messages (for diagnostics)
        self._messages: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
        # F-2: Track which agents have been restored to prevent duplicate
        # restores from multiple extension instances (ITR-45).
        self._restored_agents: set = set()

    def _find_similar_topic(self, agent_id: str, embedding: np.ndarray) -> Optional[int]:
        """Find an existing topic cluster where the new embedding is similar
        to ANY member embedding (max similarity >= threshold).

        This handles transitive similarity: if msg_A and msg_B are in a cluster,
        and msg_C is similar to msg_B but not msg_A, msg_C still joins the cluster.

        Returns the index of the matching topic, or None.
        """
        topics = self._topics.get(agent_id, [])
        best_sim = 0.0
        best_idx = None
        for i, cluster in enumerate(topics):
            # Check against ALL embeddings in the cluster
            for stored_emb in cluster["embeddings"]:
                sim = _cosine_similarity(embedding, stored_emb)
                if sim >= _SIMILARITY_THRESHOLD and sim > best_sim:
                    best_sim = sim
                    best_idx = i
        if best_idx is not None:
            logger.debug(
                f"[TOPIC DEDUP] {agent_id}: Matched topic #{best_idx} "
                f"(best_similarity={best_sim:.3f})"
            )
        return best_idx

    # Escape hatch: after this many blocks past hard_limit, allow through.
    # The supervisor may have corrected the underlying issue, so permanent
    # blocking causes death spirals (smoke test: 8 blocks on GitHub push).
    _ESCAPE_AFTER_HARD_BLOCKS = 3

    def _process_check(
        self, agent_id: str, message: str, fp: TopicFingerprint, profile: str,
        agent_data: Optional[dict] = None,
        task_type: str = '',
    ) -> Optional[str]:
        """Core check logic — shared by sync and async paths.

        Takes a pre-computed TopicFingerprint and processes it against the
        stored topic clusters to detect topic loops.
        """
        # Fix 3.3: Scope agent_id by task_type to prevent false dedup across
        # different delegation types (e.g., research vs implementation phases).
        # task_type is universally derived from profile + optional category.
        if task_type:
            agent_id = f"{agent_id}:{task_type}"

        # If embedding failed, fall back to no-op (don't block without evidence)
        if fp.embedding is None:
            logger.debug("[TOPIC DEDUP] No embedding available — skipping dedup check")
            return None

        # U-9: Design-phase profiles (frontend) are exempt from topic dedup
        # UNLESS the delegation contains error codes (indicates troubleshooting)
        if (profile or '').lower() in self._DESIGN_PROFILES and not fp.error_codes:
            return None

        # Find semantically similar previous delegation
        topic_idx = self._find_similar_topic(agent_id, fp.embedding)

        if topic_idx is not None:
            # Same topic — increment count FIRST, then decide whether to add
            # the embedding to the cluster. HARD_BLOCKed delegations must NOT
            # broaden the cluster (Fix 6b — prevents "gravity well" effect
            # where repeated blocks absorb increasingly diverse messages).
            cluster = self._topics[agent_id][topic_idx]
            cluster["count"] += 1
            self._messages[agent_id][topic_idx].append(message[:120])
            count = cluster["count"]

            is_hard_block = count >= self.hard_limit

            # Fix 6b: Only add embedding to cluster if NOT hard-blocked.
            # Blocked delegations should not broaden the cluster's semantic
            # footprint. After 6+ blocks, the cluster would absorb ANY
            # implementation-related text, making escape impossible.
            if not is_hard_block:
                cluster["embeddings"].append(fp.embedding)
        else:
            # New topic — create a new cluster
            topic_idx = len(self._topics[agent_id])
            self._topics[agent_id].append({
                "embeddings": [fp.embedding],
                "preview": message[:120],
                "count": 1,
            })
            self._messages[agent_id][topic_idx] = [message[:120]]
            return None  # First occurrence — no loop

        count = self._topics[agent_id][topic_idx]["count"]

        if count >= self.threshold:
            error_desc = ", ".join(sorted(fp.error_codes)) or "none"
            prev_msgs = self._messages[agent_id][topic_idx]

            # is_hard_block already computed above, but recompute for
            # readability in the diagnostic section
            is_hard_block = count >= self.hard_limit

            # ── Escape hatch: use centralized gate_check() when agent_data
            # is available, falling back to inline counter for backward compat.
            if is_hard_block:
                from python.helpers.universal_gate_budget import gate_check
                if agent_data is not None and gate_check(
                    agent_data, "topic_dedup_hard_block",
                    threshold=self._ESCAPE_AFTER_HARD_BLOCKS,
                ):
                    logger.warning(
                        f"[TOPIC DEDUP] {agent_id}: Escape hatch (gate_check) — "
                        f"topic blocked {count}x past hard_limit={self.hard_limit}, "
                        f"allowing through (ADVISORY)"
                    )
                    return None  # Allow through
                elif agent_data is None:
                    blocks_past_hard = count - self.hard_limit
                    if blocks_past_hard >= self._ESCAPE_AFTER_HARD_BLOCKS:
                        logger.warning(
                            f"[TOPIC DEDUP] {agent_id}: Escape hatch — topic blocked {count}x "
                            f"({blocks_past_hard}x past hard_limit={self.hard_limit}), "
                            f"allowing through (ADVISORY)"
                        )
                        return None  # Allow through

            if is_hard_block:
                logger.error(
                    f"[TOPIC DEDUP] {agent_id}: HARD BLOCK — Same topic delegated {count}x "
                    f"(similarity>={_SIMILARITY_THRESHOLD}, errors={error_desc})"
                )
            else:
                logger.warning(
                    f"[TOPIC DEDUP] {agent_id}: Same topic delegated {count}x "
                    f"(similarity>={_SIMILARITY_THRESHOLD}, errors={error_desc})"
                )

            block_prefix = "## 🛑 HARD_BLOCK — " if is_hard_block else "## 🔄 "

            return (
                f"{block_prefix}TOPIC LOOP DETECTED — Same topic delegated {count} times\n\n"
                f"**Similarity**: ≥{_SIMILARITY_THRESHOLD} (semantic embedding match)\n"
                f"**Error codes**: {error_desc}\n\n"
                f"You have delegated {count} tasks about the SAME topic "
                f"to subordinates. Each used different "
                f"wording but the underlying problem is the same.\n\n"
                f"### Previous delegations on this topic:\n"
                + "\n".join(f"- {i+1}. {m}..." for i, m in enumerate(prev_msgs))
                + "\n\n"
                f"### Required action:\n"
                f"1. **STOP re-delegating** — the subordinate cannot fix this\n"
                f"2. **Accept the limitation** — this may be an environmental "
                f"constraint (e.g., invalid API key, missing secret)\n"
                f"3. **Call `response`** with PARTIAL status, noting this item "
                f"as blocked\n"
                f"4. **Move on** to other tasks that don't depend on this\n"
            )

        return None

    def check(self, agent_id: str, message: str, profile: str = '',
              agent_data: Optional[dict] = None,
              task_type: str = '') -> Optional[str]:
        """Record a delegation and return diagnostic if topic loop detected (sync).

        Computes embedding synchronously (~10-15ms). For production async use,
        call check_async() instead.

        Args:
            task_type: Optional delegation type scope (e.g., profile or
                profile:category). When set, clusters are isolated per
                task_type so semantically similar messages with different
                types don't collide.
        """
        fp = extract_topic_fingerprint(message)
        return self._process_check(agent_id, message, fp, profile,
                                   agent_data=agent_data, task_type=task_type)

    async def check_async(self, agent_id: str, message: str, profile: str = '',
                          agent_data: Optional[dict] = None,
                          task_type: str = '') -> Optional[str]:
        """Record a delegation and return diagnostic if topic loop detected (async).

        Computes embedding in thread pool via asyncio.to_thread() to avoid
        blocking the event loop. Use this in production hooks.

        Args:
            task_type: Optional delegation type scope (e.g., profile or
                profile:category). When set, clusters are isolated per
                task_type so semantically similar messages with different
                types don't collide.
        """
        fp = await extract_topic_fingerprint_async(message)
        return self._process_check(agent_id, message, fp, profile,
                                   agent_data=agent_data, task_type=task_type)

    def reset(self, agent_id: str) -> None:
        """Clear tracking for an agent."""
        if agent_id in self._topics:
            del self._topics[agent_id]
        if agent_id in self._messages:
            del self._messages[agent_id]

    def save_to_agent_data(self, agent_data: dict, agent_id: str) -> None:
        """Persist topic dedup state to agent.data for restart survival.

        v2: Stores ALL embeddings per topic cluster as base64-encoded numpy arrays.
        This preserves transitive similarity — on restore, new messages can match
        ANY member of the cluster, not just the centroid.
        """
        clusters = self._topics.get(agent_id)
        if not clusters:
            return
        state = {
            "version": 2,
            "clusters": [
                {
                    "embeddings_b64": [
                        base64.b64encode(emb.tobytes()).decode()
                        for emb in cluster["embeddings"]
                    ],
                    "embedding_dim": cluster["embeddings"][0].shape[0],
                    "preview": cluster["preview"],
                    "count": cluster["count"],
                }
                for cluster in clusters
            ],
            "messages": {
                str(k): list(v) for k, v in self._messages.get(agent_id, {}).items()
            },
        }
        agent_data["_topic_dedup_state"] = state

    def restore_from_agent_data(self, agent_data: dict, agent_id: str) -> None:
        """Restore topic dedup state from agent.data after restart.

        v2: Deserializes ALL embeddings per cluster from base64.
        v1 state (keyword-based) is discarded — incompatible with v2.
        """
        state = agent_data.get("_topic_dedup_state")
        if not state or not isinstance(state, dict):
            return

        version = state.get("version", 1)
        if version < 2:
            # v1 state (keyword-based) — discard, incompatible
            logger.info(
                f"[TOPIC DEDUP] Discarding v1 state for {agent_id} "
                f"(upgrading to v2 embedding-based)"
            )
            if "_topic_dedup_state" in agent_data:
                del agent_data["_topic_dedup_state"]
            return

        clusters = state.get("clusters", [])
        # Backward compat: v2.0 used "entries" instead of "clusters"
        if not clusters:
            clusters = state.get("entries", [])
        messages = state.get("messages", {})

        if clusters:
            # F-2 (ITR-45): Clear existing state BEFORE restoring to make
            # restore idempotent. Without this, each call APPENDS clusters,
            # causing exponential growth (1→3→7→14→15,008).
            self._topics[agent_id] = []
            self._messages[agent_id] = {}
            for cluster_data in clusters:
                dim = cluster_data.get("embedding_dim", 384)
                embeddings = []

                # v2.1: clusters have "embeddings_b64" (list of all members)
                emb_list = cluster_data.get("embeddings_b64", [])
                if emb_list:
                    for emb_b64 in emb_list:
                        emb_bytes = base64.b64decode(emb_b64)
                        embedding = np.frombuffer(emb_bytes, dtype=np.float32).copy()
                        if embedding.shape[0] == dim:
                            embeddings.append(embedding)
                else:
                    # v2.0 fallback: single "embedding_b64"
                    emb_b64 = cluster_data.get("embedding_b64", "")
                    if emb_b64:
                        emb_bytes = base64.b64decode(emb_b64)
                        embedding = np.frombuffer(emb_bytes, dtype=np.float32).copy()
                        if embedding.shape[0] == dim:
                            embeddings.append(embedding)

                if embeddings:
                    self._topics[agent_id].append({
                        "embeddings": embeddings,
                        "preview": cluster_data.get("preview", ""),
                        "count": cluster_data.get("count", 1),
                    })

            for idx_str, msgs in messages.items():
                self._messages[agent_id][int(idx_str)] = list(msgs)
            logger.info(
                f"[TOPIC DEDUP] Restored {len(clusters)} topic clusters "
                f"for {agent_id} from agent.data (v2)"
            )

