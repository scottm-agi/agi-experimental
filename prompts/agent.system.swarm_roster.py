"""
Swarm Roster VariablesPlugin — Dynamic Agent Profile Roster from Ontology.

Generates a markdown table of all agent profiles and their capabilities
at prompt render time, reading directly from ontology.json.

This ensures the roster is ALWAYS in sync with the actual ontology —
when new profiles or categories are added to ontology.json, the roster
updates automatically without manual prompt edits.

RCA-239: Now reads from `profile_meta` in ontology.json for role labels,
descriptions, routing guidance, prohibitions, output tools, and output
locations. The `profiles` section provides the category→tool enforcement;
`profile_meta` provides the human-readable swarm context for orchestrators.

Single source of truth: ontology.json. No hardcoded Python dicts.

Usage in .md file:
    {{swarm_roster_table}}

U-4: Dead-End Recovery — agents need to know teammate capabilities
to suggest correct routing when they encounter work outside their expertise.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from python.helpers.files import VariablesPlugin, get_abs_path

logger = logging.getLogger("agix.swarm_roster")

# Profiles that are orchestrators — shown but marked differently
_ORCHESTRATOR_PROFILES = {
    "multiagentdev", "architect", "alex", "account-leader",
    "marketing-lead", "sales-enabler",
}

# Profiles to exclude from the roster entirely (internal/meta)
_EXCLUDED_PROFILES = {"default"}

# Human-readable descriptions of tool categories (fallback only —
# used when profile_meta is missing for a profile)
_CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "core": "Basic communication & file reading",
    "files_read": "Read any file by path",
    "files_write": "Write/edit source code files",
    "code_exec": "Execute code, run terminal commands",
    "system_tools": "System-level code execution",
    "code_audit": "Architecture analysis, code quality, vulnerability scanning",
    "design": "UI design, images, deliverables",
    "design_spec": "Architecture specs, mermaid diagrams",
    "deliverables": "Save/read/edit deliverable artifacts",
    "memory": "Persistent knowledge across sessions",
    "system": "Settings, parameters, secrets management",
    "dashboard_ops": "Agile dashboard, system monitoring",
    "on_demand": "Scheduled/recurring tasks",
    "web_dev": "Browser automation, web scraping",
    "web_search": "Web search, Perplexity, Tavily, fact-checking",
    "orchestration": "Delegate to subordinate agents",
    "thinking": "Sequential thinking, 5-whys analysis",
    "sales_tools": "CRM, campaigns, deal management",
    "research": "Deep research, documentation lookup",
    "dev_services": "Service management",
}


class SwarmRosterPlugin(VariablesPlugin):
    """Generate a dynamic swarm roster table from ontology.json.

    Reads `profile_meta` for all human-readable labels and prohibitions.
    Falls back to category-based descriptions if profile_meta is missing.
    """

    def get_variables(self, file: str, backup_dirs=None, **kwargs) -> dict[str, Any]:
        """Generate the swarm_roster_table variable.

        If an `agent` kwarg is provided with a `.config.profile`, that
        profile is excluded from the roster (the agent already knows
        its own capabilities).
        """
        # Load ontology
        ontology_path = get_abs_path("python", "tools", "ontology.json")
        try:
            with open(ontology_path, "r", encoding="utf-8") as f:
                ontology = json.load(f)
        except Exception as e:
            logger.warning(f"[SWARM_ROSTER] Failed to load ontology: {e}")
            return {"swarm_roster_table": "_Swarm roster unavailable — ontology.json not found._"}

        profiles = ontology.get("profiles", {})
        profile_meta = ontology.get("profile_meta", {})
        categories = ontology.get("categories", {})

        # Determine the current agent's profile to exclude
        exclude_profile = None
        agent = kwargs.get("agent")
        if agent and hasattr(agent, "config") and hasattr(agent.config, "profile"):
            exclude_profile = agent.config.profile

        # Build the table
        lines = [
            "### Your Team — Agent Swarm Capabilities",
            "",
            "You are part of a multi-agent swarm. Each agent has a specific role, "
            "capabilities, and **hard prohibitions**. When delegating tasks, check "
            "the 🚫 NEVER column first — sending prohibited work to an agent wastes "
            "tokens and triggers tool blocks.",
            "",
            "| Profile | Role | Description | Route Here When | 🚫 NEVER Delegate | Output → |",
            "|---------|------|-------------|-----------------|-------------------|----------|",
        ]

        for profile_name in sorted(profiles.keys()):
            if profile_name in _EXCLUDED_PROFILES:
                continue
            if profile_name == exclude_profile:
                continue

            meta = profile_meta.get(profile_name, {})

            # Role label (from ontology meta, or fallback)
            role = meta.get("role", profile_name.title())

            # Description (from ontology meta, or build from categories)
            description = meta.get("description", "")
            if not description:
                profile_cats = profiles.get(profile_name, [])
                cap_parts = []
                for cat in profile_cats:
                    if cat in ("core", "thinking", "memory"):
                        continue
                    desc = _CATEGORY_DESCRIPTIONS.get(cat, cat)
                    cap_parts.append(desc)
                description = ", ".join(cap_parts) if cap_parts else "Basic tools only"

            # Route guidance (from ontology meta)
            route_when = meta.get("route_when", "")

            # Prohibitions (from ontology meta)
            never = meta.get("never_delegate") or "—"

            # Output info (from ontology meta)
            output_tool = meta.get("output_tool", "")
            output_loc = meta.get("output_location", "")
            output = f"{output_loc}" if output_loc else "—"

            # Mark orchestrators
            prefix = "🔀 " if profile_name in _ORCHESTRATOR_PROFILES else ""
            lines.append(
                f"| {prefix}`{profile_name}` | {role} | {description} "
                f"| {route_when} | {never} | {output} |"
            )

        lines.extend([
            "",
            "**Pattern**: `debug` finds a bug → reports: "
            "\"Bug in auth.ts:L42 — suggested fix: null check. "
            "Recommend delegating to `code` profile.\" "
            "The orchestrator then delegates the fix.",
        ])

        return {"swarm_roster_table": "\n".join(lines)}
