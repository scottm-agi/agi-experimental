"""
Delegation Brief Sections — Policy & Quality.

Group 2 of the delegation brief section builders, extracted from
delegation_brief_sections.py for maintainability.

Contains section builders related to:
  - Research documentation inline injection
  - Schema lock / type coherence mandates
  - Codebase state scanning
  - Infrastructure fast-pass directives
  - Error relay (cross-subordinate error history)
  - Gate failure details
  - Verification findings
  - Fidelity violation warnings
  - TDD mandates (phase-aware)
  - Acceptance criteria

All functions maintain their original signatures and behavior.
The parent module (delegation_brief_sections.py) re-exports everything
from this module so existing imports continue to work.
"""

from __future__ import annotations

import glob
import json
import logging
import os
from typing import Any, Optional, TYPE_CHECKING

from python.helpers.phase_category import (
    is_planning_phase,
    is_scaffold_phase,
    is_post_tdd_generation_phase,
)
from python.helpers.delegation_brief_context import ProjectContext
from python.helpers.output_truncation import truncate_output_middle_out
from python.helpers.delegation_brief_config import (
    PROFILE_CONTEXT_CONFIG, _DEFAULT_CONFIG,
    check_type_coherence,
)

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger(__name__)


def _build_research_docs_inline_section(
    project_dir: str,
    config: dict,
) -> str:
    """Build RESEARCH DOCS section — full content of researcher API docs."""
    if not config.get("research_docs_inline"):
        return ""
    docs_dir = os.path.join(project_dir, "docs")
    if not os.path.isdir(docs_dir):
        return ""
    research_files = sorted(glob.glob(os.path.join(docs_dir, "*research*.md")))
    if not research_files:
        return ""
    parts = []
    for fpath in research_files[:3]:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            if content.strip():
                fname = os.path.basename(fpath)
                # RCA-ITR51: Increased from 2000→4000 to preserve Compatibility
                # Matrix tables that contain version pins (researcher output
                # averaged 3800 bytes; 2000 cut off the version table).
                if len(content) > 4000:
                    content = truncate_output_middle_out(content, max_chars=4000, head_ratio=0.3)
                parts.append(
                    f"<!-- INCLUDED_DOCUMENT_START: {fname} -->\n"
                    f"#### {fname}\n{content}\n"
                    f"<!-- INCLUDED_DOCUMENT_END -->"
                )
        except (IOError, OSError):
            continue
    if not parts:
        return ""
    return (
        "### RESEARCHER API DOCS\n"
        "Use these EXACT endpoints, SDK patterns, version pins, and env var names:\n\n"
        + "\n\n".join(parts)
    )

def _build_schema_lock_section(
    project_dir: str,
    config: dict,
) -> str:
    """Build SCHEMA LOCK section — mandates type name reuse (SS-4).

    SS-4 (P0, Type/Schema Coherence): Multi-wave code delegations independently
    introduce conflicting type names (Prospect, Lead, Business for the same
    concept) → 84 TypeScript errors. This section scans existing types and
    injects a prescriptive mandate listing exact names code agents MUST reuse.

    Args:
        project_dir: Absolute path to the project directory.
        config: Profile context config dict.

    Returns:
        Formatted schema lock section, or empty string if disabled/no types.
    """
    if not config.get("codebase_state"):
        return ""

    if not project_dir:
        return ""

    if check_type_coherence is None:
        return ""

    try:
        result = check_type_coherence(project_dir)
    except Exception:
        return ""  # Graceful fallback — don't crash delegation

    types_found = result.get("types_found", [])
    if not types_found:
        return ""

    # Build prescriptive schema lock
    type_list = ", ".join(f"`{t}`" for t in sorted(types_found)[:15])
    issues = result.get("issues", [])

    lines = [
        "### 🔒 SCHEMA LOCK — MANDATORY TYPE REUSE (SS-4)",
        "",
        "The following TypeScript types/interfaces ALREADY EXIST in this project.",
        f"You MUST reuse these exact names — do NOT create alternatives:",
        "",
        f"**Locked types:** {type_list}",
        "",
        "⚠️ Creating a new type that duplicates or conflicts with these ",
        "(e.g., `Lead` vs `Business` for the same entity) is a BLOCKING ERROR ",
        "that will cause 84+ TypeScript build failures.",
    ]

    if issues:
        lines.append("")
        lines.append("**Known coherence issues (fix these):**")
        for issue in issues[:5]:
            lines.append(f"  - {issue}")

    return "\n".join(lines)

def _build_codebase_state_section(
    project_dir: str,
    config: dict,
) -> str:
    """Build CODEBASE section — exported types, models, existing files."""
    if not config.get("codebase_state"):
        return ""
    try:
        from python.helpers.codebase_state_injector import scan_project_state, format_codebase_manifest
        state = scan_project_state(project_dir)
        manifest = format_codebase_manifest(state)
        if manifest:
            return f"### CODEBASE STATE\n{manifest}"
    except Exception as e:
        logger.debug(f"Codebase state section skipped: {e}")
    return ""

def _build_infrastructure_fast_pass_section(
    project_dir: str,
    profile: str,
    config: dict,
) -> str:
    """Build INFRASTRUCTURE FAST-PASS directive when infra is already verified.

    ITR-44 RCA: Each new code agent delegation re-does infrastructure setup
    (npm install, Prisma config, build verification) that was already completed
    by previous agents, wasting 10-15 iterations per delegation.

    This section detects existing infrastructure and injects a directive telling
    the agent to SKIP setup and START with feature implementation immediately.

    Infrastructure is considered "verified" when ALL of:
      - package.json exists
      - node_modules/ exists (deps installed)
      - At least one src/ file exists (scaffold done)
    """
    if not config.get("infrastructure_fast_pass"):
        return ""

    if not project_dir or not os.path.isdir(project_dir):
        return ""

    # Check for the minimum infrastructure markers
    has_pkg = os.path.isfile(os.path.join(project_dir, "package.json"))
    has_node_modules = os.path.isdir(os.path.join(project_dir, "node_modules"))

    if not has_pkg or not has_node_modules:
        return ""

    # Check for src files (scaffold is done)
    src_dir = os.path.join(project_dir, "src")
    has_src = os.path.isdir(src_dir) and any(
        f.endswith((".ts", ".tsx", ".js", ".jsx"))
        for f in os.listdir(os.path.join(src_dir, "app"))
    ) if os.path.isdir(os.path.join(src_dir, "app")) else False

    if not has_src:
        return ""

    # Detect optional infrastructure components for the report
    has_prisma = os.path.isfile(os.path.join(project_dir, "prisma", "schema.prisma"))
    has_tsconfig = os.path.isfile(os.path.join(project_dir, "tsconfig.json"))
    has_tailwind = any(
        os.path.isfile(os.path.join(project_dir, f))
        for f in ("tailwind.config.ts", "tailwind.config.js", "tailwind.config.mjs")
    )

    # Build the verified inventory
    verified = ["package.json ✅", "node_modules/ ✅ (dependencies installed)"]
    if has_prisma:
        verified.append("Prisma schema ✅ (prisma/schema.prisma exists)")
    if has_tsconfig:
        verified.append("tsconfig.json ✅")
    if has_tailwind:
        verified.append("Tailwind CSS ✅ (config exists)")

    inventory = "\n".join(f"  - {v}" for v in verified)

    return (
        "### 🟢 INFRASTRUCTURE VERIFIED — DO NOT RE-SETUP\n\n"
        "The following infrastructure is already installed and configured:\n"
        f"{inventory}\n\n"
        "**DO NOT** perform any of the following (they are ALREADY DONE):\n"
        "  - Do NOT run `npm install` or `npx create-next-app`\n"
        "  - Do NOT reconfigure Prisma, Tailwind, or TypeScript\n"
        "  - Do NOT re-run `npm run build` to verify infrastructure\n"
        "  - Do NOT read/modify package.json, tsconfig.json, or config files\n\n"
        "**START IMMEDIATELY** with feature implementation:\n"
        "  - Create API routes in `src/app/api/`\n"
        "  - Create pages in `src/app/`\n"
        "  - Create components in `src/components/`\n"
        "  - Create lib modules in `src/lib/`\n"
        "  - Write the actual business logic for the assigned requirements\n"
    )

def _build_error_relay_section(
    agent_data: dict,
    project_dir: str,
    config: dict,
) -> str:
    """Build ERRORS section — cross-subordinate error history."""
    if not config.get("error_relay"):
        return ""

    parts = []

    # In-memory error log
    try:
        from python.helpers.subordinate_error_relay import build_error_injection
        error_injection = build_error_injection(agent_data)
        if error_injection:
            parts.append(error_injection.strip())
    except Exception:
        pass

    # File-based error log
    if project_dir:
        log_path = os.path.join(project_dir, "memory-bank", "delegation-error-log.md")
        if os.path.isfile(log_path):
            try:
                with open(log_path, "r") as f:
                    file_content = f.read()
                if file_content.strip():
                    if len(file_content) > 2000:
                        file_content = truncate_output_middle_out(file_content, max_chars=2000, head_ratio=0.1)
                    parts.append(f"Persistent Error Log:\n{file_content}")
            except (IOError, OSError):
                pass

    if not parts:
        return ""
    return "### ⚠️ PREVIOUS ERRORS\n" + "\n\n".join(parts)

def _build_gate_failure_section(
    agent_data: dict,
    config: dict,
) -> str:
    """Build GATE FAILURES section — previous gate block details.

    D-2: When block_message is a JSON string, parse it for structured fields
    (summary, files_affected, specific_errors) and format them as readable
    sections. Falls back to plain text for non-JSON messages.
    """
    if not config.get("gate_failures"):
        return ""
    block_details = agent_data.get("_last_gate_block_details")
    if not block_details:
        return ""

    check_name = block_details.get("check_name", "unknown")
    block_message = block_details.get("block_message", "")
    block_count = block_details.get("block_count", 1)
    gate_name = block_details.get("gate", "")

    # D-2: Try to parse block_message as JSON for structured fields
    structured = None
    if block_message and isinstance(block_message, str):
        try:
            import json
            parsed = json.loads(block_message)
            if isinstance(parsed, dict):
                structured = parsed
        except (json.JSONDecodeError, ValueError):
            pass  # Not JSON — use as plain text

    # Use summary from structured data, or raw block_message
    display_message = (structured.get("summary", block_message) if structured else block_message)

    gate_label = f" [{gate_name.upper()} GATE]" if gate_name else ""
    lines = [
        f"### ⚠️ PREVIOUS GATE FAILURE{gate_label} — ADAPT YOUR APPROACH",
        f"Check Failed: {check_name}",
        f'Block Message: "{display_message}"',
        f"Attempt: {block_count}",
    ]

    # D-2: Append structured details if available
    if structured:
        files_affected = structured.get("files_affected", [])
        if files_affected:
            lines.append("")
            lines.append("Files affected:")
            for f in files_affected:
                lines.append(f"  - {f}")

        specific_errors = structured.get("specific_errors", [])
        if specific_errors:
            lines.append("")
            lines.append("Errors:")
            for err in specific_errors:
                if isinstance(err, dict):
                    parts = []
                    if err.get("path"):
                        parts.append(err["path"])
                    if err.get("file"):
                        parts.append(f"in {err['file']}")
                    if err.get("line"):
                        parts.append(f"L{err['line']}")
                    lines.append(f"  - {' '.join(parts)}" if parts else f"  - {err}")
                else:
                    lines.append(f"  - {err}")

    history = agent_data.get("_gate_block_history", [])
    if history and len(history) > 1:
        lines.append("")
        lines.append("Previous failure trajectory:")
        for i, entry in enumerate(history, 1):
            lines.append(f"  #{i}: [{entry.get('check', '?')}] {entry.get('summary', '?')}")

    lines.append("")
    lines.append("🔴 DO NOT repeat previous approaches. Fix the ROOT CAUSE.")
    return "\n".join(lines)

def _build_verification_section(
    agent_data: dict,
    config: dict,
) -> str:
    """Build VERIFICATION section — E2E findings (only when passed=False).

    Side effect: clears _quality_evaluation after reading to prevent stale re-injection.
    """
    if not config.get("verification_findings"):
        return ""
    quality_eval = agent_data.get("_quality_evaluation")
    if not quality_eval or quality_eval.get("passed", True):
        return ""

    source = quality_eval.get("source", "verification_agent")
    verdict = quality_eval.get("verdict", "FAIL")
    issues = quality_eval.get("issues", [])
    response_text = quality_eval.get("response", "")

    lines = [
        "### 🔍 VERIFICATION FINDINGS — FIX THESE",
        f"Source: {source} | Verdict: {verdict}",
    ]

    if isinstance(issues, list) and issues:
        lines.append("")
        for i, issue in enumerate(issues, 1):
            lines.append(f"  {i}. {issue}")
    elif isinstance(issues, str) and issues:
        lines.append(f"Issues: {issues}")
    elif response_text:
        lines.append(f"Response: {response_text[:300]}")

    lines.append("")
    lines.append("🔴 Fix the ROOT CAUSE. The orchestrator will re-run verification.")

    # Clear after reading to prevent stale re-injection
    del agent_data["_quality_evaluation"]

    return "\n".join(lines)

def _build_fidelity_section(
    agent_data: dict,
    config: dict,
) -> str:
    """Build FIDELITY section — manifest value substitution warnings."""
    if not config.get("fidelity_violations"):
        return ""
    violations = agent_data.get("_pending_fidelity_violations", [])
    if not violations:
        return ""

    lines = ["### ⚠️ FIDELITY VIOLATIONS — USE EXACT VALUES"]
    for v in violations:
        vtype = v.get("type", "unknown")
        if vtype == "substitution":
            lines.append(
                f"  🔴 Expected '{v.get('expected_value', '?')}' "
                f"but found '{v.get('found_value', '?')}'"
            )
        elif vtype == "missing_url":
            lines.append(f"  🔴 MISSING URL: {v.get('url', '?')}")
        elif vtype == "missing_price":
            lines.append(f"  🔴 MISSING PRICE: {v.get('price', '?')}")
        else:
            lines.append(f"  🔴 {vtype.upper()}: {v.get('detail', '?')}")
    lines.append("Do NOT use substituted values. Use the EXACT values from the manifest.")
    return "\n".join(lines)

def _build_manifest_values_section(
    agent_data: dict,
    phase_id: str = "",
) -> str:
    """Build manifest values section for delegation briefs.

    F-8 (RCA-461): Injects critical values from the original prompt
    (brand names, URLs, prices, contact info) into delegation briefs
    so code agents see them BEFORE writing code, not just at Phase 5
    validation.

    Unlike _build_fidelity_section() which requires _pending_fidelity_violations
    (only populated after Phase 5 verification), this section reads from
    _content_manifest which is populated during Phase 0 planning and
    available for all subsequent phases.

    Args:
        agent_data: The agent.data dict with manifest/prompt data.
        phase_id: Current phase ID for filtering relevant values.

    Returns:
        Formatted markdown section string, or '' if no manifest values.
    """
    manifest = agent_data.get("_content_manifest")
    if not manifest or not isinstance(manifest, dict):
        return ""

    # Collect all non-empty manifest entries
    lines: list[str] = []

    # Brand name
    brand = manifest.get("brand_name", "")
    if brand:
        lines.append(f"- **Brand Name**: `{brand}`")

    # Contact info
    contact = manifest.get("contact", "")
    if contact:
        lines.append(f"- **Contact**: `{contact}`")

    phone = manifest.get("phone", "")
    if phone:
        lines.append(f"- **Phone**: `{phone}`")

    # URLs
    urls = manifest.get("urls", [])
    if isinstance(urls, list):
        for url in urls:
            if url:
                lines.append(f"- **URL**: `{url}`")
    elif isinstance(urls, dict):
        for label, url in urls.items():
            if url:
                lines.append(f"- **{label}**: `{url}`")

    # Prices
    prices = manifest.get("prices", [])
    if isinstance(prices, list):
        for price in prices:
            if price:
                lines.append(f"- **Price**: `{price}`")

    # Catch-all: surface any other string values not already handled
    _KNOWN_KEYS = {"brand_name", "contact", "phone", "urls", "prices"}
    for key, value in manifest.items():
        if key in _KNOWN_KEYS:
            continue
        if isinstance(value, str) and value.strip():
            display_key = key.replace("_", " ").title()
            lines.append(f"- **{display_key}**: `{value}`")

    if not lines:
        return ""

    header = (
        "## \U0001f4cc MANIFEST VALUES \u2014 USE EXACTLY (F-8)\n\n"
        "The following values come from the original user prompt.\n"
        "You MUST use these EXACT strings in your code \u2014 do NOT paraphrase,\n"
        "abbreviate, or substitute.\n"
    )

    return header + "\n".join(lines)

def _build_tdd_section(
    agent_data: dict,
    kwargs: dict,
    config: dict,
    ctx: ProjectContext,
    project_dir: str = "",
    *,
    phase: int | None = None,
) -> str:
    """Build TDD section — architect test specs or generic mandate.

    RCA-ITR42 RC-2: Now phase-aware. During Phase 1 (scaffold), injects
    infrastructure test specs from _DELIVERY_STANDARDS instead of the
    generic "Write Tests FIRST" mandate that caused scaffold-verification
    spirals (77% wasted iterations in MainStreet).

    FIX-9: Also injects docs/tdd/ directory references if present.

    RCA-ITR55 F1: Phase >= 3 awareness. When docs/tdd/ exists (Phase 2.8
    completed), tells the agent to "Make Existing Tests PASS" instead of
    "Write Tests FIRST" + "Reference ONLY" which caused agents to rewrite
    the generated test files, destroying assertions.
    """
    if not config.get("tdd_mandate"):
        return ""

    test_specs = agent_data.get("_test_specs", []) or kwargs.get("bdd_specs", [])

    # ── RCA-ITR42 RC-2: Phase 1 infra test injection ──
    # When phase=1 and no explicit specs provided, inject infrastructure
    # delivery standards as test specs. These tell the agent to test
    # INFRASTRUCTURE (build, config, scaffold cleanup) not CONTENT.
    if phase is not None and (is_planning_phase(phase) or is_scaffold_phase(phase)) and not test_specs:
        try:
            from python.helpers.skeleton_generator import _DELIVERY_STANDARDS
            infra_standards = [
                s for s in _DELIVERY_STANDARDS
                if s.get("category") in ("infra", "scaffold_cleanup", "config")
            ]
            if infra_standards:
                lines = [
                    "## 🧪 TDD MANDATE — INFRASTRUCTURE Tests Only (Phase 1)\n",
                    "This is Phase 1 (Scaffold). Test INFRASTRUCTURE, not features.\n",
                    "**What to test:**\n",
                ]
                for std in infra_standards:
                    req_id = std.get("req_id", "REQ-INFRA")
                    suggested = std.get("suggested_test", std.get("text", ""))
                    lines.append(f"- [{req_id}] {suggested}")
                lines.append("\n**What NOT to test:**")
                lines.append("- ❌ Page content, copy, or visual styling")
                lines.append("- ❌ Feature logic (authentication, API routes)")
                lines.append("- ❌ Component rendering with design tokens")
                lines.append("\n**Infrastructure test workflow:**")
                lines.append("1. Write a test that `npm run build` exits 0")
                lines.append("2. Write a test that scaffold boilerplate is removed")
                lines.append("3. Write a test that tsconfig paths resolve")
                lines.append("4. Run tests → see them fail (Red)")
                lines.append("5. Fix scaffold to make them pass (Green)")
                return "\n".join(lines)
        except ImportError:
            pass  # Fall through to generic mandate

    # ── RCA-ITR55 F1: Phase >= 3 "Make Existing Tests PASS" mode ──
    # When Phase 2.8 already generated test files in docs/tdd/ and they've
    # been wired to src/__tests__/, the code agent must MAKE THEM PASS —
    # not rewrite them. The SKILL.md says "make these tests PASS — not to
    # write new tests from scratch" but the old brief said "Reference ONLY"
    # + "Write Tests FIRST" which caused 6/9 test files to be rewritten.
    if phase is not None and is_post_tdd_generation_phase(phase) and config.get("tdd_stub_wiring") and project_dir:
        # AF-4 fix: Read from src/__tests__/ directly (the executable test location)
        # NOT from docs/tdd/ (which uses .tdd.ts extension and is not runnable).
        test_dirs = [
            os.path.join(project_dir, "src", "__tests__"),
            os.path.join(project_dir, "__tests__"),
            os.path.join(project_dir, "tests"),
        ]
        for test_dir in test_dirs:
            if os.path.isdir(test_dir):
                spec_files = [f for f in os.listdir(test_dir)
                              if f.endswith((".test.ts", ".test.tsx", ".test.js", ".test.jsx", ".spec.ts", ".spec.tsx"))
                              or (f.startswith("test_") and f.endswith(".py"))]
                if spec_files:
                    # Compute relative test dir for display
                    rel_test_dir = os.path.relpath(test_dir, project_dir)
                    # Build the "existing tests" mandate — NO "Write Tests FIRST"
                    lines = [
                        "## 🧪 TDD — Make Existing Tests PASS (Phase 3)\n",
                        f"Test files already exist in `{rel_test_dir}/`. Phase 2.8 generated these",
                        "with real assertions (including `expect().toContain()` for prompt literals).",
                        "Your job: **write production code that makes ALL existing tests pass.**\n",
                        "### ⛔ DO NOT modify, rewrite, or replace any test files",
                        "- DO NOT create new test files to replace the existing ones",
                        "- DO NOT change assertions to match your code — change your CODE to match the assertions",
                        "- DO NOT delete or skip tests for features you haven't implemented yet\n",
                        "### ✅ What to do",
                        "1. Run `npx vitest run` (or equivalent) to see which tests FAIL",
                        "2. Read each failing test — it tells you EXACTLY what to implement",
                        "3. If a test expects `toContain('https://buy.stripe.com/...')`, add that URL to your code",
                        "4. If a test imports a module that doesn't exist, CREATE that module",
                        "5. Run tests again after each implementation — track RED → GREEN progress",
                        "6. Report final pass/fail counts in your response\n",
                        "### 📂 Test files to make pass:\n",
                    ]
                    for sf in sorted(spec_files)[:15]:
                        lines.append(f"- `{rel_test_dir}/{sf}`")
                    return "\n".join(lines)
                break  # Found the test dir but no files — fall through
    try:
        from python.helpers.delegation_message import build_tdd_mandate, enrich_tdd_mandate_with_api_checks
        mandate = build_tdd_mandate(test_specs)
        if ctx.manifest:
            mandate = enrich_tdd_mandate_with_api_checks(mandate, ctx.manifest)
    except ImportError:
        # Fallback if delegation_message not available
        if test_specs:
            lines = ["### 🧪 TDD MANDATE — Write Tests FIRST"]
            for spec in test_specs:
                lines.append(f"- `{spec.get('test_file', 'unknown')}`: {', '.join(spec.get('descriptions', []))}")
            mandate = "\n".join(lines)
        else:
            mandate = (
                "### 🧪 TDD MANDATE — Write Tests FIRST\n"
                "Create test files BEFORE implementation code. Run tests to verify."
            )

    # FIX-9 + F-3 (TDD spec wiring): Inject docs/tdd/ directory as reference specs
    # ITR-26→ITR-52 Fix: Removed "stubs = PRIMARY DELIVERABLE" heading that caused
    # LLMs to treat stubs as the goal. Now frames specs as reference-only input.
    # NOTE: This block only runs for phases < 3 (or when phase is None) because
    # Phase >= 3 returns early above with the "Make Existing Tests PASS" mandate.
    if config.get("tdd_stub_wiring") and project_dir:
        tdd_dir = os.path.join(project_dir, "docs", "tdd")
        if os.path.isdir(tdd_dir):
            spec_files = [f for f in os.listdir(tdd_dir)
                          if f.endswith((".ts", ".tsx", ".js", ".jsx", ".py",
                                        ".tdd.ts", ".tdd.tsx", ".tdd.py"))]  # AF-4: include .tdd extensions
            if spec_files:
                mandate += (
                    f"\n\n### 📂 Test Specifications — IMMUTABLE — DO NOT MODIFY\n"
                    f"Test specs exist at `docs/tdd/` — these define WHAT to test.\n"
                    f"Read each spec, then write REAL test files with REAL assertions\n"
                    f"and REAL production code that makes them pass:\n\n"
                )
                for sf in sorted(spec_files)[:15]:  # Cap at 15 files
                    mandate += f"- `docs/tdd/{sf}`\n"
                mandate += (
                    "\n1. Read BDD THEN clauses for each REQ-ID in docs/bdd-scenarios.md\n"
                    "2. Write real test files with real assertions (NOT placeholders)\n"
                    "3. Run tests — verify they FAIL (Red phase)\n"
                    "4. Write production code to make tests pass (Green phase)"
                )

    return mandate.strip() if mandate else ""

def _build_acceptance_section(
    agent_data: dict,
    kwargs: dict,
    config: dict,
) -> str:
    """Build ACCEPTANCE section — success criteria per REQ-ID."""
    if not config.get("acceptance_criteria"):
        return ""
    requirement_ids = kwargs.get("requirement_ids", [])
    if not requirement_ids:
        return ""
    ledger = agent_data.get("_requirements_ledger")
    if not ledger or not isinstance(ledger, dict):
        return ""
    requirements = ledger.get("requirements", [])
    if not requirements:
        return ""

    try:
        from python.helpers.req_id_normalizer import build_normalized_req_map
        req_map = build_normalized_req_map(requirements)
    except ImportError:
        req_map = {r.get("id", ""): r for r in requirements if isinstance(r, dict)}

    blocks = []
    for req_id in requirement_ids:
        req = req_map.get(req_id)
        if not req:
            continue
        block_lines = [f"**{req_id}** [{req.get('category', 'feature')}]: {req.get('text', '')}"]
        criteria = req.get("success_criteria", [])
        if criteria:
            for c in criteria:
                block_lines.append(f"  - {c}")
        blocks.append("\n".join(block_lines))

    if not blocks:
        return ""
    return (
        "### ACCEPTANCE CRITERIA\n"
        "The following MUST be fully implemented:\n\n"
        + "\n\n".join(blocks)
    )
