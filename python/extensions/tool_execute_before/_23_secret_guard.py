"""
SecretGuard — tool_execute_before extension (_23_)

Intercepts write_to_file and replace_in_file tool calls to scan content
for hardcoded secrets BEFORE they are written to disk. This eliminates
the death spiral: write secret → gate blocks → remove secret → gate
blocks on different check → agent re-writes secret.

When secrets are detected, a warning is injected into the tool args
so the agent sees the issue immediately, rather than discovering it
much later when the completion gate runs.

Fires at _23_ (after path enforcement at _15_/_05_, before mode filter _20_).
"""

import logging
from typing import Any

from python.helpers.extension import Extension
from python.helpers.secret_guard import scan_content, should_scan_file

logger = logging.getLogger("agix.secret_guard_extension")


class SecretGuardInterceptor(Extension):
    """Pre-write secret scanner. Warns the agent before secrets reach disk."""

    async def execute(self, tool_name: str = "", tool_args: dict = None, **kwargs):
        if not tool_name:
            return

        tool_lower = tool_name.lower()

        # Only intercept file-writing tools
        if tool_lower not in ("write_to_file", "replace_in_file", "save_file"):
            return

        if not isinstance(tool_args, dict):
            return

        # Extract filepath and content from tool args
        filepath = tool_args.get("filename", "") or tool_args.get("path", "")
        content = tool_args.get("content", "") or tool_args.get("text", "")

        if not filepath or not content:
            return

        # Skip .env files — secrets belong there
        if not should_scan_file(filepath):
            return

        # Scan content for hardcoded secrets
        findings = scan_content(content)
        if not findings:
            return

        # Build a clear, actionable warning for the agent
        warning_lines = [
            f"🔴 SECRET GUARD: Detected {len(findings)} hardcoded secret(s) in {filepath}:",
        ]
        for f in findings[:5]:  # Cap at 5 to avoid flooding
            warning_lines.append(
                f"  • Line {f['line_number']}: {f['description']} "
                f"({f['matched_text']})"
            )
        warning_lines.append(
            "Move these values to .env and reference via process.env.VARIABLE_NAME "
            "or os.environ['VARIABLE_NAME']. DO NOT hardcode secrets in source files."
        )
        warning = "\n".join(warning_lines)

        logger.warning(
            f"[SECRET GUARD] Intercepted {len(findings)} secret(s) "
            f"in write to {filepath}"
        )

        # Inject warning into tool args as a prepended comment.
        # The agent will see this in the tool response and can fix
        # before the completion gate even runs.
        existing_content = tool_args.get("content", "") or tool_args.get("text", "")
        if "content" in tool_args:
            tool_args["_secret_guard_warning"] = warning
        elif "text" in tool_args:
            tool_args["_secret_guard_warning"] = warning

        # Store warning in agent data for gate to reference
        warnings = self.agent.data.get("_secret_guard_warnings", [])
        warnings.append({
            "filepath": filepath,
            "findings": findings,
            "warning": warning,
        })
        # Keep last 10 warnings
        self.agent.data["_secret_guard_warnings"] = warnings[-10:]
