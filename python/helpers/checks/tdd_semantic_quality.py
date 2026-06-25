"""TDD Semantic Quality Gate — 2-layer detection architecture.

RCA-366: Ensures TDD tests actually implement the BDD requirements, not just
dummy assertions like expect(true).toBe(true).

Layer 1 (Fast Filter): Reuses tautological_test_detector.py to detect
    structurally useless assertions. NO duplicate regex — delegates to
    the existing validator that's already battle-tested.

Layer 2 (Semantic Verification): Uses sentence_transformers embeddings
    (python.helpers.semantic_embeddings) to compute cosine similarity
    between BDD scenario text and TDD test source code.

BDD Loading: Reuses _load_bdd_scenarios from tdd_generator_helpers.py
    which supports both docs/bdd-scenarios.json (structured) and
    docs/bdd-scenarios.md (Gherkin markdown).

Registered as check 2.11 — runs after Code agent implements TDD tests.
"""

import logging
import os
import re
from typing import Dict, List, Tuple

from python.helpers.orchestrator_gate_integration_checks import (
    CheckContext,
    register_check,
)

logger = logging.getLogger("agix.checks.tdd_semantic_quality")


# ── BDD Loading: Reuse canonical loader ──────────────────────────────────


def _read_bdd_scenarios(project_dir: str) -> Dict[str, str]:
    """Load BDD scenarios using the canonical loader and flatten for embedding.

    Delegates to _load_bdd_scenarios from tdd_generator_helpers which supports:
      1. docs/bdd-scenarios.json (structured, preferred)
      2. docs/bdd-scenarios.md (Gherkin markdown fallback)

    Returns:
        Dict mapping req_id -> flattened scenario text for embedding.
        Empty dict if no BDD scenarios found.
    """
    try:
        from python.helpers.tdd_generator_helpers import _load_bdd_scenarios
    except ImportError:
        logger.warning("[TDD SEMANTIC] Cannot import _load_bdd_scenarios. Skipping.")
        return {}

    bdd_map = _load_bdd_scenarios(project_dir)
    result = {}
    for req_id, scenarios in bdd_map.items():
        text_parts = []
        for scn in scenarios:
            text_parts.append(f"Scenario: {scn.get('scenario', '')}")
            text_parts.append(f"Given {scn.get('given', '')}")
            text_parts.append(f"When {scn.get('when', '')}")
            for then_clause in scn.get("then", []):
                text_parts.append(f"Then {then_clause}")
        result[req_id] = "\n".join(text_parts)
    return result


# ── Test Block Extraction ────────────────────────────────────────────────


def _extract_test_blocks(project_dir: str) -> List[Tuple[str, str]]:
    """Extracts all test blocks from TDD files.

    Returns a list of tuples: (filename, test_block_content)
    """
    test_blocks = []

    search_dirs = [
        os.path.join(project_dir, "docs", "tdd"),
        os.path.join(project_dir, "tests"),
        os.path.join(project_dir, "src", "__tests__"),
        os.path.join(project_dir, "app", "__tests__"),
    ]

    for d_path in search_dirs:
        if not os.path.isdir(d_path):
            continue
        for root, _, files in os.walk(d_path):
            for file in files:
                if file.endswith((".test.ts", ".test.tsx", ".spec.ts", ".py")):
                    full_path = os.path.join(root, file)
                    try:
                        with open(full_path, "r", encoding="utf-8") as f:
                            content = f.read()
                    except (IOError, OSError):
                        continue

                    # Split on test block boundaries
                    blocks = re.split(r"\b(test\(|it\(|def test_)", content)
                    if len(blocks) > 1:
                        # blocks[0] is everything before the first test
                        for i in range(1, len(blocks), 2):
                            keyword = blocks[i]
                            if i + 1 < len(blocks):
                                body = blocks[i + 1]
                            else:
                                continue  # trailing keyword with no body
                            test_content = keyword + body
                            test_blocks.append((file, test_content.strip()))

    return test_blocks


# ── Layer 1: Tautological Test Detection (reuses existing detector) ──────


def _check_tautological_tests(project_dir: str):
    """Layer 1 fast filter: detect tautological assertions.

    Delegates to detect_tautological_tests from the existing validator.
    Returns a block message string if tautologies found, or None.
    """
    try:
        from python.helpers.validators.tautological_test_detector import (
            detect_tautological_tests,
        )
    except ImportError:
        logger.debug("[TDD SEMANTIC L1] Cannot import tautological detector.")
        return None

    result = detect_tautological_tests(project_dir)
    if result is None or result["tautological_count"] == 0:
        return None

    findings = result.get("findings", [])[:5]
    details = "\n".join(
        f"  - {f['file']}:{f['line']}: {f['pattern']}" for f in findings
    )
    overflow = (
        f"\n  ... and {result['tautological_count'] - 5} more"
        if result["tautological_count"] > 5
        else ""
    )

    return (
        f"⚠️ TDD SEMANTIC QUALITY: TAUTOLOGICAL — "
        f"{result['tautological_count']} assertion(s) "
        f"test nothing:\n{details}{overflow}\n\n"
        f"Replace with behavioral assertions that call functions, render "
        f"components, or query APIs."
    )


# ── Main Gate Check ──────────────────────────────────────────────────────


@register_check(2.11, "TDD Semantic Quality", critical=True, web_only=True, gate="tdd")
def _check_tdd_semantic_quality(ctx: CheckContext):
    """2-layer gate: ensures TDD tests semantically align with BDD scenarios.

    Layer 1: Fast deterministic filter via tautological_test_detector.
    Layer 2: Semantic embedding similarity via sentence_transformers.
    """
    if not ctx.project_dir:
        return None

    bdd_scenarios = _read_bdd_scenarios(ctx.project_dir)
    if not bdd_scenarios:
        return None  # No BDD → nothing to cross-reference (other gates handle this)

    # ── Layer 1: Tautological tests ──
    tautology_result = _check_tautological_tests(ctx.project_dir)
    if tautology_result is not None:
        return ctx.block(tautology_result)

    # ── Layer 2: Semantic Similarity ──
    test_blocks = _extract_test_blocks(ctx.project_dir)
    if not test_blocks:
        return None  # No tests → handled by coverage gate

    try:
        from python.helpers.semantic_embeddings import (
            compute_embedding_sync,
            cosine_similarity,
        )

        import numpy as np  # noqa: F401 — verify numpy available
    except ImportError:
        logger.warning("[TDD SEMANTIC L2] Missing embeddings dependencies. Skipping.")
        return None

    # Compute BDD embeddings
    bdd_embeddings = []
    for req_id, text in bdd_scenarios.items():
        emb = compute_embedding_sync(text)
        if emb is not None:
            bdd_embeddings.append((req_id, text, emb))

    if not bdd_embeddings:
        return None

    SIMILARITY_THRESHOLD = 0.50  # Conservative to avoid false positives

    for filename, block_content in test_blocks:
        block_emb = compute_embedding_sync(block_content)
        if block_emb is None:
            continue  # Graceful degradation if embedding fails for a block

        best_score = -1.0
        best_req = None

        for req_id, _, bdd_emb in bdd_embeddings:
            score = cosine_similarity(block_emb, bdd_emb)
            if score > best_score:
                best_score = score
                best_req = req_id

        if best_score >= 0 and best_score < SIMILARITY_THRESHOLD:
            return ctx.block(
                f"⚠️ TDD SEMANTIC QUALITY: Semantic gap detected in {filename}.\n"
                f"Test does not align with any BDD scenario "
                f"(max similarity {best_score:.0%} to {best_req}).\n"
                f"TDD tests must actually implement the requirements "
                f"specified in docs/bdd-scenarios.md."
            )

    return None
