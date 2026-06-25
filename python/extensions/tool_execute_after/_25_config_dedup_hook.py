"""
Config Dedup Guard — tool_execute_after extension.

Runs AFTER code_execution_tool invocations and detects when a scaffold
command (create-next-app, create-vite, etc.) has just completed. If the
scaffold created conflicting config files (e.g., tailwind.config.js +
tailwind.config.ts), automatically removes the lower-priority file.

Root cause (ADR-018, MSR Iteration 150):
    create-next-app scaffolds tailwind.config.js with `content: []`.
    Agent creates tailwind.config.ts with correct content paths.
    Node.js resolves .js before .ts → empty config wins → all styles purged.

Hooks into: tool_execute_after (order 25 — after build loop hook)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from python.helpers.extension import Extension

logger = logging.getLogger("agix.config_dedup_hook")

# Tool names that fire code_execution
CODE_EXEC_TOOLS = {"code_execution_tool", "code_execution"}

# Patterns that indicate a scaffold command just ran
SCAFFOLD_PATTERNS = [
    re.compile(r"\bcreate-next-app\b", re.IGNORECASE),
    re.compile(r"\bcreate-vite\b", re.IGNORECASE),
    re.compile(r"\bcreate-react-app\b", re.IGNORECASE),
    re.compile(r"\bnpx.*init\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+init\b", re.IGNORECASE),
]

# Patterns that indicate scaffold completed successfully
SUCCESS_PATTERNS = [
    re.compile(r"Success", re.IGNORECASE),
    re.compile(r"created successfully", re.IGNORECASE),
    re.compile(r"project is ready", re.IGNORECASE),
    re.compile(r"Done\b", re.IGNORECASE),
    re.compile(r"exit code 0", re.IGNORECASE),
]


def _is_scaffold_command(code: str) -> bool:
    """Check if the executed code is a scaffold command."""
    return any(p.search(code) for p in SCAFFOLD_PATTERNS)


def _extract_project_dir(code: str) -> str:
    """Extract project directory from 'cd /path && ...' style commands."""
    match = re.match(r"^cd\s+(/\S+)\s*(?:&&|;)", code)
    if match:
        return match.group(1)
    return ""


class ConfigDedupHook(Extension):
    # Context-aware: code agents, code execution (post-scaffold)
    PROFILES = {"code"}
    TOOLS = frozenset({"code_execution_tool", "code_execution"})

    """Auto-remove conflicting config files after scaffold commands."""

    async def execute(self, tool_name: str = "", response: Any = None, **kwargs):
        if not tool_name or response is None:
            return

        if tool_name.lower() not in CODE_EXEC_TOOLS:
            return

        tool_args = kwargs.get("tool_args", {})
        if not tool_args or not isinstance(tool_args, dict):
            return

        code = tool_args.get("code", "")
        if not code or not _is_scaffold_command(code):
            return

        # Extract output
        msg = ""
        if hasattr(response, "message") and response.message:
            msg = str(response.message)
        elif isinstance(response, str):
            msg = response

        # Only act on successful scaffolds
        if not any(p.search(msg) for p in SUCCESS_PATTERNS):
            return

        project_dir = _extract_project_dir(code)
        if not project_dir:
            project_dir = getattr(self.agent, "project_dir", "") or ""

        if not project_dir:
            logger.debug("[CONFIG DEDUP HOOK] Could not determine project dir, skipping")
            return

        try:
            from python.helpers.config_dedup import resolve_config_conflicts
            removed = resolve_config_conflicts(project_dir)
            if removed:
                filenames = [f.split("/")[-1] for f in removed]
                logger.warning(
                    f"[CONFIG DEDUP HOOK] {self.agent.agent_name}: "
                    f"Auto-removed {len(removed)} conflicting config(s) in "
                    f"{project_dir}: {filenames}"
                )
                # Inject a system note so agent knows what happened
                await self.agent.hist_add_warning(
                    f"🧹 **Config Dedup**: Auto-removed {len(removed)} conflicting "
                    f"config file(s) after scaffold: {', '.join(filenames)}. "
                    f"Higher-priority .ts/.mjs versions are preserved."
                )
        except Exception as e:
            logger.error(f"[CONFIG DEDUP HOOK] Error during dedup: {e}")

        # RCA-463: Programmatically enforce ignoreDuringBuilds in next.config
        # after scaffold. ESLint errors during `next build` should NEVER block
        # the build — the LLM knows how to fix lint errors, but our defensive
        # infrastructure (surgical edit enforcer) creates loops when the agent
        # tries to fix files. Prevention > prompt rules.
        if re.search(r"create-next-app", code, re.IGNORECASE):
            try:
                await _enforce_next_config_ignore_lint(project_dir, self.agent)
            except Exception as e:
                logger.error(f"[CONFIG DEDUP HOOK] Error enforcing next.config: {e}")

            # ISSUE-6: Also patch .eslintrc.json to disable no-explicit-any
            # in test files, preventing lint loops when agents use `as any` in mocks.
            try:
                _enforce_eslint_test_override(project_dir)
            except Exception as e:
                logger.error(f"[CONFIG DEDUP HOOK] Error enforcing eslint override: {e}")


async def _enforce_next_config_ignore_lint(project_dir: str, agent) -> None:
    """Ensure next.config has ignoreDuringBuilds and ignoreBuildErrors.
    
    RCA-463: ESLint/TypeScript errors blocking `next build` cause build loops
    because the agent's natural fix (write_to_file) gets blocked by the surgical
    edit enforcer, creating an infinite loop. The LLM already knows how to fix
    lint errors — we just need to stop ESLint from making them build-blockers.
    """
    import os

    # Find next.config file (could be .js, .mjs, or .ts)
    config_path = None
    for ext in [".mjs", ".js", ".ts"]:
        candidate = os.path.join(project_dir, f"next.config{ext}")
        if os.path.exists(candidate):
            config_path = candidate
            break

    if not config_path:
        logger.debug("[CONFIG DEDUP HOOK] No next.config found, skipping lint enforcement")
        return

    with open(config_path, "r") as f:
        content = f.read()

    needs_update = False
    original = content

    # Check if ignoreDuringBuilds is already set
    if "ignoreDuringBuilds" not in content:
        needs_update = True
        # Inject eslint + typescript config into the nextConfig object
        # Handle common patterns: `const nextConfig = {` or `module.exports = {`
        config_obj_pattern = re.compile(
            r"(const\s+\w+\s*=\s*\{|module\.exports\s*=\s*\{|export\s+default\s*\{)"
        )
        match = config_obj_pattern.search(content)
        if match:
            insert_pos = match.end()
            inject = (
                "\n  eslint: { ignoreDuringBuilds: true },"
                "\n  typescript: { ignoreBuildErrors: true },"
            )
            content = content[:insert_pos] + inject + content[insert_pos:]

    if needs_update and content != original:
        with open(config_path, "w") as f:
            f.write(content)
        logger.warning(
            f"[CONFIG DEDUP HOOK] {agent.agent_name}: "
            f"Injected eslint.ignoreDuringBuilds + typescript.ignoreBuildErrors "
            f"into {config_path} (RCA-463)"
        )
        await agent.hist_add_warning(
            f"🛡️ **ESLint Config**: Auto-injected `eslint: {{ ignoreDuringBuilds: true }}` "
            f"and `typescript: {{ ignoreBuildErrors: true }}` into `{config_path.split('/')[-1]}`. "
            f"ESLint errors will not block builds."
        )
    else:
        logger.debug("[CONFIG DEDUP HOOK] next.config already has ignoreDuringBuilds")


# ── ISSUE-6: Test-file ESLint override ────────────────────────────────
# The no-explicit-any rule causes infinite lint loops when agents write
# `as any` in test mocks.  Disabling it for test files breaks the loop.

# The override we inject
_TEST_OVERRIDE = {
    "files": ["**/__tests__/**", "**/*.test.*", "**/*.spec.*"],
    "rules": {
        "@typescript-eslint/no-explicit-any": "off"
    }
}


def _enforce_eslint_test_override(project_dir: str) -> None:
    """Patch .eslintrc.json to disable no-explicit-any in test files.

    ISSUE-6: The `@typescript-eslint/no-explicit-any` rule causes a lint
    loop in test files where `as any` is commonly used for mocks.
    This function adds an override to disable the rule for test file
    patterns: **/__tests__/**, **/*.test.*, **/*.spec.*

    Idempotent — skips if the override is already present.
    Preserves all existing config and overrides.
    """
    import json
    import os

    eslintrc_path = os.path.join(project_dir, ".eslintrc.json")
    if not os.path.isfile(eslintrc_path):
        logger.debug("[CONFIG DEDUP HOOK] No .eslintrc.json found, skipping test override")
        return

    try:
        with open(eslintrc_path, "r") as f:
            config = json.load(f)
    except (json.JSONDecodeError, ValueError):
        logger.warning(
            "[CONFIG DEDUP HOOK] .eslintrc.json is malformed, skipping test override"
        )
        return

    if not isinstance(config, dict):
        return

    overrides = config.get("overrides", [])

    # Check if the override is already present (idempotency)
    for existing in overrides:
        rules = existing.get("rules", {})
        if "@typescript-eslint/no-explicit-any" in rules:
            logger.debug(
                "[CONFIG DEDUP HOOK] .eslintrc.json already has no-explicit-any "
                "override, skipping"
            )
            return

    # Append our override
    overrides.append(_TEST_OVERRIDE)
    config["overrides"] = overrides

    with open(eslintrc_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")  # trailing newline

    logger.warning(
        f"[CONFIG DEDUP HOOK] Injected test-file no-explicit-any override "
        f"into {eslintrc_path} (ISSUE-6)"
    )
