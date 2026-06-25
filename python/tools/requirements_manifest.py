"""
Requirements Management Tool

Gives the orchestrator LLM direct access to the requirements ledger:
  - init: Bootstrap the ledger from extracted prompt requirements (Phase 0)
  - list: Show all tracked requirements with their status
  - coverage: Show coverage statistics (total/assigned/completed/unassigned)
  - suggest: Return unassigned requirement IDs ready for delegation
  - update: Add new requirements dynamically
  - mark_complete: Mark a requirement as completed
  - save_manifest: Persist content_manifest.json, decomposition_index.json, or requirements_ledger.json

This replaces all write_to_file usage for planning artifacts.
Multiagentdev should NEVER use write_to_file — all writes go through this tool.

Architecture: Thin tool wrapper around python.helpers.requirements_ledger.
"""
from __future__ import annotations

import glob
import json
import os
import logging
from typing import Any

from python.helpers.tool import Tool, Response
from python.helpers.planning_paths import get_path as _planning_path
from python.helpers.requirements_ledger import (
    get_coverage,
    get_unassigned_requirements,
    add_requirement,
    mark_requirement_complete,
    check_assignment_coverage,
    init_requirements,
    supplement_from_prompt,
    _ensure_ledger,
)

logger = logging.getLogger("agix.requirements_tool")

# Module-level import for project resolution — used by _ensure_active_project_dir
# and all action handlers. Imported here so it can be patched cleanly in tests.
from python.helpers import projects
from python.helpers.projects import get_decomp_index_path


from python.tools.requirements_config import _MANDATORY_PHASES, _PHASE_ARTIFACT_MAP, _reconciler_warnings, _ensure_active_project_dir

def _normalize_manifest_schema(manifest: dict) -> dict:
    """Normalize content_manifest.json to canonical schema.

    ISS-R2-v2 FIX: The LLM consistently uses flat strings for founder
    (e.g., `branding.founder: "Jon"`) instead of the canonical nested
    structure `founder: {name, email}`. This deterministic Layer 1
    normalizer restructures the manifest.

    Returns a new dict (does not mutate input).
    """
    result = dict(manifest)  # shallow copy

    # Case 1: branding.founder (flat string) + branding.email
    branding = result.get("branding")
    if isinstance(branding, dict):
        founder_str = branding.get("founder") or branding.get("founder_name")
        email_str = branding.get("email") or branding.get("founder_email")

        if isinstance(founder_str, str) and founder_str.strip():
            result["founder"] = {
                "name": founder_str.strip(),
                "email": (email_str or "").strip(),
            }
            # Clean up branding — remove migrated fields
            cleaned_branding = {k: v for k, v in branding.items()
                                if k not in ("founder", "founder_name",
                                             "email", "founder_email")}
            if cleaned_branding:
                result["branding"] = cleaned_branding
            else:
                del result["branding"]

            logger.info(
                f"[REQUIREMENTS TOOL] ISS-R2-v2: Normalized founder from "
                f"branding.founder='{founder_str}' to canonical founder dict"
            )

    # Case 2: Top-level founder as flat string
    top_founder = result.get("founder")
    if isinstance(top_founder, str) and top_founder.strip():
        # Look for email at top level or in branding
        email = result.pop("email", "") or ""
        result["founder"] = {
            "name": top_founder.strip(),
            "email": email.strip(),
        }
        logger.info(
            f"[REQUIREMENTS TOOL] ISS-R2-v2: Normalized top-level flat founder "
            f"'{top_founder}' to canonical dict"
        )

    # Case 3: Already canonical — no-op
    # (founder is a dict with name/email keys)

    # Case 4: Normalize "links" → "urls" key aliasing (RCA-ITR3 F-1)
    # LLM sometimes uses "links" instead of canonical "urls"
    links = result.get("links")
    if isinstance(links, dict) and "urls" not in result:
        result["urls"] = links
        del result["links"]
        logger.info(
            "[REQUIREMENTS TOOL] RCA-ITR3: Normalized 'links' → 'urls' "
            f"({len(links)} entries)"
        )

    # Case 5: Promote stripe URLs from urls.* to pricing.stripe_*_url
    # F-3 AUDIT FIX: Replaced hardcoded _STRIPE_ALIASES with fuzzy matching.
    # Any key that fuzzy-matches a canonical stripe_*_url key gets promoted.
    urls = result.get("urls", {})
    pricing = result.get("pricing", {})
    if isinstance(pricing, dict):
        from python.helpers.manifest_normalizer import fuzzy_match_key
        _CANONICAL_STRIPE_KEYS = [
            "stripe_monthly_url", "stripe_annual_url",
            "stripe_prepaid_url", "stripe_quarterly_url",
        ]
        # Check in urls dict
        if isinstance(urls, dict):
            for src_key, src_val in list(urls.items()):
                if "stripe" in src_key.lower():
                    matched = fuzzy_match_key(src_key, _CANONICAL_STRIPE_KEYS)
                    if matched and matched not in pricing:
                        pricing[matched] = src_val
        # Also check in pricing dict itself (LLM may put *_link directly in pricing)
        for src_key in list(pricing.keys()):
            if "stripe" in src_key.lower() and src_key not in _CANONICAL_STRIPE_KEYS:
                matched = fuzzy_match_key(src_key, _CANONICAL_STRIPE_KEYS)
                if matched and matched != src_key and matched not in pricing:
                    pricing[matched] = pricing[src_key]
        if pricing:
            result["pricing"] = pricing

    # Case 5b: Promote calendly/booking/scheduling from integrations to urls
    # F-3 AUDIT FIX: Replaced hardcoded _CALENDLY_ALIASES with fuzzy matching.
    # Any integration key that fuzzy-matches scheduling-related canonical keys
    # gets promoted to urls.calendly.
    _CANONICAL_SCHEDULING_KEYS = [
        "calendly_url", "calendly", "booking_url", "scheduling_url",
    ]
    integrations = result.get("integrations", {})
    if isinstance(integrations, dict):
        calendly_val = None
        matched_alias = None
        for int_key, int_val in integrations.items():
            if not isinstance(int_val, str):
                continue
            # Try fuzzy matching against canonical scheduling keys
            matched = fuzzy_match_key(int_key, _CANONICAL_SCHEDULING_KEYS, threshold=0.6)
            if matched:
                calendly_val = int_val
                matched_alias = int_key
                break
        if calendly_val:
            if not isinstance(urls, dict):
                urls = {}
            if "calendly" not in urls:
                urls["calendly"] = calendly_val
                result["urls"] = urls
                logger.info(
                    f"[REQUIREMENTS TOOL] F-3 AUDIT: Promoted integrations.{matched_alias} → urls.calendly (fuzzy match)"
                )

    # Case 5c: Promote outreach_scenarios dict/list → scenarios array
    # ITR-18 FIX: LLM uses outreach_scenarios object instead of scenarios array
    # RCA-400 F-2: Also handle outreach_scenarios as a list (not just dict)
    outreach = result.get("outreach_scenarios")
    if isinstance(outreach, dict) and "scenarios" not in result:
        scenarios_list = []
        for key in sorted(outreach.keys()):
            entry = outreach[key]
            if isinstance(entry, dict):
                entry["id"] = key
                scenarios_list.append(entry)
        result["scenarios"] = scenarios_list
        logger.info(
            f"[REQUIREMENTS TOOL] ITR-18: Promoted outreach_scenarios dict → "
            f"scenarios array ({len(scenarios_list)} entries)"
        )
    elif isinstance(outreach, list) and "scenarios" not in result:
        # RCA-400 F-2: LLM produces a flat list of scenario strings/dicts
        result["scenarios"] = outreach
        logger.info(
            f"[REQUIREMENTS TOOL] RCA-400 F-2: Promoted outreach_scenarios list → "
            f"scenarios array ({len(outreach)} entries)"
        )

    # Case 5d: Promote tech_stack from branding to top level
    # ITR-18 FIX: LLM puts tech_stack under branding instead of top level
    branding = result.get("branding", {})
    if isinstance(branding, dict):
        tech_stack = branding.get("tech_stack")
        if tech_stack and "tech_stack" not in result:
            result["tech_stack"] = tech_stack
            logger.info("[REQUIREMENTS TOOL] ITR-18: Promoted branding.tech_stack → top-level")

    # F-3 (ITR-13, L1): Detect duplicate URLs from the same domain.
    # When the LLM saves the same URL for both stripe_monthly and
    # stripe_prepaid, emit a warning. The architect's manifest cross-ref
    # mandate (F-2) is the primary fix; this is defense-in-depth.
    if isinstance(urls, dict):
        url_values = list(urls.values())
        url_set = set(v for v in url_values if isinstance(v, str) and v.startswith("http"))
        if len(url_values) > len(url_set):
            # Find which keys share the same URL
            from collections import Counter
            url_counts = Counter(v for v in url_values if isinstance(v, str) and v.startswith("http"))
            duplicates = {url: count for url, count in url_counts.items() if count > 1}
            if duplicates:
                logger.warning(
                    f"[REQUIREMENTS TOOL] F-3 MANIFEST DEDUP WARNING: "
                    f"{len(duplicates)} URL(s) are used for multiple keys. "
                    f"Verify the user prompt has distinct URLs for each: {duplicates}"
                )

    # Case 6: Ensure domain is accessible at top level
    # Check urls.domain first, then branding.domain, then founder.email
    if "domain" not in result:
        if isinstance(urls, dict) and "domain" in urls:
            result["domain"] = urls["domain"]
        elif isinstance(branding, dict) and "domain" in branding:
            result["domain"] = branding["domain"]
            logger.info("[REQUIREMENTS TOOL] ITR-18: Promoted branding.domain → top-level")

    # RCA-400 F-3: Extract domain from founder.email if no other source
    if "domain" not in result:
        founder = result.get("founder", {})
        if isinstance(founder, dict):
            email = founder.get("email", "")
            if isinstance(email, str) and "@" in email:
                email_domain = email.split("@")[1].strip()
                if "." in email_domain:
                    result["domain"] = email_domain
                    logger.info(
                        f"[REQUIREMENTS TOOL] RCA-400 F-3: Extracted domain "
                        f"'{email_domain}' from founder.email"
                    )

    # F-6 (ITR-18): Auto-detect tech_stack from project markers if missing
    if 'tech_stack' not in result or not result['tech_stack']:
        detected_stack = []
        # These are read from the manifest itself — not from disk
        if isinstance(result.get('urls', {}), dict) and result.get('urls'):
            # Project has URLs → suggests web stack
            detected_stack.append('Next.js')  # Default for web projects
        # Check for framework hints in existing data
        scenarios = result.get('scenarios', [])
        if scenarios:
            detected_stack.append('TypeScript')  # Scenarios imply web
        if detected_stack:
            result['tech_stack'] = detected_stack
            logger.info(
                f"[REQUIREMENTS TOOL] F-6: Auto-detected tech_stack: {detected_stack}"
            )

    # F-6 (ITR-18): Promote openrouter_model / ai_model to top level
    for key in ('openrouter_model', 'ai_model', 'model'):
        if key in result and isinstance(result[key], str):
            if 'ai_model' not in result:
                result['ai_model'] = result[key]
            break
    # Check nested locations
    for section_key in ('integrations', 'branding', 'config'):
        section = result.get(section_key, {})
        if isinstance(section, dict):
            for key in ('openrouter_model', 'ai_model', 'model'):
                if key in section and isinstance(section[key], str):
                    if 'ai_model' not in result:
                        result['ai_model'] = section[key]
                        logger.info(
                            f"[REQUIREMENTS TOOL] F-6: Promoted {section_key}.{key} → ai_model"
                        )
                    break

    # ITR-45 FIX (RCA-1): Resolve marketing model name to API slug.
    # Without this, each downstream agent independently guesses the slug,
    # causing inconsistencies like "anthropic/claude-4-sonnet" vs
    # "anthropic/claude-sonnet-4" in the same project.
    ai_model = result.get('ai_model')
    if ai_model and isinstance(ai_model, str) and 'ai_model_slug' not in result:
        try:
            from python.helpers.model_resolver import resolve_model_slug
            resolved_slug = resolve_model_slug(ai_model)
            if resolved_slug:
                result['ai_model_slug'] = resolved_slug
                logger.info(
                    f"[REQUIREMENTS TOOL] ITR-45: Resolved ai_model '{ai_model}' → "
                    f"ai_model_slug '{resolved_slug}'"
                )
            else:
                logger.warning(
                    f"[REQUIREMENTS TOOL] ITR-45: Could not resolve ai_model '{ai_model}' "
                    f"to an API slug. Downstream agents may use inconsistent slugs."
                )
        except Exception as _mr_err:
            logger.debug(f"[REQUIREMENTS TOOL] Model resolver failed: {_mr_err}")

    # Case 7 (ITR-32 SS-4): Synthesize structured integrations[] from tech_stack.
    #
    # Root cause: The 09 improvements correctly removed SDK_ACTION_MAP and made
    # _build_integration_section() manifest-driven (reads manifest.integrations[]).
    # But _normalize_manifest_schema() never populated integrations[] from
    # tech_stack entries like {"email": "Resend"}. This closes the produce/consume gap.
    #
    # Schema expected by _build_integration_section():
    #   {"name": "Resend", "type": "email", "env_var": "RESEND_API_KEY"}

    # Keys that are NOT SDK integrations (infrastructure/framework — no API key needed)
    _NON_SDK_KEYS = {"framework", "deployment", "hosting", "css", "styling", "language",
                     "runtime", "bundler", "testing", "ci", "cd", "database", "orm"}

    import re  # local import — follows pattern at L793, L874 in this file

    # Known provider extraction patterns: "X via Provider" → Provider
    _VIA_PATTERN = re.compile(r'(?:via|through|using)\s+(\w+)', re.IGNORECASE)

    # Known multi-service patterns: "A + B" or "A and B"
    _MULTI_PATTERN = re.compile(r'\s*[+&]\s*|\s+and\s+', re.IGNORECASE)

    tech_stack = result.get("tech_stack", {})
    if isinstance(tech_stack, dict):
        # Collect existing integration names to prevent duplicates
        existing_integrations = result.get("integrations", [])
        if not isinstance(existing_integrations, list):
            # integrations is a dict (LLM variant) — don't try to append
            existing_integrations = None

        if existing_integrations is not None:
            existing_names = {
                i.get("name", "").lower()
                for i in existing_integrations
                if isinstance(i, dict)
            }

            for ts_key, ts_value in tech_stack.items():
                if not isinstance(ts_value, str) or not ts_value.strip():
                    continue
                if ts_key.lower() in _NON_SDK_KEYS:
                    continue

                # Extract service names from the value
                services = []

                # Check for "X via Provider" pattern → extract Provider
                via_match = _VIA_PATTERN.search(ts_value)
                if via_match:
                    services.append(via_match.group(1).strip())

                # Check for multi-service "A + B" or "A and B"
                parts = _MULTI_PATTERN.split(ts_value)
                for part in parts:
                    # Clean up: remove "API" suffix, "via ..." suffix
                    clean = re.sub(r'\s+API$', '', part.strip())
                    clean = re.sub(r'\s+via\s+.*$', '', clean, flags=re.IGNORECASE)
                    clean = clean.strip()
                    # Take first word(s) as service name (e.g., "Google Places" → "Google Places")
                    # But skip if it's a model name (contains version numbers at start)
                    if clean and not re.match(r'^[\d.]', clean):
                        # Only take the service name — first significant word(s)
                        # For "Perplexity" → "Perplexity", for "Google Places" → "Google Places"
                        # Remove trailing generic words
                        svc = re.sub(r'\s+(Services?|Platform|Cloud)$', '', clean, flags=re.IGNORECASE)
                        if svc and svc.lower() not in existing_names:
                            services.append(svc)

                # Deduplicate while preserving order
                seen = set()
                unique_services = []
                for svc in services:
                    key = svc.lower()
                    if key not in seen and key not in existing_names:
                        seen.add(key)
                        unique_services.append(svc)
                        existing_names.add(key)

                for svc_name in unique_services:
                    # Derive env_var: strip spaces, uppercase, add _API_KEY
                    env_key = re.sub(r'[^a-zA-Z0-9]', '_', svc_name).upper()
                    env_var = f"{env_key}_API_KEY"

                    integration_obj = {
                        "name": svc_name,
                        "type": ts_key.lower(),
                        "env_var": env_var,
                        "_source": "tech_stack_synthesis",
                    }
                    existing_integrations.append(integration_obj)
                    logger.info(
                        f"[REQUIREMENTS TOOL] ITR-32 SS-4: Synthesized integration "
                        f"from tech_stack.{ts_key}='{ts_value}' → "
                        f"{{name: '{svc_name}', type: '{ts_key}', env_var: '{env_var}'}}"
                    )

            if existing_integrations:
                result["integrations"] = existing_integrations

    return result

def _validate_manifest_model_name(manifest: dict, prompt: str) -> list:
    """Validate manifest ai_model against the original user prompt.

    ITR-30 SS-7 FIX: LLMs substitute model names from training data
    (e.g., 'Claude 3.5 Sonnet') when the prompt specifies a newer model
    (e.g., 'Claude Sonnet 4'). This L1 deterministic validator catches
    the mismatch.

    Universal patterns — works for ANY AI model family:
      - Claude (Anthropic)
      - GPT (OpenAI)
      - Gemini (Google)
      - Llama (Meta)

    Args:
        manifest: The content manifest dict (must have 'ai_model' key).
        prompt: The original user prompt text.

    Returns:
        List of warning strings. Empty if no issues found.
    """
    import re

    warnings = []

    manifest_model = manifest.get("ai_model", "")
    if not manifest_model or not isinstance(manifest_model, str):
        return warnings

    if not prompt or not isinstance(prompt, str):
        return warnings

    # Extract model references from the prompt
    # Matches: Claude Sonnet 4, Claude 3.5 Sonnet, GPT-4o, Gemini 2.0 Flash, etc.
    model_pattern = re.compile(
        r'\b((?:Claude|GPT|Gemini|Llama|Mistral|Command)'
        r'[\s\-]*(?:[\d]+\.?[\d]*\s*)?'
        r'(?:Sonnet|Opus|Haiku|Turbo|Flash|Pro|Ultra|Mini|Nano)?'
        r'(?:\s*[\d]+\.?[\d]*)?)\b',
        re.IGNORECASE,
    )

    prompt_models = model_pattern.findall(prompt)
    if not prompt_models:
        return warnings  # No model mentioned in prompt — nothing to validate

    # Normalize for comparison: lowercase, strip whitespace
    manifest_norm = manifest_model.lower().strip()

    for prompt_model in prompt_models:
        prompt_norm = prompt_model.lower().strip()

        # Check if prompt model is NOT contained in manifest model
        # and manifest model is NOT contained in prompt model
        if prompt_norm not in manifest_norm and manifest_norm not in prompt_norm:
            warnings.append(
                f"⚠️ MODEL NAME MISMATCH: Manifest has ai_model='{manifest_model}' "
                f"but the user prompt specifies '{prompt_model}'. "
                f"The LLM likely substituted a stale model name from training data. "
                f"Update the manifest to match the prompt."
            )
            break  # One warning is sufficient

    return warnings


def _validate_models_for_llm_integrations(manifest: dict) -> list:
    """RCA-470 F-3: Validate that LLM integrations have a models section.

    Root cause: When the manifest has integrations with type='llm' but no
    'models' section (or models with empty verified_slug), the literal
    extraction pipeline is blind to model slugs. The code agent then
    substitutes stale model names from training data.

    Args:
        manifest: The content_manifest.json dict.

    Returns:
        List of warning strings. Empty if validation passes.
    """
    warnings = []
    integrations = manifest.get("integrations", [])

    # Check if any integration has type='llm'
    llm_integrations = []
    if isinstance(integrations, list):
        llm_integrations = [
            i for i in integrations
            if isinstance(i, dict) and i.get("type", "").lower() == "llm"
        ]
    elif isinstance(integrations, dict):
        llm_integrations = [
            v for v in integrations.values()
            if isinstance(v, dict) and v.get("type", "").lower() == "llm"
        ]

    if not llm_integrations:
        return warnings  # No LLM integrations — nothing to validate

    # Check for models section
    models = manifest.get("models", [])
    if not models:
        llm_names = [i.get("name", "unknown") for i in llm_integrations]
        warnings.append(
            f"⚠️ MODEL SLUG MISSING: Manifest has LLM integration(s) "
            f"({', '.join(llm_names)}) but NO 'models' section. "
            f"Add a 'models' array with 'verified_slug' for each AI model. "
            f"Without this, the code agent will substitute stale model names "
            f"from training data."
        )
        return warnings

    # Check that models have non-empty verified_slug
    if isinstance(models, list):
        for model in models:
            if isinstance(model, dict):
                slug = model.get("verified_slug", "")
                if not slug or not slug.strip():
                    warnings.append(
                        f"⚠️ MODEL SLUG EMPTY: Model '{model.get('marketing_name', 'unknown')}' "
                        f"has an empty verified_slug. The researcher must resolve "
                        f"the correct API slug (e.g., 'anthropic/claude-sonnet-4')."
                    )

    return warnings

def _enrich_tech_stack_from_research(manifest: dict, project_dir: str) -> dict:
    """FIX-8: Enrich manifest tech_stack with pinned versions from framework-research.md.

    Root cause: The content_manifest.json tech_stack contains bare names
    (e.g., {"framework": "Next.js"}) without version pins. The researcher
    pins exact versions in docs/framework-research.md during Phase 0.5,
    but those versions never flow back into the manifest. Downstream agents
    (architect, code) then use unpinned versions, causing drift.

    Fix: Parse the researcher's version table from framework-research.md
    and enrich tech_stack entries with the pinned versions. Only updates
    entries that don't already have a version number.

    Args:
        manifest: The content_manifest dict (mutated in-place)
        project_dir: Absolute path to the project directory

    Returns:
        The manifest dict (same reference, mutated)
    """
    research_path = os.path.join(project_dir, "docs", "framework-research.md")
    if not os.path.isfile(research_path):
        return manifest

    try:
        with open(research_path, "r", encoding="utf-8", errors="ignore") as f:
            research_text = f.read()
    except (IOError, OSError):
        return manifest

    if not research_text or len(research_text) < 50:
        return manifest

    # Parse version pins from the research doc.
    # Format 1: Markdown table row: | **Next.js** | `15.0.0` | Stable |
    # Format 2: Bullet list: - **Framework**: Next.js 14.2.15 (App Router)
    import re
    version_pins: dict = {}  # {"next.js": "15.0.0", "prisma": "6.0.0", ...}

    # Pattern 1: Table rows with backtick versions
    table_pattern = re.compile(
        r'\|\s*\*{0,2}([^|*]+?)\*{0,2}\s*\|\s*`([^`]+)`\s*\|',
        re.MULTILINE
    )
    for match in table_pattern.finditer(research_text):
        name = match.group(1).strip()
        version = match.group(2).strip()
        if name and version and re.match(r'[\d^~]', version):
            version_pins[name.lower()] = version

    # Pattern 2: Bullet list "- **Framework**: Next.js 14.2.15"
    bullet_pattern = re.compile(
        r'-\s*\*{2}[^*]+\*{2}:\s*([\w.\s-]+?)\s+(\d+\.\d+[\d.]*)\b',
        re.MULTILINE
    )
    for match in bullet_pattern.finditer(research_text):
        name = match.group(1).strip()
        version = match.group(2).strip()
        if name and version:
            key = name.lower().rstrip()
            if key not in version_pins:  # Table takes priority
                version_pins[key] = version

    if not version_pins:
        return manifest

    # Enrich tech_stack
    tech_stack = manifest.get("tech_stack", {})
    if not tech_stack:
        return manifest

    enriched = False
    if isinstance(tech_stack, dict):
        for key, value in tech_stack.items():
            if not isinstance(value, str):
                continue
            # Skip if already has a version pinned
            if re.search(r'\d+\.\d+', value):
                continue
            # Try to find a matching version pin
            value_lower = value.lower().strip()
            for pin_name, pin_version in version_pins.items():
                if value_lower in pin_name or pin_name in value_lower:
                    tech_stack[key] = f"{value} {pin_version}"
                    enriched = True
                    logger.info(
                        f"[REQUIREMENTS TOOL] FIX-8: Pinned tech_stack.{key} "
                        f"'{value}' → '{value} {pin_version}' from framework-research.md"
                    )
                    break
    elif isinstance(tech_stack, list):
        new_stack = []
        for item in tech_stack:
            if not isinstance(item, str) or re.search(r'\d+\.\d+', item):
                new_stack.append(item)
                continue
            item_lower = item.lower().strip()
            matched = False
            for pin_name, pin_version in version_pins.items():
                if item_lower in pin_name or pin_name in item_lower:
                    new_stack.append(f"{item} {pin_version}")
                    enriched = True
                    matched = True
                    logger.info(
                        f"[REQUIREMENTS TOOL] FIX-8: Pinned tech_stack "
                        f"'{item}' → '{item} {pin_version}' from framework-research.md"
                    )
                    break
            if not matched:
                new_stack.append(item)
        if enriched:
            manifest["tech_stack"] = new_stack

    if enriched and isinstance(tech_stack, dict):
        manifest["tech_stack"] = tech_stack

    return manifest

def _normalize_seq(seq) -> str:
    """Normalize a phase seq to canonical format.

    ITR-30 P0 FIX (SS-2/SS-6): LLMs use semver format (2.3.0) but
    canonical phases use short format (2.3). This function strips
    trailing '.0' segments to normalize.

    Examples:
        "2.3.0" → "2.3"
        "1.0.0" → "1"
        "3.0.0" → "3"
        "0.5"   → "0.5"
        "2.3"   → "2.3"
        2.3     → "2.3"

    Args:
        seq: Phase sequence number (str or numeric).

    Returns:
        Normalized canonical string.
    """
    s = str(seq).strip()
    # Strip trailing .0 segments (2.3.0 → 2.3, 1.0.0 → 1)
    while s.endswith(".0"):
        s = s[:-2]
    return s

def _seq_less_than(a: str, b: str) -> bool:
    """Compare two phase sequence numbers: return True if a < b.

    F-6 FIX: Used by the monotonicity guard to determine predecessor
    relationships between phases. Uses _normalize_seq to handle
    semver-style sequences (e.g., "2.3.0" → "2.3").

    Args:
        a: First phase sequence (e.g., "2.3")
        b: Second phase sequence (e.g., "2.5")

    Returns:
        True if phase a comes before phase b in execution order.
    """
    try:
        return float(_normalize_seq(a)) < float(_normalize_seq(b))
    except (ValueError, TypeError):
        return False
