from __future__ import annotations
"""
Dev Server Command Enforcer Extension

RCA-253: Agents run `npm run dev` or `npx next dev` directly via
code_execution_tool, binding to port 3000/5000/8000 which are NOT
mapped to the Docker host. The `services_mgt` tool handles port
allocation correctly.

This extension intercepts dev server commands at the tool_execute_before
hook and blocks them with a message telling the agent to use `services_mgt`.

Exception: Commands that explicitly specify a port >= 5100 are allowed,
since those ports are in the mapped range.
"""

import re
from typing import Any
from python.helpers.extension import Extension
from python.helpers.tool import Response
from python.helpers.universal_gate_budget import gate_check
import logging

logger = logging.getLogger("agix.dev_server_enforcer")


# ── Heredoc Stripping ────────────────────────────────────────────────

# Match heredoc start: << 'MARKER', << "MARKER", or << MARKER
_HEREDOC_START = re.compile(
    r"<<-?\s*['\"]?(\w+)['\"]?\s*$",
    re.MULTILINE,
)


def strip_heredoc_bodies(command: str) -> str:
    """Remove heredoc body content from a shell command string.

    Heredocs embed file content inside a shell command. This content
    should NOT be pattern-matched as actual commands.

    Replaces heredoc bodies (everything between `<< 'MARKER'` and
    `MARKER`) with an empty string, preserving the shell commands
    outside the heredoc blocks.

    Args:
        command: The full shell command string, potentially multi-line.

    Returns:
        The command with heredoc bodies removed.
    """
    result = []
    lines = command.split("\n")
    i = 0
    while i < len(lines):
        match = _HEREDOC_START.search(lines[i])
        if match:
            marker = match.group(1)
            result.append(lines[i][:match.start()])  # Keep the part before <<
            i += 1
            # Skip lines until we find the closing marker
            while i < len(lines) and lines[i].strip() != marker:
                i += 1
            i += 1  # Skip the closing marker line itself
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)


# ── Dev Server Detection ─────────────────────────────────────────────

# Patterns that indicate a dev server command (process stays running, listens)
# ISS-3 (RCA-335): Consolidated from _11 + _28 to create a single blocking gate.
_DEV_SERVER_PATTERNS = [
    r'\bnpm\s+run\s+dev\b',
    r'\bnpm\s+start\b',
    r'\bnpx\s+next\s+dev\b',
    r'\bnext\s+dev\b',
    r'\bnpx\s+vite\b',                # ISS-3: consolidated from _28
    r'\byarn\s+dev\b',                 # ISS-3: consolidated from _28
    r'\byarn\s+start\b',              # ISS-3: consolidated from _28
    r'\bpnpm\s+dev\b',                # ISS-3: consolidated from _28
    r'\bpnpm\s+start\b',              # ISS-3: consolidated from _28
    r'\bflask\s+run\b',
    r'\bpython\s+-m\s+flask\s+run\b',
    r'\buvicorn\b',
    r'\bgunicorn\b',
    r'\bnode\s+server[\./]',           # node server.js, node server/index.js
    r'\bnode\s+dist/',                 # ISS-3: consolidated from _28
    r'\bpython3?\s+-m\s+http\.server\b',
]

# Port exception: if the command explicitly sets a port >= 5100, allow it
_HIGH_PORT_PATTERN = re.compile(
    r'(?:--port\s+|PORT=|:)(\d{4,5})\b'
)

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _DEV_SERVER_PATTERNS]


def is_dev_server_command(command: str) -> bool:
    """Check if a shell command is a dev server command that should be blocked.

    Returns True if the command matches a dev server pattern AND does not
    specify a port >= 5100 (mapped port range).

    Heredoc bodies are stripped before matching to prevent false positives
    when agents write config files that mention dev server commands as text.

    Args:
        command: The shell command string to check.

    Returns:
        True if the command should be blocked, False if allowed.
    """
    # Strip heredoc bodies so we only scan actual shell commands
    stripped = strip_heredoc_bodies(command)

    # Check if any dev server pattern matches in the stripped command
    is_dev = any(p.search(stripped) for p in _COMPILED_PATTERNS)

    if not is_dev:
        return False

    # Exception: allow if explicit high port (>= 5100) is specified
    port_match = _HIGH_PORT_PATTERN.search(stripped)
    if port_match:
        port = int(port_match.group(1))
        if port >= 5100:
            return False  # Allowed — port is in mapped range

    return True


# ── Extension Class ──────────────────────────────────────────────────

class DevServerEnforcer(Extension):
    # Context-aware: only fire for code agents, on code execution
    PROFILES = {"code"}
    TOOLS = frozenset({"code_execution_tool", "code_execution"})

    """Blocks direct dev server commands, enforcing `services_mgt` tool usage.

    Intercepts `code_execution_tool` calls that contain dev server
    commands (npm run dev, flask run, uvicorn, etc.) and returns a
    blocking response telling the agent to use `services_mgt` instead.

    This prevents agents from binding to default ports (3000, 5000, 8000)
    which are not mapped to the Docker host.
    """

    async def execute(
        self,
        tool_args: dict[str, Any] | None = None,
        tool_name: str = "",
        **kwargs,
    ):
        # Only intercept code execution tools
        if tool_name not in ("code_execution_tool", "code_execution"):
            return None

        if not tool_args:
            return None

        # Extract the command from args (different tools use different keys)
        command = ""
        for key in ("code", "command", "runtime_code"):
            if key in tool_args:
                command = str(tool_args[key])
                break

        if not command:
            return None

        if not is_dev_server_command(command):
            return None

        # Escape hatch — prevent infinite blocking loops
        if gate_check(self.agent.data, "dev_server_enforcer"):
            return None

        # Block the command
        logger.warning(
            f"[DEV_SERVER_ENFORCER] BLOCKED dev server command: "
            f"{command[:100]}... — agent must use services_mgt tool"
        )
        return Response(
            message=(
                "⛔ BLOCKED: Direct dev server commands (npm run dev, npm start, "
                "flask run, uvicorn, etc.) are NOT allowed via code_execution_tool. "
                "Default ports (3000, 5000, 8000) are NOT mapped to the Docker host.\n\n"
                "Use the `services_mgt` tool instead:\n"
                "```json\n"
                '{\n'
                '  "tool_name": "services_mgt",\n'
                '  "tool_args": {\n'
                '    "action": "start_service",\n'
                '    "command": "npm run dev",\n'
                '    "project_dir": "/path/to/project",\n'
                '    "name": "my-app-dev"\n'
                '  }\n'
                '}\n'
                "```\n\n"
                "The `services_mgt` tool handles automatic port allocation to a "
                "host-mapped port range (5100+) and provides the correct URL."
            ),
            break_loop=False,
        )
