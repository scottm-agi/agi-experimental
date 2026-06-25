"""Secret Scanner — tool_execute_after extension.

Scans saved file content for exposed secrets (API keys, tokens, passwords,
connection strings). Injects warning when secrets detected.

Root cause: No guard existed to detect hardcoded secrets in agent-written code.
Agents sometimes embed API keys or tokens directly in source files.

Hooks into: tool_execute_after (order 36)
"""
from __future__ import annotations

import logging
from typing import Any

from python.helpers.extension import Extension
from python.helpers.secret_scanner import scan_file_content

logger = logging.getLogger("agix.secret_scan")

WRITE_TOOLS = {
    "code_execution_tool", "code_execution",
    "write_to_file", "replace_in_file",
    "save_file", "save_deliverable",
}


class SecretScan(Extension):

    async def execute(self, tool_name: str = "", tool_args: dict = None, response: Any = None, **kwargs):
        if not tool_name or tool_name.lower() not in WRITE_TOOLS:
            return

        try:
            args = tool_args or {}
            filename = args.get("filename", args.get("path", args.get("file_path", "")))
            content = args.get("content", args.get("code", ""))

            if not content:
                return

            matches = scan_file_content(content, file_path=filename or "<stdin>")

            if matches:
                warning_lines = [f"🔒 SECRET DETECTED in {filename or 'written content'}:"]
                for m in matches[:5]:
                    warning_lines.append(f"  • [{m.pattern_name}] line {m.line_number}: {m.matched_value[:30]}...")
                if len(matches) > 5:
                    warning_lines.append(f"  ... and {len(matches) - 5} more")
                warning_lines.append("")
                warning_lines.append("Use environment variables or .env files instead of hardcoding secrets.")

                warning = "\n".join(warning_lines)
                logger.warning(warning)

                if hasattr(self, 'agent') and hasattr(self.agent, 'hist_add_event'):
                    self.agent.hist_add_event(
                        "warning",
                        warning,
                        importance=90,  # High importance — security issue
                    )

        except Exception as e:
            logger.debug(f"Secret scan skipped: {e}")
