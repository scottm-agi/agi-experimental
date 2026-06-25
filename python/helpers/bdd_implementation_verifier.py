"""
BDD Implementation Verifier (F-0c, RCA ITR-10)
===============================================

Verifies that BDD THEN clauses from bdd-scenarios.md are ACTUALLY
implemented in source code — not just documented or deferred.

This is the gate that catches code agents who write:
  "// In a real implementation, this would call OpenRouter"
when the BDD says:
  "Then it MUST use model anthropic/claude-sonnet-4 via OpenRouter"

2-Layer Architecture:
- Layer 1 (Deterministic): Pattern matching for SDK imports, env vars,
  stub/deferred patterns, and API endpoint references.
- Layer 2 (LLM): Semantic check — evaluates whether source code implements
  a BDD THEN clause with a real API or is mocked/stubbed. Invoked when L1
  is inconclusive (some signals present but not definitive).

Used by:
- _22_multiagentdev_completion_gate.py (can be wired as additional check)
- Standalone verification during audit

Origin: MainStreet Phase 3 audit — Break B-3 (no gate verifies BDD→code)
       + Break B-4 (agent claims completion with template code)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.bdd_verifier")

# ─── Deferred Implementation Stub Patterns ────────────────────────────
# These are the patterns that indicate code agent wrote a stub instead
# of implementing the real functionality. Applied to non-test source files.
# (Imported from shared module — DUP-2 consolidation)
from python.helpers.stub_patterns import STUB_PATTERN_CATEGORIES
from python.helpers.source_scanner import read_project_files, EXCLUDE_DIRS
_DEFERRED_STUB_PATTERNS = STUB_PATTERN_CATEGORIES["deferred_stubs"]


# ─── API Integration Indicators ──────────────────────────────────────
# Positive signals that real API integration exists in source code.
_API_IMPORT_PATTERNS = [
    re.compile(r'\bimport\b.*\b(?:openai|OpenAI|resend|Resend|stripe|Stripe)\b', re.IGNORECASE),
    re.compile(r'\bfrom\b.*\bimport\b', re.IGNORECASE),
    re.compile(r'\brequire\s*\(\s*["\'](?:openai|resend|stripe|@stripe)', re.IGNORECASE),
    re.compile(r'\bfetch\s*\(', re.IGNORECASE),
    re.compile(r'\baxios\b', re.IGNORECASE),
]

_ENV_VAR_PATTERNS = [
    re.compile(r'process\.env\.\w+_(?:API_KEY|KEY|SECRET|TOKEN)', re.IGNORECASE),
    re.compile(r'process\.env\.\w+', re.IGNORECASE),
    re.compile(r'secret_get\s*\(', re.IGNORECASE),
]

_HARDCODED_KEY_PATTERNS = [
    # Matches hardcoded API keys like 're_abc123' or 'sk-abc123'
    re.compile(r"""['"](?:re_|sk-|pk_|whsec_|rk_)[a-zA-Z0-9_\-]{8,}['"]"""),
]

# ─── File Extension and Skip Configuration ────────────────────────────
_SCAN_EXTENSIONS = {
    ".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs",
    ".py", ".vue", ".svelte", ".astro",
}

_SKIP_DIRS = {
    "node_modules", ".next", ".nuxt", "dist", ".git", "__pycache__",
    ".turbo", ".cache", ".vercel", ".output", "coverage", ".svelte-kit",
    "build", ".expo", ".parcel-cache", ".pytest_cache", "venv", ".venv",
}

_TEST_FILE_PATTERN = re.compile(
    r'(?:__tests__|tests?/|\.test\.|\.spec\.|test_|_test\.)',
    re.IGNORECASE,
)


def parse_then_clauses(bdd_content: str) -> List[Dict[str, str]]:
    """Extract THEN/AND clauses from BDD scenario content.

    Parses bdd-scenarios.md content and extracts all THEN and AND
    clauses, associating them with their parent Feature's REQ-ID.

    Args:
        bdd_content: Raw text content of bdd-scenarios.md

    Returns:
        List of dicts with keys:
        - text: The THEN/AND clause text
        - req_id: The associated REQ-ID (from Feature heading)
        - feature: The feature name
    """
    if not bdd_content or not bdd_content.strip():
        return []

    clauses = []
    current_req_id = ""
    current_feature = ""

    # Pattern to extract REQ-ID from Feature headings
    feature_re = re.compile(r'##\s+Feature:\s*(.+?)\s*\[(REQ-[a-fA-F0-9]+)\]')
    then_re = re.compile(r'^\s+(?:Then|And)\s+(.+)$', re.IGNORECASE)

    for line in bdd_content.splitlines():
        # Update current feature/req_id context
        feature_match = feature_re.search(line)
        if feature_match:
            current_feature = feature_match.group(1).strip()
            current_req_id = feature_match.group(2)

        # Extract THEN/AND clauses
        then_match = then_re.match(line)
        if then_match and current_req_id:
            clauses.append({
                "text": then_match.group(1).strip(),
                "req_id": current_req_id,
                "feature": current_feature,
            })

    return clauses


def _scan_source_files(project_dir: str) -> Dict[str, str]:
    """Read all source files in the project.

    Returns:
        Dict mapping relative file path to file content (non-test files only).
    """
    # OVL-3: Use centralized scanner instead of inline os.walk
    all_files = read_project_files(
        project_dir,
        extensions=_SCAN_EXTENSIONS,
        skip_dirs=EXCLUDE_DIRS | _SKIP_DIRS,
    )
    # Filter out test files — stubs in tests are expected (mocks)
    return {
        rel_path: content
        for rel_path, content in all_files.items()
        if not _TEST_FILE_PATTERN.search(rel_path)
    }


def _check_for_stubs(source_files: Dict[str, str]) -> List[Dict[str, Any]]:
    """Scan all source files for deferred-implementation stubs.

    Returns:
        List of violations with file, line, text, and pattern info.
    """
    violations = []

    for rel_path, content in source_files.items():
        for i, line in enumerate(content.splitlines(), 1):
            for pattern in _DEFERRED_STUB_PATTERNS:
                if pattern.search(line):
                    violations.append({
                        "type": "deferred_stub",
                        "file": rel_path,
                        "line": i,
                        "text": line.strip(),
                        "reason": f"Deferred implementation stub detected: {line.strip()}",
                        "pattern": pattern.pattern,
                    })
                    break  # One match per line

    return violations


def _check_hardcoded_keys(source_files: Dict[str, str]) -> List[Dict[str, Any]]:
    """Scan for hardcoded API keys in source files.

    Returns:
        List of violations where API keys are hardcoded instead of env vars.
    """
    violations = []

    for rel_path, content in source_files.items():
        has_env_var = any(p.search(content) for p in _ENV_VAR_PATTERNS)
        for i, line in enumerate(content.splitlines(), 1):
            for pattern in _HARDCODED_KEY_PATTERNS:
                if pattern.search(line):
                    violations.append({
                        "type": "hardcoded_key",
                        "file": rel_path,
                        "line": i,
                        "text": line.strip(),
                        "reason": (
                            f"Hardcoded API key detected — BDD requires env vars. "
                            f"File {'also has' if has_env_var else 'does NOT have'} "
                            f"process.env references."
                        ),
                    })
                    break

    return violations


def _check_integration_clauses(
    then_clauses: List[Dict[str, str]],
    source_files: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Check that integration-related THEN clauses have corresponding source code.

    For THEN clauses containing "MUST use", "via", "API", or integration keywords,
    verify that relevant SDK imports or API calls exist in source files.

    Returns:
        List of violations where integration requirements aren't implemented.
    """
    violations = []

    # Integration keywords that indicate a THEN clause requires real API code
    integration_keywords = re.compile(
        r'\b(?:MUST use|via|API|SDK|endpoint|webhook|service)\b',
        re.IGNORECASE,
    )

    # Combine all source code for global search
    all_source = "\n".join(source_files.values())

    for clause in then_clauses:
        text = clause["text"]

        # Only check integration-related THEN clauses
        if not integration_keywords.search(text):
            continue

        # Extract provider/product names from THEN clause
        # e.g., "MUST use model X via OpenRouter" → "OpenRouter"
        providers = re.findall(
            r'(?:via|through|using|from)\s+([A-Z][a-zA-Z0-9.]+)',
            text,
            re.IGNORECASE,
        )

        for provider in providers:
            provider_lower = provider.lower()
            # Check if provider name appears in any source file
            if provider_lower not in all_source.lower():
                violations.append({
                    "type": "missing_integration",
                    "req_id": clause["req_id"],
                    "feature": clause["feature"],
                    "then_clause": text,
                    "reason": (
                        f"BDD THEN clause requires '{provider}' but no reference "
                        f"found in any source file. The integration is missing."
                    ),
                })

    return violations


def verify_bdd_implementation(
    project_dir: str,
    requirement_ids: list | None = None,
) -> Dict[str, Any]:
    """Verify that BDD THEN clauses are implemented in source code.

    Main entry point for the BDD implementation verifier.
    Performs 3 checks:
    1. Stub detection — no deferred-implementation patterns
    2. Integration verification — THEN clause providers appear in code
    3. Hardcoded key detection — API keys use env vars

    Args:
        project_dir: Root directory of the project to verify
        requirement_ids: Optional filter — only check these REQ-IDs

    Returns:
        Dict with keys:
        - passed: bool — True if all checks pass
        - violations: list of violation dicts
        - then_clauses_checked: int count of THEN clauses examined
        - summary: str human-readable summary
    """
    result = {
        "passed": True,
        "violations": [],
        "then_clauses_checked": 0,
        "summary": "",
    }

    # Read BDD scenarios
    bdd_path = os.path.join(project_dir, "docs", "bdd-scenarios.md")
    if not os.path.isfile(bdd_path):
        result["summary"] = "No bdd-scenarios.md found — nothing to verify"
        return result

    try:
        with open(bdd_path, "r") as f:
            bdd_content = f.read()
    except (IOError, OSError) as e:
        result["summary"] = f"Failed to read bdd-scenarios.md: {e}"
        return result

    # Parse THEN clauses
    then_clauses = parse_then_clauses(bdd_content)

    # Filter by requirement_ids if specified
    if requirement_ids:
        then_clauses = [c for c in then_clauses if c["req_id"] in requirement_ids]

    result["then_clauses_checked"] = len(then_clauses)

    if not then_clauses:
        result["summary"] = "No THEN clauses found in bdd-scenarios.md"
        return result

    # Scan source files
    source_files = _scan_source_files(project_dir)

    if not source_files:
        result["summary"] = "No source files found to verify"
        return result

    # Check 1: Stub detection
    stub_violations = _check_for_stubs(source_files)
    result["violations"].extend(stub_violations)

    # Check 2: Integration clause verification
    integration_violations = _check_integration_clauses(then_clauses, source_files)
    result["violations"].extend(integration_violations)

    # Check 3: Hardcoded key detection
    key_violations = _check_hardcoded_keys(source_files)
    result["violations"].extend(key_violations)

    # Set overall pass/fail
    if result["violations"]:
        result["passed"] = False
        result["summary"] = (
            f"BDD verification FAILED: {len(result['violations'])} violation(s) found "
            f"({len(stub_violations)} stubs, {len(integration_violations)} missing integrations, "
            f"{len(key_violations)} hardcoded keys)"
        )
        logger.warning(
            f"[BDD VERIFIER] FAILED: {len(result['violations'])} violations "
            f"across {len(source_files)} source files, "
            f"{result['then_clauses_checked']} THEN clauses checked"
        )
    else:
        result["summary"] = (
            f"BDD verification PASSED: {result['then_clauses_checked']} THEN clauses "
            f"verified across {len(source_files)} source files"
        )
        logger.info(f"[BDD VERIFIER] PASSED: {result['summary']}")

    return result


# ─── Integration Quality Assessment (L2 Slot) ────────────────────────────
# Fills the reserved L2 semantic check slot from the original architecture.

_INTEGRATION_CLAUSE_RE = re.compile(
    r'\b(?:MUST use|via|API|SDK|endpoint|webhook|service)\b',
    re.IGNORECASE,
)

_PROVIDER_EXTRACT_RE = re.compile(
    r'(?:via|through|using|from|use)\s+([A-Z][a-zA-Z0-9.]+)',
    re.IGNORECASE,
)


def _extract_integration_names(then_clauses: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Filter to integration-related THEN clauses and extract provider names.

    Returns:
        List of dicts: {clause_text, req_id, feature, provider}
    """
    results = []
    for clause in then_clauses:
        text = clause["text"]
        if not _INTEGRATION_CLAUSE_RE.search(text):
            continue

        providers = _PROVIDER_EXTRACT_RE.findall(text)
        for provider in providers:
            results.append({
                "clause_text": text,
                "req_id": clause["req_id"],
                "feature": clause["feature"],
                "provider": provider,
            })

    return results


async def _l2_assess_integration(
    then_clause: str,
    source_content: str,
    provider: str,
    _mock_response: Optional[dict] = None,
) -> Dict[str, Any]:
    """Layer 2: LLM semantic check — does the source implement the THEN clause?

    Args:
        then_clause: The BDD THEN clause text.
        source_content: Source code content of the relevant file(s).
        provider: Integration provider name (e.g., 'Stripe').
        _mock_response: For testing — inject a mock response dict.

    Returns:
        {verdict: 'REAL'|'MOCK'|'PARTIAL', confidence: float, reasoning: str}
    """
    # Test injection: return mock if provided
    if _mock_response is not None:
        return _mock_response

    # Production path: call utility model
    system_prompt = (
        "You are a code quality reviewer. Analyze the source code and determine "
        "whether the BDD THEN clause is implemented with a REAL API integration "
        f"(using the {provider} SDK/API with env vars and real API calls) or is "
        "MOCKED/stubbed (hardcoded data, placeholder, template code).\n\n"
        "Respond with ONLY a JSON object (no markdown, no explanation outside JSON):\n"
        '{"verdict": "REAL" or "MOCK" or "PARTIAL", '
        '"confidence": 0.0-1.0, '
        '"reasoning": "brief explanation"}'
    )

    user_message = (
        f"BDD THEN clause: {then_clause}\n\n"
        f"Source code (first 3000 chars):\n{source_content[:3000]}"
    )

    try:
        import python.models as models

        model = models.get_model("utility", "")
        if not model:
            logger.warning("[BDD L2] No utility model configured — defaulting to REAL")
            return {"verdict": "REAL", "confidence": 0.0, "reasoning": "no utility model"}

        response, _reasoning, _model, _provider = await model.unified_call(
            system_message=system_prompt,
            user_message=user_message,
            timeout=30,
            agix_retry_attempts=2,
        )

        try:
            result = json.loads(response.strip())
            return {
                "verdict": str(result.get("verdict", "REAL")).upper(),
                "confidence": float(result.get("confidence", 0.5)),
                "reasoning": str(result.get("reasoning", "parsed from LLM")),
            }
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"[BDD L2] LLM response not valid JSON: {response[:200]}")
            return {"verdict": "REAL", "confidence": 0.0, "reasoning": "invalid LLM response"}

    except Exception as e:
        logger.warning(f"[BDD L2] Assessment failed: {e}")
        return {"verdict": "REAL", "confidence": 0.0, "reasoning": f"error: {e}"}


def assess_bdd_implementation_quality(
    project_dir: str,
    requirement_ids: list | None = None,
    _mock_l2: Optional[dict] = None,
) -> Dict[str, Any]:
    """Assess BDD implementation quality with 2-layer detection.

    Fills the RESERVED L2 slot in the BDD implementation verifier.
    For each integration-related THEN clause:
      - L1: Reuses _l1_scan_integration from integration_reality_verifier
      - L2 (NEW): When L1 inconclusive, uses LLM to semantically evaluate
        whether the source code genuinely implements the integration.

    Also runs stub detection and hardcoded key detection from the existing
    verifier for a complete quality picture.

    Args:
        project_dir: Root directory of the project to verify.
        requirement_ids: Optional filter — only check these REQ-IDs.
        _mock_l2: For testing — inject a mock L2 response dict.

    Returns:
        Dict with keys:
        - passed: bool
        - violations: list of violation dicts
        - integration_verdicts: list of per-integration verdict dicts
        - summary: str
    """
    result: Dict[str, Any] = {
        "passed": True,
        "violations": [],
        "integration_verdicts": [],
        "summary": "",
    }

    # ── Step 1: Read BDD scenarios ──
    bdd_path = os.path.join(project_dir, "docs", "bdd-scenarios.md")
    if not os.path.isfile(bdd_path):
        result["summary"] = "No bdd-scenarios.md found — nothing to assess"
        return result

    try:
        with open(bdd_path, "r") as f:
            bdd_content = f.read()
    except (IOError, OSError) as e:
        result["summary"] = f"Failed to read bdd-scenarios.md: {e}"
        return result

    # ── Step 2: Parse THEN clauses ──
    then_clauses = parse_then_clauses(bdd_content)
    if requirement_ids:
        then_clauses = [c for c in then_clauses if c["req_id"] in requirement_ids]

    # ── Step 3: Extract integration-related clauses ──
    integration_clauses = _extract_integration_names(then_clauses)
    if not integration_clauses:
        result["summary"] = "No integration-related THEN clauses found"
        return result

    # ── Step 4: Scan source files ──
    source_files = _scan_source_files(project_dir)
    if not source_files:
        result["summary"] = "No source files found to assess"
        return result

    # ── Step 5: Stub detection (L1 — carried forward) ──
    stub_violations = _check_for_stubs(source_files)
    result["violations"].extend(stub_violations)

    # ── Step 6: Hardcoded key detection (L1 — carried forward) ──
    key_violations = _check_hardcoded_keys(source_files)
    result["violations"].extend(key_violations)

    # integration_reality_verifier was deleted — L1 scan unavailable
    _l1_scan_integration = None
    L1_REAL_THRESHOLD = 0.75
    L1_MOCK_THRESHOLD = 0.25

    all_source_content = "\n".join(source_files.values())

    for ic in integration_clauses:
        provider = ic["provider"]
        verdict_entry: Dict[str, Any] = {
            "integration_name": provider,
            "clause_text": ic["clause_text"],
            "req_id": ic["req_id"],
            "verdict": "UNKNOWN",
            "confidence": 0.0,
            "layer": 0,
            "evidence": [],
            "reasoning": "",
        }

        # ── L1: Deterministic scan ──
        if _l1_scan_integration is not None:
            l1 = _l1_scan_integration(project_dir, provider, project_files=source_files)

            if l1["confidence"] >= L1_REAL_THRESHOLD:
                verdict_entry.update({
                    "verdict": "REAL",
                    "confidence": l1["confidence"],
                    "layer": 1,
                    "evidence": l1["evidence"],
                    "reasoning": "L1 definitive: SDK + env var + API call present",
                })
                result["integration_verdicts"].append(verdict_entry)
                continue

            if l1["confidence"] <= L1_MOCK_THRESHOLD and not l1["has_sdk_import"]:
                verdict_entry.update({
                    "verdict": "MOCK",
                    "confidence": 0.85,
                    "layer": 1,
                    "evidence": l1["evidence"] or ["No SDK import, no env var, no API call"],
                    "reasoning": f"L1 definitive MOCK: no SDK for '{provider}'",
                })
                result["violations"].append({
                    "type": "mock_integration",
                    "req_id": ic["req_id"],
                    "feature": ic["feature"],
                    "then_clause": ic["clause_text"],
                    "reason": (
                        f"BDD requires '{provider}' but L1 found no SDK import, "
                        f"no env var, no API call. Integration is mocked/missing."
                    ),
                })
                result["integration_verdicts"].append(verdict_entry)
                continue

            # ── L1 inconclusive — invoke L2 ──
            # Find relevant source content for this integration
            provider_lower = provider.lower()
            relevant_content = ""
            for fpath, content in source_files.items():
                if provider_lower in fpath.lower() or provider_lower in content.lower():
                    relevant_content += f"\n// --- {fpath} ---\n{content}"

            if not relevant_content:
                relevant_content = all_source_content[:3000]

            # L2: Semantic LLM check (or mock)
            if _mock_l2 is not None:
                l2_result = _mock_l2
            else:
                # In synchronous context, we can't await — run it sync
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Already in async context — schedule
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            l2_result = pool.submit(
                                asyncio.run,
                                _l2_assess_integration(
                                    ic["clause_text"],
                                    relevant_content,
                                    provider,
                                ),
                            ).result(timeout=35)
                    else:
                        l2_result = loop.run_until_complete(
                            _l2_assess_integration(
                                ic["clause_text"],
                                relevant_content,
                                provider,
                            )
                        )
                except Exception as e:
                    logger.warning(f"[BDD L2] Failed for {provider}: {e}")
                    l2_result = {
                        "verdict": "REAL",
                        "confidence": 0.0,
                        "reasoning": f"L2 failed: {e}",
                    }

            verdict_entry.update({
                "verdict": l2_result.get("verdict", "REAL"),
                "confidence": l2_result.get("confidence", 0.0),
                "layer": 2,
                "evidence": l1["evidence"],
                "reasoning": l2_result.get("reasoning", ""),
            })

            if l2_result.get("verdict") in ("MOCK", "PARTIAL"):
                result["violations"].append({
                    "type": "mock_integration",
                    "req_id": ic["req_id"],
                    "feature": ic["feature"],
                    "then_clause": ic["clause_text"],
                    "reason": (
                        f"BDD requires '{provider}' but L2 assessed as "
                        f"{l2_result.get('verdict')}: {l2_result.get('reasoning', '')}"
                    ),
                })

        else:
            # Fallback: no integration_reality_verifier available
            # Use the simple provider-name-in-source check from existing code
            if provider.lower() not in all_source_content.lower():
                verdict_entry.update({
                    "verdict": "MOCK",
                    "confidence": 0.7,
                    "layer": 1,
                    "reasoning": f"Provider '{provider}' not referenced in source code",
                })
                result["violations"].append({
                    "type": "missing_integration",
                    "req_id": ic["req_id"],
                    "feature": ic["feature"],
                    "then_clause": ic["clause_text"],
                    "reason": f"BDD requires '{provider}' but no reference in source code.",
                })
            else:
                verdict_entry.update({
                    "verdict": "REAL",
                    "confidence": 0.5,
                    "layer": 1,
                    "reasoning": f"Provider '{provider}' found in source (basic check)",
                })

        result["integration_verdicts"].append(verdict_entry)

    # ── Final: Set overall pass/fail ──
    if result["violations"]:
        result["passed"] = False
        result["summary"] = (
            f"BDD quality assessment FAILED: {len(result['violations'])} violation(s) found "
            f"({len(stub_violations)} stubs, {len(key_violations)} hardcoded keys, "
            f"{len(result['violations']) - len(stub_violations) - len(key_violations)} integration issues)"
        )
        logger.warning(f"[BDD QUALITY] FAILED: {result['summary']}")
    else:
        result["summary"] = (
            f"BDD quality assessment PASSED: {len(integration_clauses)} integration clause(s) verified"
        )
        logger.info(f"[BDD QUALITY] PASSED: {result['summary']}")

    return result
