"""
Delegation Brief Sections — Ops & Scaffolding.

Group 3 of the delegation brief section builders, extracted from
delegation_brief_sections.py for maintainability.

Contains section builders related to:
  - ORM detection and database seed mandates
  - TypeScript compilation mandates
  - Scaffold phase mandates (Phase 1 minimalism / Phase 3+ cleanup)
  - Tool availability sections
  - Dev server URL discovery
  - Budget & tracking sections
  - Skill reference sections
  - Auth prohibition sections
  - Framework detection and pitfall warnings
  - Domain context injection
  - Compliance requirement classification

All functions maintain their original signatures and behavior.
The parent module (delegation_brief_sections.py) re-exports everything
from this module so existing imports continue to work.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional, TYPE_CHECKING

from python.helpers.phase_category import (
    is_scaffold_phase,
    is_post_tdd_generation_phase,
)
from python.helpers.delegation_brief_context import ProjectContext
from python.helpers.delegation_brief_config import (
    PROFILE_CONTEXT_CONFIG, _DEFAULT_CONFIG,
    _PROFILE_TOOLS, _AUTH_CATEGORIES, _AUTH_KEYWORDS,
    _MAX_DOMAIN_CONTEXT_CHARS, _FRAMEWORK_PITFALL_RULES,
)

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger(__name__)


def detect_orm_and_build_seed_mandate(project_dir: str) -> Optional[str]:
    """Detect ORM framework and return database seed mandate text.

    Checks for Prisma, Drizzle, or Sequelize indicators in the project
    directory. Returns a formatted mandate string instructing the code
    agent to create seed data, or None if no ORM is detected.

    Detection logic:
    1. Prisma: ``prisma/schema.prisma`` file exists
    2. Drizzle: ``drizzle.config.*`` file exists (.ts, .js, .mjs, .mts)
    3. Sequelize: ``sequelize`` in package.json dependencies

    Args:
        project_dir: Absolute path to the project directory.

    Returns:
        Formatted mandate string, or None if no ORM detected.
    """
    if not project_dir or not os.path.isdir(project_dir):
        return None

    orm_name: Optional[str] = None
    seed_cmd: str = ""
    seed_file: str = ""

    # 1. Prisma detection
    prisma_schema = os.path.join(project_dir, "prisma", "schema.prisma")
    if os.path.isfile(prisma_schema):
        orm_name = "Prisma"
        seed_cmd = "npx prisma db seed"
        seed_file = "prisma/seed.ts"

    # 2. Drizzle detection (multiple config extensions)
    if not orm_name:
        for ext in ("ts", "js", "mjs", "mts"):
            drizzle_config = os.path.join(project_dir, f"drizzle.config.{ext}")
            if os.path.isfile(drizzle_config):
                orm_name = "Drizzle"
                seed_cmd = "npx tsx src/db/seed.ts"
                seed_file = "src/db/seed.ts"
                break

    # 3. Sequelize detection (package.json dependencies)
    if not orm_name:
        pkg_path = os.path.join(project_dir, "package.json")
        if os.path.isfile(pkg_path):
            try:
                with open(pkg_path, "r", encoding="utf-8") as f:
                    pkg_data = json.load(f)
                if isinstance(pkg_data, dict):
                    deps = pkg_data.get("dependencies", {})
                    dev_deps = pkg_data.get("devDependencies", {})
                    all_deps = {}
                    if isinstance(deps, dict):
                        all_deps.update(deps)
                    if isinstance(dev_deps, dict):
                        all_deps.update(dev_deps)
                    if "sequelize" in all_deps:
                        orm_name = "Sequelize"
                        seed_cmd = "npx sequelize-cli db:seed:all"
                        seed_file = "seeders/"
            except (json.JSONDecodeError, IOError, OSError):
                pass

    if not orm_name:
        return None

    return (
        f"### \U0001f331 Database Seed Mandate\n"
        f"This project uses **{orm_name}**. You MUST:\n"
        f"1. Create a seed script (e.g., `{seed_file}`) with realistic mock data\n"
        f"2. Execute the seed command (e.g., `{seed_cmd}`)\n"
        f"3. Verify the database has data BEFORE marking implementation complete\n\n"
        f"Without seed data, verification will fail with empty-database errors."
    )

def _build_database_seed_mandate_section(
    project_dir: str,
    config: dict,
) -> str:
    """Build database seed mandate section (Fix-9).

    Only fires when:
    1. Profile has database_seed_mandate=True (currently: code)
    2. Project has an ORM (Prisma/Drizzle/Sequelize)
    """
    if not config.get("database_seed_mandate"):
        return ""
    if not project_dir or not os.path.isdir(project_dir):
        return ""
    mandate = detect_orm_and_build_seed_mandate(project_dir)
    return mandate if mandate else ""

def _build_tsc_mandate_section(
    project_dir: str,
    config: dict,
) -> str:
    """Build TypeScript compilation mandate section (FIX-3).

    Would have prevented 6/18 ITR-356 symptoms. Only fires when:
    1. Profile has tsc_mandate=True (currently: code)
    2. Project has tsconfig.json (i.e., it's a TypeScript project)
    """
    if not config.get("tsc_mandate"):
        return ""

    tsconfig_path = os.path.join(project_dir, "tsconfig.json")
    if not os.path.isfile(tsconfig_path):
        return ""

    return (
        "### 🔴 TYPESCRIPT COMPILATION MANDATE\n"
        "This project uses TypeScript (`tsconfig.json` detected).\n"
        "After implementing ANY code changes:\n"
        "1. Run `npx tsc --noEmit` to check for type errors\n"
        "2. Fix ALL errors before reporting completion\n"
        "3. Do NOT skip this step — type errors indicate broken code\n\n"
        "Common issues to watch for:\n"
        "- Missing `'use client'` directive on components using React hooks\n"
        "- Wrong import paths (case sensitivity matters)\n"
        "- Missing type definitions for third-party packages\n"
        "- Mismatched prop types between parent and child components"
    )

def _build_scaffold_mandate_section(
    kwargs: dict,
    config: dict,
    *,
    phase: int | None = None,
    message: str = "",
) -> str:
    """Build Phase 1 scaffold minimalism mandate (FIX-4, RC-1 fix).

    Prevents code agents from building styled marketing pages during
    Phase 1 (scaffold). Phase 1 should produce framework boilerplate
    ONLY — design comes from Phase 2.3 (frontend designer agent).

    RCA-ITR42 RC-1: Previously read kwargs._current_phase which was NEVER
    populated (it lives in agent.data). Now accepts phase param directly
    from build_delegation_package, with message-text fallback.
    """
    if not config.get("scaffold_minimal"):
        return ""

    # ── Phase detection (RCA-ITR42 RC-1 fix) ──
    # Priority 1: Explicit phase param (from build_delegation_package)
    # Priority 2: Message-text regex fallback (backward compat)
    import re
    if phase is not None:
        is_scaffold = is_scaffold_phase(phase)
    elif message:
        is_scaffold = bool(re.search(r'phase\s*1|scaffold', message, re.IGNORECASE))
    else:
        is_scaffold = False

    if not is_scaffold:
        # Phase 3+ → scaffold cleanup (replace markers left by Phase 1)
        if phase is not None and is_post_tdd_generation_phase(phase):
            return (
                "### 🏗️ SCAFFOLD CLEANUP\n"
                "Phase 1 created scaffold files with `// SCAFFOLD: Replace in Phase 3` markers.\n"
                "Find and REPLACE every scaffold marker with real implementation code.\n"
                "Do NOT leave any `SCAFFOLD`, `TODO`, or placeholder markers in delivered files."
            )
        return ""

    return (
        "### 🏗️ PHASE 1 SCAFFOLD RULES\n"
        "This is Phase 1 (Scaffold). You MUST build ONLY:\n"
        "- Framework boilerplate (Next.js/Vite setup, folder structure)\n"
        "- Route pages (page.tsx files with proper exports and minimal layout)\n"
        "- Package dependencies (package.json with required libraries)\n"
        "- Configuration files (tsconfig.json, next.config.js, etc.)\n\n"
        "You MUST NOT build:\n"
        "- Styled marketing pages or landing pages\n"
        "- Custom CSS, Tailwind themes, or color schemes\n"
        "- Real page content (copy, images, layouts)\n"
        "- Navigation components with styled links\n\n"
        "Design tokens and visual styling come from Phase 2.3 (design agent).\n"
        "Building styled pages now will be OVERWRITTEN in Phase 2.3.\n\n"
        "⚠️ **CRITICAL: NON-EMPTY DIRECTORY & ROOT PROTECTION MANDATE**\n"
        "This project root directory `.` is NOT empty (it contains docs, memory-bank, etc.).\n"
        "CLI tools like `create-next-app` or `create-vite` will fail if you run them in `.`.\n"
        "To protect the root directory planning artifacts, you MUST scaffold the application into a fixed subdirectory.\n"
        "Use a command like:\n"
        "```bash\n"
        "npx create-next-app@... ./web [flags]\n"
        "```\n"
        "Do NOT scaffold into a temp directory and attempt to `rsync` or move files into `.`. Leave the application entirely within the `./web` (or similar) subdirectory."
    )

def _build_tools_section(
    profile: str,
    config: dict,
) -> str:
    """Build TOOLS section — available tools for this profile."""
    if not config.get("available_tools"):
        return ""
    tools = _PROFILE_TOOLS.get(profile.lower() if profile else "", [])
    if not tools:
        return ""
    tool_list = ", ".join(f"`{t}`" for t in tools)
    return (
        f"### AVAILABLE TOOLS\n"
        f"Your profile ({profile}) can use: {tool_list}\n"
        f"Do NOT attempt other tools — they will be blocked."
    )

def _build_dev_server_url_section(
    profile: str,
    config: dict,
) -> str:
    """Build dev server URL section — inject the actual running port.

    RCA-FIX: E2E agent navigated to localhost:3000 (LLM training default)
    because the delegation package never provided the actual dev server URL.
    The E2E prompt says "extract port from delegation instructions" but no
    port was ever injected.

    This function scans ports 5100-5199 (the standard AGIX dev server
    range) to discover running servers and injects the exact URL so agents
    don't have to guess.

    Relevant for e2e, code, and debug profiles — any profile that needs
    to interact with the dev server.
    """
    if not config.get("dev_server_url", False):
        return ""

    import urllib.request

    found_port = None
    for port in range(5100, 5200):
        try:
            req = urllib.request.Request(
                f"http://0.0.0.0:{port}/", method="HEAD"
            )
            with urllib.request.urlopen(req, timeout=0.5) as resp:
                if resp.status < 500:
                    found_port = port
                    break
        except Exception:
            continue

    if not found_port:
        return (
            "### 🌐 DEV SERVER STATUS\n"
            "⚠️ No dev server detected on ports 5100-5199.\n"
            "Use `services_mgt` → `list_services` to check for running services.\n"
            "If no server is running, use `services_mgt` → `start_service` with\n"
            "`command: \"npm run dev\"` — NEVER run `npm run dev` directly via terminal.\n"
            "🔴 DO NOT navigate to `localhost:3000` — that port is NOT used."
        )

    url = f"http://0.0.0.0:{found_port}"
    return (
        f"### 🌐 DEV SERVER URL (VERIFIED — USE THIS EXACT URL)\n\n"
        f"✅ Dev server is RUNNING at: **`{url}`**\n\n"
        f"🔴 **MANDATORY**: Navigate to `{url}` — NOT `localhost:3000` or any other port.\n"
        f"All browser_agent, curl, and scrape_url calls MUST use this URL.\n"
        f"The dev server port is dynamically assigned by `services_mgt` and is\n"
        f"NEVER port 3000.\n\n"
        f"Route examples:\n"
        f"  - Home: `{url}/`\n"
        f"  - API: `{url}/api/...`"
    )

def _build_budget_section(
    agent: "Agent",
    subordinate: "Agent",
    kwargs: dict,
    config: dict,
) -> str:
    """Build BUDGET section — turn budget + task tracking metadata."""
    if not config.get("turn_budget"):
        return ""

    lines = ["### BUDGET & TRACKING"]

    # Turn budget
    try:
        parent_turns = getattr(agent, "_absolute_turns", 0)
        parent_max = agent.get_max_turns() if hasattr(agent, "get_max_turns") else 0
        if parent_max > 0:
            remaining = parent_max - parent_turns
            lines.append(f"Turn budget: {remaining} turns remaining (of {parent_max})")
    except Exception:
        pass

    # Task tracking
    task_hash = kwargs.get("_task_hash", "")
    task_seq_id = kwargs.get("_task_seq_id", 0)
    task_guid = kwargs.get("task_guid", "")

    if task_hash and task_seq_id:
        tracking = f"Task: `{task_hash}` attempt #{task_seq_id}"
        if task_guid:
            tracking += f" (guid: `{task_guid}`)"
        lines.append(tracking)
        lines.append(f"Include `task_hash={task_hash}` and `attempt=#{task_seq_id}` in ALL responses.")

        # Side effect: propagate tracking to subordinate.data
        try:
            subordinate.data["_parent_task_hash"] = task_hash
            subordinate.data["_parent_task_seq_id"] = task_seq_id
            if task_guid:
                subordinate.data["_parent_task_guid"] = task_guid
        except Exception:
            pass

    return "\n".join(lines) if len(lines) > 1 else ""

def _build_skill_section(
    agent: "Agent",
    profile: str,
    config: dict,
) -> str:
    """Build SKILL section — activated skill conventions reference."""
    if not config.get("skill_reference"):
        return ""
    try:
        activated_skill = agent.get_data("_activated_skill_name")
        if not activated_skill:
            return ""
        target = profile.lower() if profile else ""
        if target not in ("architect", "code", "frontend_designer"):
            return ""
        if not hasattr(agent, "skills_manager"):
            return ""

        lines = [f"### ACTIVE SKILL: {activated_skill}"]
        lines.append(f"Follow **{activated_skill}** conventions.")

        conventions_name = None
        if "fullstack" in activated_skill:
            conventions_name = "fullstack-conventions"

        if conventions_name:
            conv_content = agent.skills_manager.get_skill_content(conventions_name)
            if conv_content and conv_content.instructions:
                conv_lines = conv_content.instructions.split("\n")[:80]
                lines.append("\n".join(conv_lines))

        return "\n".join(lines)
    except Exception:
        return ""

def _build_auth_prohibition_section(
    profile: str,
    kwargs: dict,
    ctx: ProjectContext,
    config: dict | None = None,
) -> str:
    """Build auth prohibition section — warns code agents not to add auth.

    F-7: When the task's assigned requirements do NOT include any
    auth-related categories or keywords, inject a prohibition to
    prevent scope creep from agents inventing auth flows.

    Only fires for code profile.
    """
    if profile.lower() != "code":
        return ""

    if not ctx.requirements:
        return ""

    all_reqs = ctx.requirements.get("requirements", [])
    req_ids = kwargs.get("requirement_ids", [])

    if not req_ids:
        return ""

    # Check if any assigned requirement is auth-related
    for req in all_reqs:
        if not isinstance(req, dict):
            continue
        if req.get("id") not in req_ids:
            continue
        category = str(req.get("category", "")).lower()
        text = str(req.get("text", "")).lower()
        if category in _AUTH_CATEGORIES:
            return ""  # Auth is required — no prohibition
        if any(kw in text for kw in _AUTH_KEYWORDS):
            return ""  # Auth keyword in requirement text — no prohibition

    return (
        "### ⚠️ NO AUTH REQUIRED — FORBIDDEN\n"
        "Do NOT add login/password/auth/signup features. "
        "The assigned requirements do NOT include authentication. "
        "Adding unauthorized auth is scope creep and will be REJECTED."
    )

def _detect_framework(project_dir: str) -> str:
    """Detect the frontend framework from package.json dependencies.

    Reads both `dependencies` and `devDependencies` from package.json
    and returns a framework identifier string.

    Args:
        project_dir: Absolute path to the project directory.

    Returns:
        Framework identifier: 'nextjs', 'nuxtjs', 'vite', 'remix',
        'astro', 'sveltekit', or '' if no recognized framework found.
    """
    pkg_path = os.path.join(project_dir, "package.json")
    if not os.path.isfile(pkg_path):
        return ""

    try:
        with open(pkg_path, "r", encoding="utf-8") as f:
            pkg = json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return ""

    if not isinstance(pkg, dict):
        return ""

    # Merge deps + devDeps for framework detection
    deps = {}
    for key in ("dependencies", "devDependencies"):
        section = pkg.get(key, {})
        if isinstance(section, dict):
            deps.update(section)

    # Detection order: most specific first
    if "next" in deps:
        return "nextjs"
    if "nuxt" in deps:
        return "nuxtjs"
    if "@remix-run/react" in deps or "@remix-run/node" in deps:
        return "remix"
    if "@sveltejs/kit" in deps:
        return "sveltekit"
    if "astro" in deps:
        return "astro"
    if "vite" in deps:
        return "vite"

    return ""

def _build_framework_pitfalls_section(
    project_dir: str,
    config: dict,
) -> str:
    """Build framework-specific anti-pattern warnings with solutions.

    F-3 (ITR-25): Detects the project framework from package.json and
    produces a section with known pitfalls + solutions. Also advises
    the agent to use RESEARCHER profile for updated framework docs
    to avoid overfitting on static rules.

    Args:
        project_dir: Absolute path to the project directory.
        config: Profile context config dict (must have framework_pitfalls key).

    Returns:
        Formatted pitfall section, or empty string if disabled or no framework.
    """
    if not config.get("framework_pitfalls"):
        return ""

    if not project_dir or not os.path.isdir(project_dir):
        return ""

    framework = _detect_framework(project_dir)
    if not framework:
        return ""

    rules = _FRAMEWORK_PITFALL_RULES.get(framework, [])
    if not rules:
        return ""

    framework_display = {
        "nextjs": "Next.js",
        "nuxtjs": "Nuxt.js",
        "remix": "Remix",
        "vite": "Vite",
        "sveltekit": "SvelteKit",
        "astro": "Astro",
    }.get(framework, framework)

    lines = [
        f"### ⚠️ {framework_display} Anti-Pattern Pitfalls (Framework-Detected)",
        "",
        f"This project uses **{framework_display}**. The following are KNOWN "
        f"anti-patterns that cause build failures or 500 errors. These rules "
        f"are advisory — if unsure, delegate to a **RESEARCHER** agent to fetch "
        f"the latest {framework_display} documentation for your specific version.",
        "",
    ]

    for i, rule in enumerate(rules, 1):
        lines.append(f"**{i}. ❌ {rule['warning']}**")
        lines.append(f"   Pattern: `{rule['pattern']}`")
        lines.append(f"   ✅ Solution: {rule['solution']}")
        lines.append("")

    lines.append(
        f"💡 **Dynamic Adaptation**: These rules reflect common pitfalls but "
        f"framework APIs evolve. If you encounter an unfamiliar pattern or are "
        f"unsure about compatibility, delegate to a RESEARCHER to check the "
        f"latest {framework_display} docs before proceeding. Do NOT guess."
    )

    return "\n".join(lines)

def _build_domain_context_section(
    agent_data: dict | None,
    project_dir: str,
) -> str:
    """Build a domain context section from the user's original prompt.

    F-9 (ITR-25): The code agent receives manifest + requirements but
    never sees the user's original business narrative (value proposition,
    target audience, pricing model, domain language). This section injects
    the user's prompt (truncated) so the agent understands the WHY behind
    the WHAT.

    Args:
        agent_data: The agent's data dict (may contain _original_user_prompt).
        project_dir: Path to the project directory.

    Returns:
        Formatted domain context section, or empty string if no prompt.
    """
    if not agent_data or not isinstance(agent_data, dict):
        return ""

    prompt = agent_data.get("_original_user_prompt", "")
    if not prompt or not isinstance(prompt, str) or not prompt.strip():
        return ""

    prompt = prompt.strip()

    # Truncate long prompts
    if len(prompt) > _MAX_DOMAIN_CONTEXT_CHARS:
        prompt = prompt[:_MAX_DOMAIN_CONTEXT_CHARS] + "... [truncated]"

    return (
        "### 🌐 Domain Context (User's Original Brief)\n"
        "The user's original request provides business context. Use this to \n"
        "understand the domain, target audience, and value proposition:\n\n"
        f"> {prompt}"
    )

def _build_compliance_section(
    kwargs: dict,
    ctx: ProjectContext,
) -> Optional[str]:
    """Build compliance section with regulation-specific guidance.

    WB-4 fix: When the requirements ledger contains compliance requirements,
    enrich the delegation brief with regulation-specific acceptance criteria.
    Uses skeleton_generator's compliance sub-type classifier to differentiate
    CAN-SPAM (email) from GDPR (privacy) from ADA (accessibility).

    Args:
        kwargs: Delegation kwargs (may contain requirement_ids).
        ctx: Loaded project context with requirements ledger.

    Returns:
        Compliance guidance section string, or None if no compliance reqs.
    """
    if not ctx.requirements:
        return None

    all_reqs = ctx.requirements.get("requirements", [])
    if not all_reqs:
        return None

    # Find compliance requirements (category == "compliance")
    req_ids = kwargs.get("requirement_ids", [])
    compliance_reqs = []
    for req in all_reqs:
        if not isinstance(req, dict):
            continue
        cat = req.get("category", "")
        if cat != "compliance":
            continue
        # If requirement_ids are provided, only include assigned compliance reqs
        if req_ids and req.get("id") not in req_ids:
            continue
        compliance_reqs.append(req)

    if not compliance_reqs:
        return None

    # Classify each compliance requirement by sub-type
    try:
        from python.helpers.skeleton_generator import _classify_compliance_subtype
    except ImportError:
        return None

    lines = ["### ⚖️ Compliance Requirements (WB-4: Regulation-Specific)"]

    for req in compliance_reqs:
        text = req.get("text", "")
        req_id = req.get("id", "?")
        subtype = _classify_compliance_subtype(text)

        # Map sub-type to human-readable label
        subtype_labels = {
            "compliance_email": "📧 Email Compliance (CAN-SPAM)",
            "compliance_privacy": "🔒 Privacy (GDPR/CCPA)",
            "compliance_accessibility": "♿ Accessibility (ADA/WCAG)",
            "compliance": "📋 General Compliance",
        }

        label = subtype_labels.get(subtype, "📋 General Compliance")
        lines.append(f"- **{req_id}** [{label}]: {text}")

    return "\n".join(lines)
