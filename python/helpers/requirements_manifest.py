"""
Requirements Manifest Helper — parse and validate requirements checklists.
=========================================================================

Reads a `requirements_manifest.md` file and determines whether a project's
deliverables are complete by counting checked vs unchecked markdown checkboxes.

Root cause (Iteration 211 RCA):
    Agent delivered "complete" responses with fabricated content and missing
    features because no gate checked the requirements manifest for unchecked
    items before allowing the response to pass.

Usage:
    from python.helpers.requirements_manifest import (
        check_manifest_completeness,
        format_manifest_warning,
    )

    result = check_manifest_completeness("/path/to/requirements_manifest.md")
    if result and not result["complete"]:
        warning = format_manifest_warning(result)
        # Inject warning into agent context
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List, Optional

logger = logging.getLogger("agix.requirements_manifest")

# Match markdown checkboxes: `- [ ]`, `- [x]`, `- [X]`, with optional indentation
_CHECKBOX_RE = re.compile(r"^\s*-\s+\[([ xX])\]\s+(.+)$", re.MULTILINE)

# Extract literals from manifest labels: backtick-quoted strings and URLs
_LITERAL_RE = re.compile(r"`([^`]+)`")
_URL_RE = re.compile(r"https?://[^\s`\"')\]]+")

# File extensions and env files are now handled by the canonical source_scanner.
# Import the shared literal search function.
from python.helpers.source_scanner import literal_exists as _search_project_for_literal_impl


# ── Known API service patterns for extraction ──────────────────────────────────
# Maps service keyword(s) → {service_name, api_key_env_var, expected_lib_file}
_KNOWN_API_SERVICES = [
    {
        "keywords": ["stripe"],
        "service_name": "stripe",
        "api_key_env_var": "STRIPE_API_KEY",
        "expected_lib_file": "lib/stripe.ts",
    },
    {
        "keywords": ["openai"],
        "service_name": "openai",
        "api_key_env_var": "OPENAI_API_KEY",
        "expected_lib_file": "lib/openai.ts",
    },
    {
        "keywords": ["google places", "google_places", "places api"],
        "service_name": "google_places",
        "api_key_env_var": "GOOGLE_PLACES_API_KEY",
        "expected_lib_file": "lib/google-places.ts",
    },
    {
        "keywords": ["resend"],
        "service_name": "resend",
        "api_key_env_var": "RESEND_API_KEY",
        "expected_lib_file": "lib/resend.ts",
    },
    {
        "keywords": ["perplexity"],
        "service_name": "perplexity",
        "api_key_env_var": "PERPLEXITY_API_KEY",
        "expected_lib_file": "lib/perplexity.ts",
    },
    {
        "keywords": ["twilio"],
        "service_name": "twilio",
        "api_key_env_var": "TWILIO_API_KEY",
        "expected_lib_file": "lib/twilio.ts",
    },
    {
        "keywords": ["sendgrid"],
        "service_name": "sendgrid",
        "api_key_env_var": "SENDGRID_API_KEY",
        "expected_lib_file": "lib/sendgrid.ts",
    },
    {
        "keywords": ["supabase"],
        "service_name": "supabase",
        "api_key_env_var": "SUPABASE_URL",
        "expected_lib_file": "lib/supabase.ts",
    },
    {
        "keywords": ["firebase"],
        "service_name": "firebase",
        "api_key_env_var": "FIREBASE_API_KEY",
        "expected_lib_file": "lib/firebase.ts",
    },
    {
        "keywords": ["cloudinary"],
        "service_name": "cloudinary",
        "api_key_env_var": "CLOUDINARY_API_KEY",
        "expected_lib_file": "lib/cloudinary.ts",
    },
    {
        "keywords": ["aws", "amazon web services"],
        "service_name": "aws",
        "api_key_env_var": "AWS_ACCESS_KEY_ID",
        "expected_lib_file": "lib/aws.ts",
    },
    {
        "keywords": ["clerk"],
        "service_name": "clerk",
        "api_key_env_var": "CLERK_SECRET_KEY",
        "expected_lib_file": "lib/clerk.ts",
    },
    {
        "keywords": ["auth0"],
        "service_name": "auth0",
        "api_key_env_var": "AUTH0_SECRET",
        "expected_lib_file": "lib/auth0.ts",
    },
    {
        "keywords": ["mapbox"],
        "service_name": "mapbox",
        "api_key_env_var": "MAPBOX_ACCESS_TOKEN",
        "expected_lib_file": "lib/mapbox.ts",
    },
    {
        "keywords": ["algolia"],
        "service_name": "algolia",
        "api_key_env_var": "ALGOLIA_API_KEY",
        "expected_lib_file": "lib/algolia.ts",
    },
    {
        "keywords": ["anthropic", "claude"],
        "service_name": "anthropic",
        "api_key_env_var": "ANTHROPIC_API_KEY",
        "expected_lib_file": "lib/anthropic.ts",
    },
]

# Regex for generic API-related keywords
_API_GENERIC_RE = re.compile(
    r'\b(?:api[_ ]?key|api[_ ]?integration|api[_ ]?endpoint)\b',
    re.IGNORECASE,
)


def extract_api_integrations(prompt_text: Optional[str]) -> List[Dict]:
    """Extract API integration references from a user prompt.

    Scans the prompt for known API services (Stripe, OpenAI, etc.) and
    generic API-related keywords. Returns a list of structured dicts
    for inclusion in content_manifest.json under the 'api_integrations' key.

    Args:
        prompt_text: The raw user prompt text. Can be None.

    Returns:
        List of dicts, each with keys:
            - service_name (str): Lowercase service identifier
            - api_key_env_var (str): Expected environment variable name
            - expected_lib_file (str): Expected library file path
    """
    if not prompt_text:
        return []

    prompt_lower = prompt_text.lower()
    seen_services = set()
    integrations = []

    # Check each known service
    for service_def in _KNOWN_API_SERVICES:
        if service_def["service_name"] in seen_services:
            continue

        for keyword in service_def["keywords"]:
            if keyword in prompt_lower:
                seen_services.add(service_def["service_name"])
                integrations.append({
                    "service_name": service_def["service_name"],
                    "api_key_env_var": service_def["api_key_env_var"],
                    "expected_lib_file": service_def["expected_lib_file"],
                })
                break

    return integrations


def _extract_verifiable_literals(label: str) -> List[str]:
    """Extract verifiable string literals from a manifest label.

    Extracts:
    - Backtick-quoted strings: `https://cal.com/...`, `OPENROUTER_API_KEY`
    - URLs: https://buy.stripe.com/...
    - Named entities after known prefixes: "Founder: Jon Leaman"

    Returns a list of strings to search for in source code.
    """
    literals = []

    # Backtick-quoted literals
    for match in _LITERAL_RE.finditer(label):
        val = match.group(1).strip()
        if val:
            literals.append(val)

    # URLs not already captured by backticks
    for match in _URL_RE.finditer(label):
        url = match.group(0)
        if url not in literals:
            literals.append(url)

    # Named entity patterns: "Founder: Jon Leaman", "Email: jon@..."
    for prefix in ("Founder:", "Email:", "Company:", "Domain:"):
        if prefix in label:
            entity = label.split(prefix, 1)[1].strip().strip("`")
            if entity and entity not in literals:
                literals.append(entity)

    return literals


def _search_project_for_literal(project_dir: str, literal: str) -> bool:
    """Search project source files for a literal string.

    Delegates to python.helpers.source_scanner.literal_exists().
    Kept as a thin wrapper to minimize diff churn in callers.

    Args:
        project_dir: Root of the project directory.
        literal: The string to search for.

    Returns:
        True if the literal is found in any source file.
    """
    return _search_project_for_literal_impl(project_dir, literal)


def check_manifest_completeness(
    manifest_path: str,
    project_dir: Optional[str] = None,
    agent_data: Optional[Dict] = None,
) -> Optional[Dict]:
    """Parse a requirements manifest and return completion status.

    Verification uses THREE sources (any match = verified):
    1. Checkbox state: `[x]` in the manifest file
    2. Ledger cross-ref: requirement marked 'completed' in agent.data
    3. Source code grep: literal values found in project source files

    RCA-258: The manifest is a write-once artifact, but the requirements
    ledger (agent.data) tracks completion via mark_delegation_complete().
    Previously these two systems didn't talk to each other, causing 0/33
    status even when all work was done. This function now bridges them.

    Args:
        manifest_path: Absolute path to requirements_manifest.md.
        project_dir: Optional project root for source-code verification.
        agent_data: Optional agent.data dict for ledger cross-referencing.

    Returns:
        Dict with keys:
            - complete (bool): True if all items are checked or verified
            - total (int): Total number of checkbox items
            - done (int): Number of checked/verified items
            - missing (List[str]): Labels of unchecked+unverified items
            - completion_pct (float): Percentage complete (0-100)
        Returns None if file doesn't exist or has no checkboxes.
    """
    if not os.path.isfile(manifest_path):
        logger.debug(f"Manifest not found: {manifest_path}")
        return None

    try:
        with open(manifest_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except (IOError, OSError) as e:
        logger.warning(f"Failed to read manifest: {manifest_path}: {e}")
        return None

    matches = _CHECKBOX_RE.findall(content)
    if not matches:
        return None

    # Build set of completed requirement texts from ledger for cross-ref
    completed_req_texts = _get_completed_requirement_texts(agent_data)

    total = len(matches)
    done = 0
    missing = []

    for check_char, label in matches:
        label = label.strip()
        if check_char.lower() == "x":
            # Path 1: Manually checked — always counts
            done += 1
        elif _is_verified_by_ledger(label, completed_req_texts):
            # Path 2: Ledger cross-ref — requirement completed via
            # mark_delegation_complete in requirements_ledger.py
            done += 1
            logger.debug(
                f"[MANIFEST] Auto-verified by ledger: {label[:60]}"
            )
        elif project_dir and os.path.isdir(project_dir):
            # Path 3: Source code grep — literal values in code
            literals = _extract_verifiable_literals(label)
            if literals and any(
                _search_project_for_literal(project_dir, lit)
                for lit in literals
            ):
                done += 1
                logger.debug(
                    f"[MANIFEST] Auto-verified by source: {label[:60]}"
                )
            else:
                missing.append(label)
        else:
            missing.append(label)

    completion_pct = round((done / total) * 100, 1) if total > 0 else 0.0

    return {
        "complete": done == total,
        "total": total,
        "done": done,
        "missing": missing,
        "completion_pct": completion_pct,
    }


def _get_completed_requirement_texts(agent_data: Optional[Dict]) -> set:
    """Extract text of completed requirements from the requirements ledger.

    The ledger lives in agent.data["_requirements_ledger"]["requirements"]
    and is updated by mark_delegation_complete().

    Returns:
        Set of lowercase requirement text strings that are completed.
    """
    if not agent_data:
        return set()

    ledger = agent_data.get("_requirements_ledger", {})
    requirements = ledger.get("requirements", [])

    completed = set()
    for req in requirements:
        if req.get("status") == "completed":
            text = req.get("text", "").lower().strip()
            if text:
                completed.add(text)
    return completed


def _is_verified_by_ledger(label: str, completed_texts: set) -> bool:
    """Check if a manifest label matches a completed requirement in the ledger.

    Uses fuzzy word-overlap matching since the manifest label format may
    differ from the ledger requirement text (e.g., manifest has backtick
    formatting, REQ-IDs, category tags).

    Args:
        label: The manifest checkbox label text.
        completed_texts: Set of completed requirement texts from ledger.

    Returns:
        True if the label matches a completed requirement.
    """
    if not completed_texts:
        return False

    # Extract just the meaningful content (strip REQ-IDs, backticks, etc.)
    clean_label = label.lower().strip()
    # Remove REQ-XXX prefix if present
    clean_label = re.sub(r"req-\d+\s*:?\s*", "", clean_label)
    # Remove markdown formatting
    clean_label = clean_label.replace("`", "").replace("*", "").strip()

    if not clean_label:
        return False

    # Direct substring match
    for text in completed_texts:
        if clean_label in text or text in clean_label:
            return True

    # Word overlap match (≥60% of label words found in requirement text)
    stop_words = {
        "the", "a", "an", "is", "are", "to", "for", "and", "or", "of",
        "in", "on", "at", "by", "with", "from", "this", "that", "it",
        "be", "as", "do", "not", "use", "must", "should",
    }
    label_words = set(clean_label.split()) - stop_words
    if len(label_words) < 2:
        return False

    for text in completed_texts:
        text_words = set(text.split()) - stop_words
        overlap = label_words & text_words
        if len(overlap) >= max(2, len(label_words) * 0.6):
            return True

    return False


def format_manifest_warning(
    result: Optional[Dict],
) -> Optional[str]:
    """Format a warning message for incomplete requirements.

    Args:
        result: Output from check_manifest_completeness().

    Returns:
        Formatted warning string, or None if requirements are complete
        or result is None.
    """
    if result is None:
        return None

    if result.get("complete", True):
        return None

    missing_list = "\n".join(f"  - [ ] {item}" for item in result["missing"])
    pct = result.get("completion_pct", 0)
    done = result.get("done", 0)
    total = result.get("total", 0)

    return (
        f"## ⚠️ DELIVERY BLOCKED — REQUIREMENTS INCOMPLETE\n"
        f"\n"
        f"**Progress:** {done}/{total} items complete ({pct}%)\n"
        f"\n"
        f"The following requirements from `requirements_manifest.md` are **NOT delivered**:\n"
        f"\n"
        f"{missing_list}\n"
        f"\n"
        f"**You MUST complete or explicitly address every item above before delivering.**\n"
        f"If a requirement cannot be fulfilled, explain why in your response.\n"
        f"Do NOT mark the task as complete until all items are checked.\n"
    )
