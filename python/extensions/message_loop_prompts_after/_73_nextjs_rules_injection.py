"""
Next.js App Router Rules Injection — message_loop_prompts_after extension (order 73).

F-6: The prompt file `prompts/agent.system.rules.nextjs_app_router.md` contains
comprehensive Next.js App Router rules (only `<html>/<body>` in layout.tsx,
`"use client"` requirements, server component constraints, async params in
Next.js 15+, etc.), but was NEVER loaded — zero `{{ include }}` directives
reference it, and no Python extension loaded it.

This extension detects when the current project uses Next.js (by reading
package.json) and conditionally injects the App Router rules into the code
agent's system prompt via `loop_data.extras_temporary`.

Behaviors:
1. Only fires for `code` and `frontend` agent profiles (NOT orchestrator/architect)
2. Reads package.json from the project directory to check for `next` dependency
3. Reads and caches the rules from `prompts/agent.system.rules.nextjs_app_router.md`
4. Caches the detection result in `agent.data["_nextjs_project_detected"]`
   so it only checks once per conversation
5. Injects as a keyed entry in `loop_data.extras_temporary`

Position 73 places it after skill auto-activation (72) and before project
extras (75), ensuring Next.js framework rules are available early in the
prompt chain.
"""
from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

from python.helpers.extension import Extension
from python.helpers.agent_core.config import LoopData

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger("agix.nextjs_rules_injection")

# Profiles that should receive Next.js framework rules.
# Only code-writing profiles benefit from these rules.
ELIGIBLE_PROFILES = {"code", "frontend"}

# Cache key names on agent.data
_DATA_KEY_DETECTED = "_nextjs_project_detected"   # True/False after first check
_DATA_KEY_CACHED_PROMPT = "_nextjs_rules_cached_prompt"  # Cached prompt string (or "")

# Extras key for the prompt injection
_EXTRAS_KEY = "nextjs_app_router_rules"

# Prompt file to load
_PROMPT_FILE = "agent.system.rules.nextjs_app_router.md"


class NextjsRulesInjection(Extension):
    """Auto-detect Next.js projects and inject App Router rules."""

    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        """Inject Next.js App Router rules if the project uses Next.js.

        This fires on every message_loop_prompts_after call, but the actual
        detection only runs once (first time). The result is cached in
        agent.data so subsequent calls are O(1) dict lookups.
        """
        # ── Gate 1: Profile eligibility ──
        profile = (self.agent.config.profile or "").lower()
        if profile not in ELIGIBLE_PROFILES:
            return

        # ── Gate 2: Check cache first ──
        cached_detection = self.agent.get_data(_DATA_KEY_DETECTED)
        if cached_detection is not None:
            # Already checked — use cached result
            if cached_detection:
                cached_prompt = self.agent.get_data(_DATA_KEY_CACHED_PROMPT) or ""
                if cached_prompt:
                    loop_data.extras_temporary[_EXTRAS_KEY] = cached_prompt
            return

        # ── First-time detection ──
        project_folder = self._get_project_folder()
        if not project_folder:
            # No project folder — cache negative and return
            self.agent.set_data(_DATA_KEY_DETECTED, False)
            return

        is_nextjs = self._detect_nextjs_in_package_json(project_folder)

        if is_nextjs:
            # Load the prompt file
            prompt_content = self._load_rules_prompt()
            if prompt_content:
                self.agent.set_data(_DATA_KEY_DETECTED, True)
                self.agent.set_data(_DATA_KEY_CACHED_PROMPT, prompt_content)
                loop_data.extras_temporary[_EXTRAS_KEY] = prompt_content
                logger.info(
                    f"NEXTJS RULES INJECTED for project in {project_folder} "
                    f"(profile={profile}, content_len={len(prompt_content)})"
                )
            else:
                # Prompt file not found or empty
                self.agent.set_data(_DATA_KEY_DETECTED, False)
                logger.warning(
                    f"Next.js detected in {project_folder} but rules prompt "
                    f"'{_PROMPT_FILE}' could not be loaded."
                )
        else:
            self.agent.set_data(_DATA_KEY_DETECTED, False)
            logger.info(
                f"NEXTJS NOT DETECTED in {project_folder} — "
                f"skipping App Router rules injection"
            )

    def _get_project_folder(self) -> str | None:
        """Get the current project's folder path.

        Uses the same approach as _75_include_project_extras.py:
        reads the project name from context and resolves to the folder path.
        """
        try:
            from python.helpers import projects
            project_name = projects.get_context_project_name(self.agent.context)
            if not project_name:
                return None
            return projects.get_project_folder(project_name)
        except Exception as e:
            logger.debug(f"Could not resolve project folder: {e}")
            return None

    def _detect_nextjs_in_package_json(self, project_folder: str) -> bool:
        """Check if the project has 'next' in its package.json dependencies.

        Checks both `dependencies` and `devDependencies`.
        """
        package_json_path = os.path.join(project_folder, "package.json")
        if not os.path.exists(package_json_path):
            return False

        try:
            with open(package_json_path, "r", encoding="utf-8") as f:
                pkg = json.load(f)

            deps = pkg.get("dependencies", {})
            dev_deps = pkg.get("devDependencies", {})

            return "next" in deps or "next" in dev_deps

        except (json.JSONDecodeError, OSError, TypeError) as e:
            logger.debug(f"Error reading package.json at {package_json_path}: {e}")
            return False

    def _load_rules_prompt(self) -> str:
        """Load the Next.js App Router rules prompt file.

        Uses the agent's read_prompt method, which searches the prompt
        directories in the correct priority order (agent-specific first,
        then default prompts/).
        """
        try:
            content = self.agent.read_prompt(_PROMPT_FILE)
            return content.strip() if content else ""
        except Exception as e:
            logger.warning(f"Failed to read prompt '{_PROMPT_FILE}': {e}")
            return ""
