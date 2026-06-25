"""
Redundant Delegation Guard — tool_execute_before extension.

Runs BEFORE call_subordinate invocations and detects when the orchestrator
is about to delegate work that has ALREADY been completed on disk.

ROOT CAUSE (ITR-48):
    Orchestrator saw stale errors from Phase 3.2 ("lucide-react missing")
    and injected a "Phase 3 Recovery" delegation that re-installed deps and
    re-read manifests — all of which Phase 3.3's code agent had already done.
    Then re-delegated Phase 3.3 from scratch. Wasted 30+ minutes.

    The existing delegation_loop_hook (_27) uses message hashing, so it
    can't catch semantically-different messages that do the same WORK.
    This guard checks the DISK state instead.

Detection:
    1. Extract npm install commands from the delegation message
    2. Check if those packages are already in node_modules/
    3. Extract file paths from the delegation message
    4. Check if those files already exist on disk
    If >80% of the expected work already exists, inject an advisory warning.

Hooks into: tool_execute_before (order 28 — after delegation loop at 27)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from python.helpers.extension import Extension

logger = logging.getLogger("agix.redundant_delegation_guard")

# Tool names that delegate to subordinates
DELEGATION_TOOLS = {"call_subordinate", "call_subordinate_batch", "fan_out_subordinates"}

# Patterns to extract from delegation messages
_NPM_INSTALL_RE = re.compile(r"npm install\s+([\w@/.-]+(?:\s+[\w@/.-]+)*)", re.IGNORECASE)
_FILE_PATH_RE = re.compile(r"(?:Create|create|Update|update|Read|read|Fix|fix)\s+[`\"]?(src/[^\s`\"]+)[`\"]?")
_RECOVERY_KEYWORDS = {"recovery", "fix", "stabiliz", "dependency fix", "environment fix"}


class RedundantDelegationGuard(Extension):
    # Context-aware: orchestrator only, delegation tools
    PROFILES = {"multiagentdev", "alex", "default"}
    TOOLS = frozenset({"call_subordinate", "call_subordinate_batch", "fan_out_subordinates"})

    """Detect and warn when a delegation would repeat already-completed work.
    
    Advisory-only: does NOT block delegations. Injects a warning into
    chat history so the LLM can make an informed decision.
    
    The prompt-level fix (Anti-Redundant-Recovery Rule in SKILL.md)
    is the primary enforcement. This guard provides the signal.
    """

    async def execute(self, tool_name: str = "", tool_args: dict = None, **kwargs):
        if not tool_name or tool_name.lower() not in DELEGATION_TOOLS:
            return
        if not tool_args or not isinstance(tool_args, dict):
            return

        message = tool_args.get("message", "") or tool_args.get("task", "")
        if not message or len(message) < 50:
            return

        # Only check for recovery/fix delegations or implementation phases
        msg_lower = message.lower()
        is_recovery = any(kw in msg_lower for kw in _RECOVERY_KEYWORDS)
        
        # Find the project directory
        project_dir = self._get_project_dir()
        if not project_dir:
            return

        findings = []

        # Check 1: npm packages mentioned in delegation that are already installed
        if is_recovery:
            npm_matches = _NPM_INSTALL_RE.findall(message)
            if npm_matches:
                already_installed = []
                for match in npm_matches:
                    packages = match.split()
                    for pkg in packages:
                        pkg_name = pkg.split("@")[0]  # Strip version
                        if pkg_name and os.path.isdir(
                            os.path.join(project_dir, "node_modules", pkg_name)
                        ):
                            already_installed.append(pkg_name)
                if already_installed:
                    findings.append(
                        f"⚠️ Packages already in node_modules: {', '.join(already_installed)}"
                    )

        # Check 2: Files mentioned in delegation that already exist
        file_matches = _FILE_PATH_RE.findall(message)
        if file_matches:
            already_exist = []
            for fpath in file_matches:
                full_path = os.path.join(project_dir, fpath)
                if os.path.exists(full_path):
                    already_exist.append(fpath)
            if already_exist and len(already_exist) >= len(file_matches) * 0.5:
                findings.append(
                    f"⚠️ Files already exist on disk: {', '.join(already_exist[:5])}"
                )

        # Check 3: If this is a "Recovery" delegation, check if build already passes
        if is_recovery and os.path.exists(os.path.join(project_dir, "node_modules", ".package-lock.json")):
            findings.append(
                "⚠️ node_modules/.package-lock.json exists — deps likely already installed"
            )

        if findings:
            warning = (
                "🔴 **REDUNDANT DELEGATION WARNING** (ITR-48 guard)\n\n"
                "This delegation may be repeating work that is already done:\n"
                + "\n".join(f"- {f}" for f in findings)
                + "\n\n**Before proceeding**, verify the error still exists on disk. "
                "If the issue was already fixed by a prior delegation, skip this "
                "Recovery task and move to the NEXT phase."
            )
            logger.warning(
                f"[REDUNDANT DELEGATION GUARD] {self.agent.agent_name}: "
                f"Detected {len(findings)} redundancy signal(s) in delegation"
            )
            await self.agent.hist_add_warning(warning)
            # Advisory only — do NOT return Response to block

    def _get_project_dir(self) -> str | None:
        """Get the current project directory from agent context."""
        try:
            # Try common data keys
            project_name = self.agent.data.get("project_name", "")
            if project_name:
                candidate = os.path.join("/agix/usr/projects", project_name)
                if os.path.isdir(candidate):
                    return candidate
            
            # Try from context
            ctx = getattr(self.agent, "context", None)
            if ctx:
                project_name = getattr(ctx, "project_name", "")
                if project_name:
                    candidate = os.path.join("/agix/usr/projects", project_name)
                    if os.path.isdir(candidate):
                        return candidate
        except Exception:
            pass
        return None
