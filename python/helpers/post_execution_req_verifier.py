"""
Post-Execution Requirement Verifier
====================================

Deterministic, regex-based verification of requirements after a subordinate
returns. Scans the project directory to validate whether each assigned
requirement was actually implemented in code.

This is NOT an LLM-based check. It uses:
- URL grep for "url" requirements
- Import/package grep for "integration" requirements
- File existence for "page" requirements
- Keyword grep for "feature", "model", "config", and other requirements

Remediation tasks use n.n.n sequencing:
    delegation-3 → remediation 3.1, 3.2, 3.3
    delegation-5 → remediation 5.1, 5.2

Each task carries an MD5 short hash for dedup tracking.

Architecture:
    verify_requirement(dir, req)        → single requirement check
    verify_requirements_batch(dir, reqs) → batch check with report
    build_remediation_task(...)          → structured task with n.n.n ID
    add_remediation_to_ledger(data, tasks) → persist to ledger with dedup
    format_remediation_manifest(tasks)  → human-readable injection text
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from python.helpers.project_scan_constants import DEFAULT_PROJECT_SKIP_DIRS
from python.helpers.source_scanner import list_project_files

logger = logging.getLogger("agix.post_execution_req_verifier")

# Directories to skip when scanning project files
# DUP-3: Now sourced from the canonical shared module.
_SKIP_DIRS = DEFAULT_PROJECT_SKIP_DIRS

# File extensions to search in
_SEARCH_EXTENSIONS = {
    ".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs",
    ".css", ".scss", ".json", ".md", ".py",
    ".env", ".yaml", ".yml", ".toml", ".html",
}

# Source-only extensions for integration verification
# Integration verification requires import/require in SOURCE files,
# not just references in config files (.env, .json, .yaml)
_SOURCE_ONLY_EXTENSIONS = {
    ".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs", ".py",
}

# Config/non-source extensions — references here are SECONDARY signal only
_CONFIG_EXTENSIONS = {
    ".env", ".json", ".yaml", ".yml", ".toml", ".md", ".html",
    ".css", ".scss",
}

# URL extraction regex
_URL_RE = re.compile(r'https?://[^\s"\'\)>]+')

# Page route extraction: "/about" → look for matching route files
_PAGE_ROUTE_RE = re.compile(r'/(\w[\w\-/]*)')


# ──────────────────────────────────────────────────────────────────────
# File scanning helpers
# ──────────────────────────────────────────────────────────────────────


def _scan_project_files(project_dir: str, max_files: int = 300) -> List[str]:
    """Get list of searchable source files in project.

    OVL-3: Now delegates to source_scanner.list_project_files().
    """
    return list_project_files(
        project_dir,
        extensions=_SEARCH_EXTENSIONS,
        max_files=max_files,
    )


def _grep_files(files: List[str], pattern: str, max_matches: int = 5) -> List[str]:
    """Search for a pattern in a list of files. Returns list of matching file paths."""
    matches = []
    pattern_lower = pattern.lower()
    for fpath in files:
        try:
            with open(fpath, "r", errors="ignore") as f:
                content = f.read()
            if pattern_lower in content.lower():
                matches.append(fpath)
                if len(matches) >= max_matches:
                    break
        except (IOError, OSError):
            continue
    return matches


def _content_hash(text: str) -> str:
    """Generate an MD5 short hash (8 chars) for dedup tracking."""
    from python.helpers.hashing import content_hash_short
    normalized = text.strip().lower()
    return content_hash_short(normalized, length=8)


# ──────────────────────────────────────────────────────────────────────
# Circuit breaker: per-REQ verification attempt tracking
# ──────────────────────────────────────────────────────────────────────


_MAX_VERIFICATION_ATTEMPTS = 2


def get_verification_count(agent_data: dict, req_id: str) -> int:
    """Get the number of verification attempts for a specific REQ."""
    counts = agent_data.get("_verification_counts", {})
    return counts.get(req_id, 0)


def increment_verification_count(agent_data: dict, req_id: str) -> None:
    """Increment the verification attempt counter for a REQ."""
    if "_verification_counts" not in agent_data:
        agent_data["_verification_counts"] = {}
    agent_data["_verification_counts"][req_id] = (
        agent_data["_verification_counts"].get(req_id, 0) + 1
    )


def should_verify_req(agent_data: dict, req_id: str) -> bool:
    """Check if a REQ should be verified (circuit breaker).

    Returns False after _MAX_VERIFICATION_ATTEMPTS to prevent loops.
    """
    return get_verification_count(agent_data, req_id) < _MAX_VERIFICATION_ATTEMPTS


# ──────────────────────────────────────────────────────────────────────
# Comment stripping for accurate content matching
# ──────────────────────────────────────────────────────────────────────


def _strip_comments(content: str) -> str:
    """Strip single-line comments from source code.

    Removes lines that start with //, #, or are inside /* */ blocks.
    Also removes HTML comments <!-- -->.
    This prevents false positives from TODO/FIXME comments.
    """
    lines = content.split("\n")
    stripped = []
    in_block = False
    for line in lines:
        trimmed = line.strip()
        # Block comment start
        if "/*" in trimmed and "*/" not in trimmed:
            in_block = True
            continue
        if in_block:
            if "*/" in trimmed:
                in_block = False
            continue
        # Single-line comments
        if trimmed.startswith("//") or trimmed.startswith("#"):
            continue
        # Inline /* */ on same line
        if trimmed.startswith("/*") and "*/" in trimmed:
            continue
        # HTML comments
        if trimmed.startswith("<!--") and "-->" in trimmed:
            continue
        stripped.append(line)
    return "\n".join(stripped)


# ──────────────────────────────────────────────────────────────────────
# Stub detection: scans source files for incomplete implementations
# ──────────────────────────────────────────────────────────────────────


# Patterns that indicate a stub / incomplete implementation
# (Imported from shared module — DUP-2 consolidation)
from python.helpers.stub_patterns import UNIVERSAL_STUB_PATTERNS as _STUB_PATTERNS


# ── OVL-2: Endpoint-specific patterns (merged from stub_endpoint_detector) ──

# Patterns that indicate an empty/null API response
_EMPTY_RESPONSE_PATTERNS = [
    re.compile(r"Response\.json\(\s*null\s*\)"),
    re.compile(r"Response\.json\(\s*\{\s*\}\s*\)"),
    re.compile(r"NextResponse\.json\(\s*null\s*\)"),
    re.compile(r"NextResponse\.json\(\s*\{\s*\}\s*\)"),
    re.compile(r"return\s+null\s*;?\s*$", re.MULTILINE),
    re.compile(r"return\s+\{\s*\}\s*;?\s*$", re.MULTILINE),
]

# Patterns that indicate REAL logic (exemptions for endpoint stubs)
_REAL_LOGIC_PATTERNS = [
    re.compile(r"import\s+.*(?:prisma|db|database|mongoose|supabase|drizzle)", re.IGNORECASE),
    re.compile(r"(?:await|\.then)\s+.*(?:find|create|update|delete|query|fetch)", re.IGNORECASE),
    re.compile(r"(?:if|switch|try)\s*\(", re.IGNORECASE),
]


def _check_for_stubs(file_paths: List[str]) -> List[str]:
    """Scan source files for stub indicators.

    Returns a list of human-readable stub descriptions.
    Each entry: "filename:line: matched text"
    """
    stubs_found: List[str] = []
    for fpath in file_paths:
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, "r", errors="ignore") as f:
                lines = f.readlines()
        except (IOError, OSError):
            continue

        fname = os.path.basename(fpath)
        for i, line in enumerate(lines, 1):
            for pattern in _STUB_PATTERNS:
                if pattern.search(line):
                    stubs_found.append(
                        f"{fname}:{i}: {line.strip()}"
                    )
                    break  # One match per line is enough
    return stubs_found


# ── OVL-2: Endpoint stub detection (merged from stub_endpoint_detector.py) ──


def _check_file_for_endpoint_stubs(
    filepath: str, project_dir: str,
) -> Dict[str, Any] | None:
    """Check a single file for stub/placeholder patterns in endpoint handlers.

    Returns a finding dict if stubs detected, or None if the file is clean.

    Priority: stub markers ALWAYS win.  Real-logic patterns only exempt the
    file when NO stub/empty-response markers are present.  This prevents
    files that contain both ``try { }`` and ``// TODO implement`` from being
    silently exempted.

    OVL-2: Merged from stub_endpoint_detector._check_file_for_stubs.
    """
    try:
        with open(filepath, "r") as f:
            content = f.read()
    except IOError:
        return None

    # ── Step 1: Check for stub patterns FIRST (they take priority) ──
    reasons = []
    for pattern in _STUB_PATTERNS:
        if pattern.search(content):
            reasons.append(f"Contains {pattern.pattern.strip(chr(92) + 'b')}")

    for pattern in _EMPTY_RESPONSE_PATTERNS:
        if pattern.search(content):
            reasons.append("Returns empty/null response")

    if reasons:
        return {
            "file": os.path.relpath(filepath, project_dir),
            "route": "",  # Set by caller
            "reason": "; ".join(reasons),
        }

    # ── Step 2: Only exempt via real-logic when NO stubs were found ──
    # (This is now unreachable when stubs are present, which is the fix.)
    if any(p.search(content) for p in _REAL_LOGIC_PATTERNS):
        return None

    return None


def scan_for_stub_endpoints(project_dir: str) -> List[Dict[str, Any]]:
    """Scan API route handlers and service modules for stub/placeholder content.

    Looks in:
      - src/app/api/**/route.{ts,tsx,js,jsx} for placeholder API handlers
      - src/lib/**/*.{ts,tsx,js,jsx} for stub service/utility modules
      - src/services/**/*.{ts,tsx,js,jsx} for stub service modules

    FIX-6 (mainstreet-2-audit): Expanded scope beyond API routes. Critical
    business logic in src/lib/ and src/services/ with TODO/placeholder
    content previously went undetected, shipping broken stubs to production.

    OVL-2: Merged from stub_endpoint_detector.scan_for_stub_endpoints to
    eliminate duplicate scanning at the same lifecycle point.

    Args:
        project_dir: Root directory of the project.

    Returns:
        List of findings, each with 'file', 'route', and 'reason' keys.
        Empty list if no stubs detected.
    """
    findings: List[Dict[str, Any]] = []

    # ── Phase 1: API route handlers (original scope) ──
    api_base = os.path.join(project_dir, "src", "app", "api")
    if os.path.isdir(api_base):
        for root, _dirs, files in os.walk(api_base):
            for fname in files:
                if not re.match(r"route\.(ts|tsx|js|jsx)$", fname):
                    continue

                filepath = os.path.join(root, fname)
                result = _check_file_for_endpoint_stubs(filepath, project_dir)
                if result:
                    # Derive the route path from the file path
                    rel = os.path.relpath(root, os.path.join(project_dir, "src", "app"))
                    result["route"] = "/" + rel.replace(os.sep, "/")
                    findings.append(result)

    # ── Phase 2: Service modules (FIX-6 expansion) ──
    _SERVICE_DIRS = ["src/lib", "src/services"]
    _SERVICE_EXTENSIONS = re.compile(r"\.(ts|tsx|js|jsx)$")

    for rel_dir in _SERVICE_DIRS:
        svc_base = os.path.join(project_dir, rel_dir)
        if not os.path.isdir(svc_base):
            continue

        for root, _dirs, files in os.walk(svc_base):
            for fname in files:
                if not _SERVICE_EXTENSIONS.search(fname):
                    continue

                filepath = os.path.join(root, fname)
                result = _check_file_for_endpoint_stubs(filepath, project_dir)
                if result:
                    # Use the relative directory as the "route" for service modules
                    rel = os.path.relpath(filepath, project_dir)
                    result["route"] = rel.replace(os.sep, "/")
                    findings.append(result)

    if findings:
        logger.warning(
            f"[STUB DETECTOR] Found {len(findings)} stub endpoint(s): "
            f"{', '.join(f['route'] for f in findings[:5])}"
        )

    return findings


# ──────────────────────────────────────────────────────────────────────
# Proof objects: per-REQ verification proof with PASS/FAIL/PARTIAL
# ──────────────────────────────────────────────────────────────────────


def build_proof(
    req_id: str,
    checks: Dict[str, Any],
    evidence: List[str],
    stubs_found: List[str],
) -> Dict[str, Any]:
    """Build a deterministic proof object for a requirement.

    Args:
        req_id: The requirement ID (e.g., "REQ-abc123")
        checks: Dict of check_name → bool/None:
            - no_stubs: True if no stub patterns found in evidence
            - has_logic: True if code has real implementation logic
            - contract_match: True if required literals found
            - build_passes: True if project builds
            - test_passed: True/False/None. True = all test suites
              (npm test, pytest, BDD, Playwright, etc.) passed.
              None = no tests exist for this requirement.
        evidence: List of file paths that constitute evidence
        stubs_found: List of stub descriptions found

    Returns:
        Proof dict with status PASS/FAIL/PARTIAL, checks, evidence,
        stubs_found, and timestamp.
    """
    # Determine status from checks
    test_passed = checks.get("test_passed")
    hard_checks = ["no_stubs", "has_logic", "build_passes"]

    # Any hard check False → FAIL
    if any(checks.get(c) is False for c in hard_checks):
        status = "FAIL"
    # test_passed explicitly False → FAIL
    elif test_passed is False:
        status = "FAIL"
    # All hard checks True but contract or test is None → PARTIAL
    elif checks.get("contract_match") is False or test_passed is None:
        status = "PARTIAL"
    else:
        status = "PASS"

    return {
        "req_id": req_id,
        "status": status,
        "checks": checks,
        "evidence": evidence,
        "stubs_found": stubs_found,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def store_proof(agent_data: dict, proof: Dict[str, Any]) -> None:
    """Store a proof object in agent.data['_verification_proofs'].

    Overwrites any previous proof for the same req_id.
    """
    if "_verification_proofs" not in agent_data:
        agent_data["_verification_proofs"] = {}
    agent_data["_verification_proofs"][proof["req_id"]] = proof
    logger.info(
        f"[PROOF] Stored proof for {proof['req_id']}: {proof['status']}"
    )


# ──────────────────────────────────────────────────────────────────────
# Single requirement verification
# ──────────────────────────────────────────────────────────────────────


def _extract_urls(text: str) -> List[str]:
    """Extract URLs from requirement text."""
    return _URL_RE.findall(text)


def _extract_integration_name(text: str) -> Optional[str]:
    """Extract the integration/service name from requirement text.

    Uses universal pattern matching — no hardcoded vendor list.
    Extracts from patterns like:
        "X integration", "X api", "X sdk", "integrate with X",
        "connect to X", "use X for"
    """
    text_lower = text.lower()

    # Pattern: "<name> integration/api/sdk/service/package"
    match = re.search(r'(\w+)\s+(?:integration|api|sdk|service|package|library)', text_lower)
    if match:
        return match.group(1)

    # Pattern: "integrate with <name>" / "connect to <name>"
    match = re.search(r'(?:integrate|connect|hook)\s+(?:with|to|into)\s+(\w+)', text_lower)
    if match:
        return match.group(1)

    # Pattern: "use <name> for" / "using <name>"
    match = re.search(r'(?:use|using)\s+(\w+)\s+(?:for|to|as)', text_lower)
    if match:
        return match.group(1)

    return None


def _extract_page_routes(text: str) -> List[str]:
    """Extract page route paths from requirement text."""
    routes = []
    # Look for explicit route mentions: "/about", "/contact", "/pricing"
    for match in _PAGE_ROUTE_RE.finditer(text):
        route = match.group(1).strip("/")
        # Filter out common false positives
        if route and route not in ("api", "src", "app", "pages", "components", "lib"):
            routes.append(route)
    return routes


def verify_requirement(project_dir: str, req: Dict[str, Any]) -> Dict[str, Any]:
    """Verify a single requirement against the project codebase.

    Dispatches to category-specific verification strategies:
    - url: grep for the URL string
    - integration: grep for package imports
    - page: check for route file existence
    - feature/model/config: grep for key terms

    Returns:
        Dict with keys:
        - id: requirement ID
        - verified: bool
        - evidence: list of file paths where evidence was found
        - content_hash: md5 short hash for dedup
        - reason: explanation (for unverified)
    """
    req_id = req.get("id", "UNKNOWN")
    text = req.get("text", "")
    category = req.get("category", "general")

    result = {
        "id": req_id,
        "verified": False,
        "evidence": [],
        "content_hash": _content_hash(f"{req_id}:{text}"),
        "reason": "",
    }

    if not text:
        result["reason"] = "Empty requirement text"
        return result

    files = _scan_project_files(project_dir)
    if not files:
        result["reason"] = "No source files found in project"
        return result

    if category == "url":
        return _verify_url(files, req, result)
    elif category == "integration":
        return _verify_integration(files, req, result)
    elif category == "page":
        return _verify_page(project_dir, files, req, result)
    else:
        # feature, model, config, general — prefer test-based, then keyword grep
        return _verify_from_tests(project_dir, req, result)


def _verify_url(files: List[str], req: Dict, result: Dict) -> Dict:
    """Verify URL requirement by grepping for the URL in project files."""
    urls = _extract_urls(req["text"])
    if not urls:
        # No explicit URL — try domain extraction
        result["reason"] = "No URL found in requirement text"
        return result

    for url in urls:
        # Try full URL first, then domain
        matches = _grep_files(files, url)
        if matches:
            result["verified"] = True
            result["evidence"] = matches
            return result
        # Try just the domain/path portion
        # e.g., "cal.com/acme/intro" from "https://cal.com/acme/intro"
        url_path = url.replace("https://", "").replace("http://", "")
        matches = _grep_files(files, url_path)
        if matches:
            result["verified"] = True
            result["evidence"] = matches
            return result

    result["reason"] = f"URL(s) {urls} not found in any project files"
    return result


def _filter_source_files(files: List[str]) -> List[str]:
    """Filter file list to source-only files (for integration verification)."""
    source_files = []
    for fpath in files:
        _, ext = os.path.splitext(fpath)
        # Handle dotfiles like .env where splitext returns ('', '') or ('.env', '')
        basename = os.path.basename(fpath)
        if ext in _SOURCE_ONLY_EXTENSIONS:
            source_files.append(fpath)
        elif not ext and basename.startswith("."):
            # Dotfiles like .env — these are config, skip for source-only
            continue
    return source_files


def _filter_config_files(files: List[str]) -> List[str]:
    """Filter file list to config/non-source files."""
    config_files = []
    for fpath in files:
        _, ext = os.path.splitext(fpath)
        basename = os.path.basename(fpath)
        if ext in _CONFIG_EXTENSIONS:
            config_files.append(fpath)
        elif not ext and basename.startswith("."):
            # Dotfiles like .env are config
            config_files.append(fpath)
    return config_files


def _has_import_in_source(source_files: List[str], name: str) -> List[str]:
    """Check if any source file contains an import/require/from for the given name.

    Looks for patterns like:
    - import X from "name"
    - import { X } from "name"
    - import name from ...
    - require('name')
    - require("name")
    - from name import ...
    - @name/ (scoped package)

    Returns list of matching file paths (empty if no match).
    """
    matches = []
    name_lower = name.lower()

    for fpath in source_files:
        try:
            with open(fpath, "r", errors="ignore") as f:
                file_content = f.read()
        except (IOError, OSError):
            continue

        content_lower = file_content.lower()

        # Check for import/require patterns containing the integration name
        found = False

        # ES6: import ... from "name" or import "name"
        # Matches: import { Resend } from "resend"
        #          import Resend from "resend"
        #          import "resend"
        if re.search(r'\bimport\b.*' + re.escape(name_lower), content_lower):
            found = True

        # Python: from name import ...
        # Matches: from resend import Emails
        if not found and re.search(r'\bfrom\s+' + re.escape(name_lower) + r'\b', content_lower):
            found = True

        # CommonJS: require("name") or require('name')
        # Matches: const resend = require('resend')
        if not found and re.search(r"\brequire\s*\(\s*[\x22\x27]" + re.escape(name_lower), content_lower):
            found = True

        # Scoped package: @name/
        if not found and ("@" + name_lower + "/") in content_lower:
            found = True

        if found:
            matches.append(fpath)
            if len(matches) >= 5:
                break

    return matches

def _verify_integration(files: List[str], req: Dict, result: Dict) -> Dict:
    """Verify integration requirement by searching for package imports in SOURCE files.

    Universal: searches for the integration name in source code files,
    requiring actual import/require/from statements — not just config references.
    No hardcoded vendor list.

    Strategy:
    1. Extract candidate names from requirement text
    2. PRIMARY CHECK: Look for import/require/from in source files only
    3. SECONDARY SIGNAL: If found in config but not source → unverified with reason
    4. If not found anywhere → unverified
    """
    # Collect candidate names to try
    candidates = []
    integration_name = _extract_integration_name(req["text"])
    if integration_name:
        candidates.append(integration_name)

    # Also collect all significant words from the text as fallback candidates
    # (words > 3 chars, not common English/category words)
    _STOP_WORDS = {
        "integration", "service", "payment", "system", "feature",
        "page", "with", "from", "that", "this", "will", "should",
        "must", "have", "need", "requirement", "implement", "create",
        "build", "make", "setup", "link", "connect", "email",
    }
    for word in re.findall(r'\b([a-zA-Z]\w{2,})\b', req["text"]):
        word_lower = word.lower()
        if word_lower not in _STOP_WORDS and word_lower not in candidates:
            candidates.append(word_lower)

    if not candidates:
        result["reason"] = "Could not identify integration name from requirement text"
        return result

    # Split files into source and config
    source_files = _filter_source_files(files)
    config_files = _filter_config_files(files)

    # PRIMARY CHECK: import/require/from in source files
    for name in candidates:
        source_matches = _has_import_in_source(source_files, name)
        if source_matches:
            result["verified"] = True
            result["evidence"] = source_matches
            return result

    # PRIMARY not found — check SECONDARY (config) for better error message
    config_evidence = []
    for name in candidates:
        config_matches = _grep_files(config_files, name)
        if config_matches:
            config_evidence.extend(config_matches)

    if config_evidence:
        # Config reference exists but no source import → false positive scenario
        result["verified"] = False
        result["evidence"] = config_evidence
        result["reason"] = (
            f"Integration referenced in config but not imported in source code. "
            f"Config files: {[os.path.basename(f) for f in config_evidence[:3]]}. "
            f"Candidates: {candidates[:5]}"
        )
        return result

    result["reason"] = f"Integration candidates {candidates[:5]} not found in imports/packages"
    return result


def _verify_page(
    project_dir: str, files: List[str], req: Dict, result: Dict
) -> Dict:
    """Verify page requirement by checking for route file existence.

    Framework-agnostic: scans all project files for a file whose path
    contains the route name (e.g., 'pricing', 'about'). Works with
    Next.js, Nuxt, SvelteKit, Remix, Astro, plain HTML, etc.
    """
    routes = _extract_page_routes(req["text"])
    if not routes:
        result["reason"] = "Could not extract route path from requirement text"
        return result

    for route in routes:
        route_parts = route.split("/")
        last_part = route_parts[-1] if route_parts else route

        # Universal search: find any file whose path contains the route name
        for fpath in files:
            fname_lower = fpath.lower()
            # Match directory-based routing (e.g., /pricing/page.tsx, /pricing/index.tsx)
            if f"/{last_part}/" in fname_lower:
                result["verified"] = True
                result["evidence"] = [fpath]
                return result
            # Match file-based routing (e.g., pricing.tsx, about.vue, contact.astro)
            basename = os.path.basename(fpath)
            name_no_ext = os.path.splitext(basename)[0]
            if name_no_ext.lower() == last_part.lower():
                result["verified"] = True
                result["evidence"] = [fpath]
                return result

    result["reason"] = f"Page route(s) {routes} not found in project file structure"
    return result


def _verify_from_tests(
    project_dir: str, req: Dict, result: Dict
) -> Dict:
    """Verify requirement via test linkage instead of keyword grep.

    FIX-19: Replaces _verify_keyword for requirements that have REQ-IDs
    embedded in test files. Falls back to _verify_keyword if no test
    linkage is found.
    """
    req_id = req.get("id", "")
    if not req_id:
        # No REQ-ID → fall back to keyword
        files = _scan_project_files(project_dir)
        return _verify_keyword(files, req, result)

    from python.helpers.test_req_linker import scan_test_files_for_reqs
    req_map = scan_test_files_for_reqs(project_dir)

    if req_id in req_map:
        test_files = req_map[req_id]
        result["verified"] = True
        result["evidence"] = test_files
        result["verification_method"] = "test_linkage"
        return result

    # No test linkage found — fall back to keyword verification
    files = _scan_project_files(project_dir)
    return _verify_keyword(files, req, result)


def _verify_keyword(files: List[str], req: Dict, result: Dict) -> Dict:
    """Verify feature/general requirement by grepping for key terms.

    Enhanced with stub detection: even if keywords are found, if the
    evidence files contain stub patterns (TODO, return [], etc.),
    verification fails.
    """
    text = req.get("text", "")
    # Extract meaningful keywords (3+ chars, not common words)
    stop_words = {
        "the", "and", "for", "with", "that", "this", "from", "should",
        "must", "have", "page", "add", "use", "create", "make", "build",
        "implement", "include", "display", "show", "ensure", "need",
    }
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text)
    keywords = [w for w in words if w.lower() not in stop_words]

    # Search for the most distinctive keywords (longest first)
    keywords.sort(key=len, reverse=True)
    for kw in keywords[:5]:  # Top 5 most distinctive
        matches = _grep_files(files, kw)
        if matches:
            # Stub-aware check: verify evidence files don't contain stubs
            stubs = _check_for_stubs(matches)
            if stubs:
                result["verified"] = False
                result["evidence"] = matches
                result["reason"] = (
                    f"STUB DETECTED: keyword '{kw}' found but evidence files "
                    f"contain stub patterns: {'; '.join(stubs[:3])}"
                )
                return result
            result["verified"] = True
            result["evidence"] = matches
            return result

    result["reason"] = f"No evidence of implementation found for keywords: {keywords[:5]}"
    return result


# ──────────────────────────────────────────────────────────────────────
# Batch verification
# ──────────────────────────────────────────────────────────────────────


def verify_requirements_batch(
    project_dir: str,
    requirements: List[Dict[str, Any]],
) -> Dict[str, List[Dict]]:
    """Verify multiple requirements against the project codebase.

    Returns:
        Dict with keys:
        - verified: list of verification results that passed
        - unverified: list of verification results that failed
    """
    verified = []
    unverified = []

    for req in requirements:
        result = verify_requirement(project_dir, req)
        if result["verified"]:
            verified.append(result)
        else:
            unverified.append(result)

    return {"verified": verified, "unverified": unverified}


# ──────────────────────────────────────────────────────────────────────
# Blueprint-based file spec verification
# ──────────────────────────────────────────────────────────────────────


def verify_file_spec(
    project_dir: str,
    spec: Dict[str, Any],
) -> Dict[str, Any]:
    """Verify a single file spec against the project codebase.

    Unlike regex-inference verification, this uses explicit, machine-
    verifiable criteria from the blueprint's file_specs:
    - must_exist: file must be present
    - min_bytes: file must be at least N bytes (stub detection)
    - must_contain: list of strings that must appear (comments stripped)
    - must_import: list of import patterns that must appear
    - must_export: list of export patterns that must appear

    Args:
        project_dir: Root directory of the project
        spec: File spec dict with path and criteria

    Returns:
        Dict with keys: path, verified, reason, missing_criteria
    """
    file_path = spec.get("path", "")
    full_path = os.path.join(project_dir, file_path)

    result = {
        "path": file_path,
        "verified": False,
        "reason": "",
        "missing_criteria": [],
    }

    # Check 1: File exists
    if spec.get("must_exist", True) and not os.path.isfile(full_path):
        result["reason"] = f"File not found: {file_path}"
        return result

    # Check 2: Minimum file size (stub detection)
    min_bytes = spec.get("min_bytes", 0)
    if min_bytes > 0:
        file_size = os.path.getsize(full_path)
        if file_size < min_bytes:
            result["reason"] = (
                f"File too small: {file_path} is {file_size} bytes "
                f"(minimum {min_bytes})"
            )
            return result

    # Read file content (with comments stripped for must_contain)
    try:
        with open(full_path, "r", errors="ignore") as f:
            raw_content = f.read()
    except (IOError, OSError) as e:
        result["reason"] = f"Cannot read file: {e}"
        return result

    stripped_content = _strip_comments(raw_content)
    stripped_lower = stripped_content.lower()

    # Check 3: must_contain — strings must appear in non-comment code
    missing_contain = []
    for term in spec.get("must_contain", []):
        if term.lower() not in stripped_lower:
            missing_contain.append(term)
    if missing_contain:
        result["reason"] = (
            f"Missing required content in {file_path}: "
            f"{', '.join(missing_contain)}"
        )
        result["missing_criteria"] = missing_contain
        return result

    # Check 4: must_import — import patterns in raw content
    # (imports can be in comments for documentation, so check raw)
    missing_import = []
    raw_lower = raw_content.lower()
    for pattern in spec.get("must_import", []):
        if pattern.lower() not in raw_lower:
            missing_import.append(pattern)
    if missing_import:
        result["reason"] = (
            f"Missing imports in {file_path}: "
            f"{', '.join(missing_import)}"
        )
        result["missing_criteria"] = missing_import
        return result

    # Check 5: must_export — export patterns in raw content
    missing_export = []
    for pattern in spec.get("must_export", []):
        export_patterns = [
            f"export {pattern.lower()}",
            f"export function {pattern.lower()}",
            f"export async function {pattern.lower()}",
            f"export const {pattern.lower()}",
            f"export default {pattern.lower()}",
            f"module.exports",
        ]
        found = any(p in raw_lower for p in export_patterns)
        if not found:
            missing_export.append(pattern)
    if missing_export:
        result["reason"] = (
            f"Missing exports in {file_path}: "
            f"{', '.join(missing_export)}"
        )
        result["missing_criteria"] = missing_export
        return result

    # All checks passed
    result["verified"] = True
    return result


# ──────────────────────────────────────────────────────────────────────
# Remediation task creation with n.n.n sequencing
# ──────────────────────────────────────────────────────────────────────


def create_remediation_task_id(delegation_seq: int, sub_seq: int) -> str:
    """Create n.n remediation task ID.

    Args:
        delegation_seq: The parent delegation sequence number (n)
        sub_seq: The remediation sub-sequence number (n.n)

    Returns:
        Task ID string in format "n.n" (e.g., "3.1", "3.2")
    """
    return f"{delegation_seq}.{sub_seq}"


def build_remediation_task(
    delegation_seq: int,
    sub_seq: int,
    req_id: str,
    req_text: str,
    profile: str,
) -> Dict[str, Any]:
    """Build a structured remediation task for an unverified requirement.

    Args:
        delegation_seq: Parent delegation sequence number
        sub_seq: Remediation sub-sequence number
        req_id: The requirement ID (e.g., "REQ-005")
        req_text: The requirement description text
        profile: Suggested agent profile for remediation

    Returns:
        Dict with keys: task_id, req_id, content_hash, description, profile, status
    """
    task_id = create_remediation_task_id(delegation_seq, sub_seq)
    description = f"REMEDIATE {req_id}: {req_text}"

    return {
        "task_id": task_id,
        "req_id": req_id,
        "content_hash": _content_hash(f"{req_id}:{req_text}"),
        "description": description,
        "profile": profile,
        "status": "pending",
    }


# ──────────────────────────────────────────────────────────────────────
# Ledger integration
# ──────────────────────────────────────────────────────────────────────


def add_remediation_to_ledger(
    agent_data: dict,
    tasks: List[Dict[str, Any]],
) -> int:
    """Add remediation tasks to the requirements ledger with dedup.

    Args:
        agent_data: The agent.data dict
        tasks: List of remediation task dicts from build_remediation_task()

    Returns:
        Number of new tasks added (after dedup)
    """
    from python.helpers.requirements_ledger import _ensure_ledger

    ledger = _ensure_ledger(agent_data)
    if "remediation_tasks" not in ledger:
        ledger["remediation_tasks"] = []

    # Build dedup set from existing tasks
    existing_hashes = {
        t.get("content_hash") for t in ledger["remediation_tasks"]
    }

    added = 0
    for task in tasks:
        content_hash = task.get("content_hash", "")
        if content_hash in existing_hashes:
            continue  # Dedup: skip duplicate
        existing_hashes.add(content_hash)
        ledger["remediation_tasks"].append(task)
        added += 1

    if added:
        logger.info(
            f"[REQ VERIFIER] Added {added} remediation tasks to ledger "
            f"(total: {len(ledger['remediation_tasks'])})"
        )

    return added


def mark_reqs_unverified(agent_data: dict, req_ids: List[str]) -> None:
    """Mark requirements as 'unverified' in the ledger.

    Args:
        agent_data: The agent.data dict
        req_ids: List of REQ-XXX IDs to mark as unverified
    """
    from python.helpers.requirements_ledger import _ensure_ledger

    ledger = _ensure_ledger(agent_data)
    from python.helpers.req_id_normalizer import build_normalized_req_map
    req_map = build_normalized_req_map(ledger.get("requirements", []))

    for req_id in req_ids:
        if req_id in req_map:
            req_map[req_id]["status"] = "unverified"

    logger.info(
        f"[REQ VERIFIER] Marked {len(req_ids)} requirements as 'unverified'"
    )


# ──────────────────────────────────────────────────────────────────────
# Manifest formatting (for injection into tool result)
# ──────────────────────────────────────────────────────────────────────


def format_remediation_manifest(
    tasks: List[Dict[str, Any]],
    delegation_id: str = "",
) -> str:
    """Format remediation tasks into a manifest for orchestrator injection.

    Args:
        tasks: List of remediation task dicts
        delegation_id: The parent delegation ID (for context)

    Returns:
        Formatted manifest string. Empty string if no tasks.
    """
    if not tasks:
        return ""

    lines = [
        "\n---",
        "## ⚠️ POST-EXECUTION VERIFICATION: REMEDIATION REQUIRED",
        "",
        f"The following requirements from `{delegation_id}` were NOT verified "
        "in the project codebase after the subordinate returned. You MUST "
        "create remediation delegations for each unverified requirement.",
        "",
        "| Task ID | REQ | Description | Profile |",
        "|---------|-----|-------------|---------|",
    ]

    for task in tasks:
        lines.append(
            f"| {task['task_id']} | {task['req_id']} "
            f"| {task['description'][:80]} | {task['profile']} |"
        )

    lines.append("")
    lines.append(
        "**ACTION**: Create a `call_subordinate` for each remediation task above, "
        "using the specified profile and including the REQ-ID in requirement_ids."
    )
    lines.append("---\n")

    return "\n".join(lines)
