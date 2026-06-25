"""
Contract Assertion Runner — executes grep-style checks against project source code.

RCA-244: Each assertion in the requirements contract specifies a literal string
that MUST appear in the generated codebase. This runner scans all source files
and reports pass/fail per assertion with match locations.

No LLM calls. Uses Python string matching (not subprocess grep) for portability
and speed.

Usage:
    from python.helpers.contract_assertion_runner import run_contract_assertions

    result = run_contract_assertions(contract, "/path/to/project")
    # Returns: {"total": 5, "passed": 4, "pass_rate": 0.8, "results": [...]}
"""

import logging
import re
from typing import List, Optional

from python.helpers.source_scanner import (
    scan_project_sources,
    search_literal,
    SOURCE_EXTENSIONS,
    EXCLUDE_DIRS,
    EXCLUDE_FILES,
)

logger = logging.getLogger(__name__)

# Re-export canonical constants for backward compatibility.
# Tests and other modules import SOURCE_EXTENSIONS, EXCLUDE_DIRS,
# EXCLUDE_FILES from this module — the re-exports above ensure they
# continue to work without code changes.
__all__ = [
    "SOURCE_EXTENSIONS", "EXCLUDE_DIRS", "EXCLUDE_FILES",
    "run_contract_assertions", "_read_all_source", "_detect_stale_slug",
]


# ─── RCA-335: Known model slug patterns by provider ──────────────────
# Used to detect stale/wrong model slugs in source code.
# Maps provider prefix → regex pattern matching that provider's model ID format.
_PROVIDER_SLUG_PATTERNS = {
    "anthropic": re.compile(
        r'anthropic/claude[\w.-]+',
        re.IGNORECASE,
    ),
    "openai": re.compile(
        r'openai/(?:gpt|o[1-9])[\w.-]+',
        re.IGNORECASE,
    ),
    "google": re.compile(
        r'google/gemini[\w.-]+',
        re.IGNORECASE,
    ),
    "meta-llama": re.compile(
        r'meta-llama/llama[\w.-]+',
        re.IGNORECASE,
    ),
    "mistralai": re.compile(
        r'mistralai/mistral[\w.-]+',
        re.IGNORECASE,
    ),
    "deepseek": re.compile(
        r'deepseek/deepseek[\w.-]+',
        re.IGNORECASE,
    ),
}


def _detect_stale_slug(
    source_content: str,
    correct_slug: str,
    marketing_name: str,
) -> Optional[str]:
    """Detect stale/wrong model slugs in source code.

    RCA-335: When the user prompt says "Claude Sonnet 4" but the agent wrote
    "anthropic/claude-3.5-sonnet" from stale training data, this function
    finds the wrong slug so the failure message can guide the agent to fix it.

    Strategy:
    1. Extract the provider prefix from the correct slug (e.g., "anthropic")
    2. Search source code for ANY model slug from that provider
    3. If found and it's NOT the correct slug, return it as stale

    Args:
        source_content: Combined source code text to scan.
        correct_slug: The correct API slug from resolve_model_slug().
        marketing_name: The marketing name from the assertion (for logging).

    Returns:
        The stale slug found in source code, or None if no stale slug detected.
    """
    if not correct_slug or not source_content:
        return None

    # Extract provider prefix
    provider = correct_slug.split("/")[0] if "/" in correct_slug else None
    if not provider:
        return None

    # Get the regex pattern for this provider
    pattern = _PROVIDER_SLUG_PATTERNS.get(provider)
    if not pattern:
        return None

    content_lower = source_content.lower()
    correct_lower = correct_slug.lower()

    # Find all model slugs from this provider in the source
    for match in pattern.finditer(content_lower):
        found_slug = match.group(0)
        if found_slug != correct_lower:
            logger.info(
                f"[STALE SLUG DETECTOR] Marketing name '{marketing_name}' "
                f"should use '{correct_slug}' but source has '{found_slug}'"
            )
            return found_slug

    return None


def _read_all_source(project_dir: str) -> List[dict]:
    """Read all source files and return list of {path, content, lines}.

    Delegates to python.helpers.source_scanner.scan_project_sources().
    Kept as a thin wrapper for backward compatibility (tests import this).
    """
    return scan_project_sources(project_dir)


def run_contract_assertions(
    contract: dict,
    project_dir: str,
    confidence_threshold: float = 0.6,
    model_catalog: dict = None,
) -> dict:
    """Execute all assertions against the project source code.

    Args:
        contract: Requirements contract with "assertions" list.
        project_dir: Absolute path to project root.
        confidence_threshold: Minimum confidence score (0.0-1.0) for an
             assertion to be enforced. Assertions below this threshold are
             still included in results but marked as ``enforced: False``
             and auto-pass (RCA-260/261).
        model_catalog: Optional dict of OpenRouter model catalog for
            resolving marketing model names to API slugs. If provided,
            model_name assertions will also match against the resolved
            API slug (Phase 4, Fix A).

    Returns:
        {
            "total": int,
            "passed": int,
            "failed": int,
            "skipped": int,
            "pass_rate": float (0.0-1.0),
            "results": [
                {
                    "id": "URL-001",
                    "passed": True/False,
                    "enforced": True/False,
                    "confidence": 0.9,
                    "value": "literal string checked",
                    "matches": ["src/page.tsx:42"] or [],
                    "expected": "the value" (only on failure)
                }
            ]
        }
    """
    assertions = contract.get("assertions", [])

    if not assertions:
        return {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "pass_rate": 1.0,
            "results": [],
        }

    # Read all source files once
    source_files = _read_all_source(project_dir)

    results = []
    passed = 0
    failed = 0
    skipped = 0

    for assertion in assertions:
        needle = assertion.get("value", "")
        assertion_id = assertion.get("id", "UNKNOWN")
        confidence = assertion.get("confidence", 1.0)
        enforced = confidence >= confidence_threshold

        if not needle:
            results.append({
                "id": assertion_id,
                "passed": True,
                "enforced": enforced,
                "confidence": confidence,
                "value": needle,
                "matches": [],
            })
            passed += 1
            continue

        # Below-threshold assertions: auto-pass, mark as skipped
        if not enforced:
            results.append({
                "id": assertion_id,
                "passed": True,
                "enforced": False,
                "confidence": confidence,
                "value": needle,
                "matches": [],
                "reason": "below_confidence_threshold",
            })
            skipped += 1
            passed += 1
            continue

        # Case-insensitive search across all source files
        needle_lower = needle.lower()
        matches = []

        # Phase 4, Fix A + U-9: For model_name assertions, also try the
        # resolved API slug as an alternative search needle.
        # U-9 ROOT CAUSE FIX: Use resolve_model_slug even when catalog is
        # None/empty — it now has a static fallback map for well-known models.
        # Previously, empty catalog → no resolution → only searched for
        # "Claude Sonnet 4" literally → never found in code → false failure.
        alt_needles = [needle_lower]
        assertion_type = assertion.get("type", "")
        if assertion_type == "model_name":
            try:
                from python.helpers.model_resolver import resolve_model_slug
                slug = resolve_model_slug(needle, catalog=model_catalog or {})
                if slug and slug.lower() != needle_lower:
                    alt_needles.append(slug.lower())
            except Exception:
                pass  # Graceful degradation if resolver unavailable

        for source_file in source_files:
            content_lower = source_file["content"].lower()
            
            # Handle regex compliance patterns that start with (?i)
            if needle.startswith("(?i)"):
                try:
                    pattern = re.compile(needle[4:], re.IGNORECASE)
                    if pattern.search(source_file["content"]):
                        # Find specific line numbers
                        for line_num, line_text in source_file["lines"]:
                            if pattern.search(line_text):
                                matches.append(f"{source_file['path']}:{line_num}")
                except re.error:
                    pass
            else:
                for alt_needle in alt_needles:
                    if alt_needle in content_lower:
                        # Find specific line numbers
                        for line_num, line_text in source_file["lines"]:
                            if alt_needle in line_text.lower():
                                matches.append(f"{source_file['path']}:{line_num}")

        # ── RCA-334 SS-8: Exclude (negative) assertions ──
        # type="exclude" inverts the logic: PASS when value NOT found,
        # FAIL when value IS found. Used for hallucinated content ($500, etc.)
        if assertion_type == "exclude":
            if matches:
                # Value found but should NOT be — FAIL
                results.append({
                    "id": assertion_id,
                    "passed": False,
                    "enforced": True,
                    "confidence": confidence,
                    "value": needle,
                    "matches": matches,
                    "reason": f"Excluded value should NOT appear in codebase but was found",
                })
                failed += 1
            else:
                # Value NOT found — PASS (correct for exclude)
                results.append({
                    "id": assertion_id,
                    "passed": True,
                    "enforced": True,
                    "confidence": confidence,
                    "value": needle,
                    "matches": [],
                })
                passed += 1
            continue

        # Normal (include) assertion logic
        if matches:
            results.append({
                "id": assertion_id,
                "passed": True,
                "enforced": True,
                "confidence": confidence,
                "value": needle,
                "matches": matches,
            })
            passed += 1
        else:
            failure_result = {
                "id": assertion_id,
                "passed": False,
                "enforced": True,
                "confidence": confidence,
                "value": needle,
                "matches": [],
                "expected": needle,
            }

            # RCA-335: For model_name assertions that FAIL, scan for stale slugs.
            # This gives the gate block message actionable context:
            # "Replace 'anthropic/claude-3.5-sonnet' with 'anthropic/claude-sonnet-4'"
            if assertion_type == "model_name":
                resolved = assertion.get("resolved_slug")
                if not resolved:
                    try:
                        from python.helpers.model_resolver import resolve_model_slug
                        resolved = resolve_model_slug(needle, catalog=model_catalog or {})
                    except Exception:
                        pass
                if resolved:
                    combined_source = "\n".join(sf["content"] for sf in source_files)
                    stale = _detect_stale_slug(combined_source, resolved, needle)
                    if stale:
                        failure_result["stale_slug_found"] = stale
                        failure_result["correct_slug"] = resolved
                        failure_result["reason"] = (
                            f"Source code uses stale model slug '{stale}' — "
                            f"replace with '{resolved}'"
                        )

            results.append(failure_result)
            failed += 1

    total = len(assertions)
    enforced_count = total - skipped
    pass_rate = passed / total if total > 0 else 1.0

    logger.info(
        f"[CONTRACT RUNNER] {passed}/{total} assertions passed "
        f"({pass_rate:.0%}), {failed} failed, "
        f"{skipped} skipped (below confidence {confidence_threshold})"
    )

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "pass_rate": pass_rate,
        "results": results,
    }



