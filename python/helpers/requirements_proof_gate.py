"""
Requirements Mark-Complete Proof Gating

F-2C: Standalone proof-validation logic for mark_complete action.
Separated from requirements.py to avoid the heavy Tool/Agent import chain
and enable unit testing without the full agent stack.

This module provides:
  - _validate_proof_files: Validate proof files exist and contain keywords
  - handle_mark_complete_with_proof: Proof-gated mark_complete logic

ISSUE-2: _auto_discover_proof_files removed. File existence is NOT proof
of correct implementation — the test IS the proof. Requirements must be
promoted from delegation_returned → completed via test results only.
"""
from __future__ import annotations

import logging
import os
from typing import List

logger = logging.getLogger("agix.requirements_proof")


def _validate_proof_files(
    proof_files: list,
    proof_grep: str = "",
) -> str:
    """Validate proof files exist and optionally contain a keyword.

    F-2C: Proof-gating helper. Returns empty string if valid,
    or an error message if validation fails.

    RCA-357: When proof_grep fails, include identifiers from the file
    so the agent can self-correct on the next attempt instead of
    guessing blindly (which causes 10+ iteration loops).

    Args:
        proof_files: List of file paths to verify.
        proof_grep: Optional keyword that must appear in the proof files.

    Returns:
        Empty string if all proof files pass, error message otherwise.
    """
    for pf in proof_files:
        if not os.path.exists(pf):
            return f"❌ Proof file does not exist: {pf}"

    if proof_grep:
        for pf in proof_files:
            try:
                with open(pf, "r", errors="replace") as f:
                    content = f.read()
                if proof_grep not in content:
                    # RCA-357: Extract identifiers from the file to help
                    # the agent pick a valid grep string on next attempt
                    hints = _extract_hint_keywords(content)
                    hint_str = ""
                    if hints:
                        hint_str = (
                            f" Hint: file contains: {', '.join(hints[:8])}"
                        )
                    return (
                        f"❌ Proof file does not contain '{proof_grep}': {pf}."
                        f"{hint_str}"
                    )
            except (IOError, OSError) as e:
                return f"❌ Cannot read proof file {pf}: {e}"

    return ""


def _extract_hint_keywords(content: str, max_keywords: int = 8) -> List[str]:
    """Extract meaningful identifiers from file content for proof_grep hints.

    RCA-357: Helps agents self-correct when proof_grep fails by showing
    what the file actually contains.

    Args:
        content: File content string.
        max_keywords: Maximum number of keywords to return.

    Returns:
        List of meaningful identifiers found in the file.
    """
    import re

    keywords = set()

    # Extract function/class names (JS/TS/Python patterns)
    for match in re.finditer(
        r"(?:export\s+)?(?:async\s+)?(?:function|class|def|const|let|var)\s+(\w{4,})",
        content,
    ):
        keywords.add(match.group(1))

    # Extract import module names
    for match in re.finditer(r"(?:from|import)\s+['\"]([^'\"]+)['\"]", content):
        mod = match.group(1).split("/")[-1]  # last segment
        if len(mod) >= 3:
            keywords.add(mod)

    # Extract exported identifiers
    for match in re.finditer(r"export\s+(?:default\s+)?(\w{4,})", content):
        keywords.add(match.group(1))

    # Filter out common noise
    noise = {
        "from", "import", "export", "const", "async", "function",
        "return", "string", "number", "boolean", "null", "void",
        "true", "false", "undefined", "interface", "type",
    }
    keywords -= noise

    return sorted(keywords)[:max_keywords]


def handle_mark_complete_with_proof(
    agent_data: dict,
    args: dict,
    mark_fn,
    project_dir: str = None,
) -> str:
    """Mark-complete with proof-gating (F-2C).

    ISSUE-2: Auto-discovery removed. The test IS the proof. Explicit
    proof_files must be provided by the calling agent.

    Args:
        agent_data: The agent.data dict
        args: Dict with requirement_id/ids, proof_files, proof_grep
        mark_fn: The mark_requirement_complete callable
        project_dir: Optional project dir for disk persistence

    Returns:
        Result message string.
    """
    # F-2C: Proof-gating — require proof_files for mark_complete
    proof_files = args.get("proof_files", [])
    proof_grep = args.get("proof_grep", "")

    # Determine which req_id(s) are being completed
    req_ids = args.get("requirement_ids", [])
    req_id = args.get("requirement_id", "")

    # At least one req_id must be specified
    if not (req_ids or req_id):
        return (
            "⚠️ Missing 'requirement_id' or 'requirement_ids'. "
            "Pass requirement_id: 'REQ-XXX' or requirement_ids: ['REQ-001', 'REQ-002']"
        )

    # ISSUE-2: No auto-discovery. Explicit proof_files required.
    if not proof_files:
        return (
            "❌ Missing 'proof_files'. mark_complete requires proof of implementation. "
            "Pass proof_files: ['/path/to/impl.ts'] and optionally proof_grep: 'functionName'. "
            "Tip: If you are the orchestrator, the code agent should call mark_complete "
            "with proof_files after writing the implementation."
        )

    # F-2C: Validate proof files exist and contain expected content
    if isinstance(proof_files, list) and proof_files:
        validation_error = _validate_proof_files(proof_files, proof_grep)
        if validation_error:
            return validation_error

    # Batch mode takes precedence
    if isinstance(req_ids, list) and req_ids:
        completed = []
        not_found = []
        for rid in req_ids:
            if mark_fn(agent_data, rid, project_dir=project_dir):
                completed.append(rid)
            else:
                not_found.append(rid)

        parts = []
        if completed:
            parts.append(f"✅ {len(completed)} marked as completed: {', '.join(completed)}")
        if not_found:
            parts.append(f"⚠️ {len(not_found)} not found: {', '.join(not_found)}")
        return " | ".join(parts) if parts else "⚠️ No requirement IDs provided."

    # Single mode (backward compatible)
    if not req_id:
        return (
            "⚠️ Missing 'requirement_id' or 'requirement_ids'. "
            "Pass requirement_id: 'REQ-XXX' or requirement_ids: ['REQ-001', 'REQ-002']"
        )

    success = mark_fn(agent_data, req_id, project_dir=project_dir)
    if success:
        return f"✅ {req_id} marked as completed."
    else:
        return f"⚠️ {req_id} not found in the requirements ledger."


# ── F-11 (RCA-470): Independent Evidence Verification ────────────────────
# Agent self-report (proof_files + proof_grep) is necessary but not sufficient.
# This function provides INDEPENDENT verification by grepping the actual
# source code for the requirement's key literal.

# File extensions to search for evidence
_EVIDENCE_EXTENSIONS: frozenset = frozenset({
    ".tsx", ".ts", ".js", ".jsx", ".mjs", ".cjs",
    ".css", ".scss", ".html", ".json", ".py",
    ".vue", ".svelte", ".astro",
})

# Directories to skip during evidence search
_EVIDENCE_SKIP_DIRS: frozenset = frozenset({
    "node_modules", ".git", ".next", ".nuxt", "dist", "build",
    "__pycache__", ".venv", "venv", ".turbo", ".vercel",
    ".agix.proj", "docs",  # Skip planning docs — evidence must be in code
})


def verify_requirement_evidence(
    project_dir: str,
    key_literal: str,
) -> dict:
    """Verify a requirement's key literal exists in the project source code.

    F-11 (RCA-470): Independent evidence verification that does NOT rely on
    agent self-report. Greps ALL source files in the project for the
    requirement's key literal (e.g., "$199/month", "MainStreet", etc.).

    Args:
        project_dir: Absolute path to the project root directory.
        key_literal: The literal string to search for in source files.

    Returns:
        Dict with:
            - found: bool — whether the literal was found in any source file
            - evidence_files: list — file paths where the literal was found
            - searched_files_count: int — how many files were searched
    """
    result = {
        "found": False,
        "evidence_files": [],
        "searched_files_count": 0,
    }

    if not project_dir or not os.path.isdir(project_dir) or not key_literal:
        return result

    evidence_files = []
    searched_count = 0

    for root, dirs, files in os.walk(project_dir):
        # Skip irrelevant directories
        dirs[:] = [d for d in dirs if d not in _EVIDENCE_SKIP_DIRS]

        for filename in files:
            # Check extension
            _, ext = os.path.splitext(filename)
            if ext.lower() not in _EVIDENCE_EXTENSIONS:
                continue

            filepath = os.path.join(root, filename)
            searched_count += 1

            try:
                with open(filepath, "r", errors="replace") as f:
                    content = f.read()
                if key_literal in content:
                    evidence_files.append(filepath)
            except (IOError, OSError):
                continue

    result["found"] = len(evidence_files) > 0
    result["evidence_files"] = evidence_files
    result["searched_files_count"] = searched_count

    if evidence_files:
        logger.info(
            f"[F-11 EVIDENCE] Found '{key_literal}' in {len(evidence_files)} "
            f"files (searched {searched_count})"
        )
    else:
        logger.warning(
            f"[F-11 EVIDENCE] '{key_literal}' NOT found in any of "
            f"{searched_count} source files"
        )

    return result

