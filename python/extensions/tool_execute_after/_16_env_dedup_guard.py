"""
.env Duplicate Key Guard — tool_execute_after extension (_16_)

Fires after code_execution_tool when output references `.env` file writes.
Scans the .env file for duplicate keys caused by secret masking appending
§§secret(KEY) entries alongside raw values.

Root cause (5-Why, Iteration 112): The _10_unmask_secrets extension replaces
§§secret(KEY) with actual values, but if the agent writes the .env file
AFTER masking, the file ends up with both the raw value and the placeholder.
The dotenv library uses the LAST occurrence, which may be an unresolved
placeholder, causing invalid runtime values.

Position: _16_ (after Prisma guard at _15_)
"""

import os
import re
import logging
from typing import Any, Dict, List

from python.helpers.extension import Extension

logger = logging.getLogger("agix.env_dedup_guard")

# Patterns that suggest .env was written
ENV_WRITE_PATTERNS = [
    re.compile(r"\.env", re.IGNORECASE),
    re.compile(r"cat\s*>\s*\.env", re.IGNORECASE),
    re.compile(r"echo.*>.*\.env", re.IGNORECASE),
    re.compile(r"wrote.*\.env", re.IGNORECASE),
    re.compile(r"\.env.*created", re.IGNORECASE),
]


class EnvDedupGuard(Extension):
    # Context-aware: code agents, code execution and write tools
    PROFILES = {"code"}
    TOOLS = frozenset({"code_execution_tool", "code_execution", "write_to_file", "replace_in_file"})

    """Post-tool hook: detect duplicate keys in .env files.

    When code_execution_tool output references .env writes:
    1. Reads the .env file from the active project directory
    2. Parses key=value lines (skipping comments and blanks)
    3. Detects duplicate keys
    4. Injects warning listing the duplicated keys
    """

    async def execute(self, tool_name: str = "", response: Any = None, **kwargs):
        if not tool_name or response is None:
            return

        # Only fire on code_execution_tool
        if tool_name.lower() != "code_execution_tool":
            return

        # Extract response text
        response_text = ""
        if hasattr(response, "message"):
            response_text = response.message or ""
        elif isinstance(response, str):
            response_text = response

        if not response_text:
            return

        # Check if .env was written
        if not any(p.search(response_text) for p in ENV_WRITE_PATTERNS):
            return

        # Get project directory
        project_dir = self.agent.data.get("_active_project_dir", "")
        if not project_dir or not os.path.isdir(project_dir):
            return

        # Find .env file
        env_path = os.path.join(project_dir, ".env")
        if not os.path.isfile(env_path):
            return

        try:
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            return

        # Parse keys
        key_counts: Dict[str, int] = {}
        for line in lines:
            stripped = line.strip()
            # Skip comments and blanks
            if not stripped or stripped.startswith("#"):
                continue
            # Parse KEY=value
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", stripped)
            if match:
                key = match.group(1)
                key_counts[key] = key_counts.get(key, 0) + 1

        # Find duplicates
        duplicates = {k: v for k, v in key_counts.items() if v > 1}

        if not duplicates:
            return

        # Build warning
        dupe_list = ", ".join(f"`{k}` ({v}x)" for k, v in duplicates.items())
        warning = (
            f"⚠️ DUPLICATE .env KEYS: Found duplicate keys in `{env_path}`: "
            f"{dupe_list}.\n\n"
            f"The `dotenv` library uses the LAST value for each key. If the last "
            f"entry is a `§§secret(...)` placeholder that wasn't unmasked, the "
            f"runtime will get an invalid value.\n\n"
            f"FIX: Remove the duplicate entries, keeping only the correct value "
            f"for each key."
        )

        await self.agent.hist_add_warning(warning)
        logger.warning(
            f"[ENV_DEDUP_GUARD] {self.agent.agent_name}: Duplicate keys in .env: "
            f"{list(duplicates.keys())}"
        )
