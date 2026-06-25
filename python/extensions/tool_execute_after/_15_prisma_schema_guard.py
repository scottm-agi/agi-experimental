"""
Prisma Schema Validation Guard — tool_execute_after extension (_15_)

Fires after code_execution_tool when `prisma generate` or `prisma db push`
is detected in the output. Validates that the Prisma schema has a `url`
field in the datasource block OR that the PrismaClient constructor passes
datasourceUrl/datasources explicitly.

Root cause (5-Why, Iteration 112): Prisma 7.x moved datasource url from
the schema to prisma.config.ts (CLI-only). The runtime client still needs
the url either in the schema or the constructor. Without this guard, agents
scaffold a valid CLI config but broken runtime client, leading to 500 errors
on ALL API routes that import PrismaClient.

Position: _15_ (after restart hook at _14_, before dedup guard at _16_)
"""

import os
import re
import logging
import glob
from typing import Any

from python.helpers.extension import Extension

logger = logging.getLogger("agix.prisma_schema_guard")

# Patterns that indicate Prisma generate/push was run
PRISMA_TRIGGER_PATTERNS = [
    re.compile(r"Generated?\s+Prisma\s+Client", re.IGNORECASE),
    re.compile(r"prisma\s+generate", re.IGNORECASE),
    re.compile(r"prisma\s+db\s+push", re.IGNORECASE),
    re.compile(r"prisma\s+migrate", re.IGNORECASE),
]

# Patterns in schema.prisma that indicate url is present
SCHEMA_URL_PATTERN = re.compile(r"url\s*=", re.IGNORECASE)

# Patterns in PrismaClient constructor that indicate url is passed
CONSTRUCTOR_URL_PATTERNS = [
    re.compile(r"datasourceUrl", re.IGNORECASE),
    re.compile(r"datasources\s*:", re.IGNORECASE),
]


class PrismaSchemaGuard(Extension):
    # Context-aware: code agents only, write tools
    PROFILES = {"code"}
    TOOLS = frozenset({"write_to_file", "replace_in_file", "apply_diff", "save_to_file"})

    """Post-tool hook: validate Prisma schema + client have url binding.

    When prisma generate/push is detected, checks:
    1. Does schema.prisma have `url = env(...)` in datasource block?
    2. If not, does the PrismaClient constructor pass datasourceUrl or datasources?
    3. If neither → inject warning with exact fix

    Tracking keys on agent.data:
        _prisma_guard_warned: set of project_dirs already warned
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

        # Check if prisma generate/push was triggered
        if not any(p.search(response_text) for p in PRISMA_TRIGGER_PATTERNS):
            return

        # Get project directory
        project_dir = self.agent.data.get("_active_project_dir", "")
        if not project_dir or not os.path.isdir(project_dir):
            return

        # Only warn once per project
        warned = self.agent.data.get("_prisma_guard_warned", set())
        if not isinstance(warned, set):
            warned = set(warned)
        if project_dir in warned:
            return

        # Check 1: Does schema.prisma have `url` in datasource block?
        schema_path = os.path.join(project_dir, "prisma", "schema.prisma")
        if not os.path.isfile(schema_path):
            return  # No schema = not a Prisma project

        try:
            with open(schema_path, "r", encoding="utf-8") as f:
                schema_content = f.read()
        except Exception:
            return

        # Extract datasource block
        datasource_match = re.search(
            r"datasource\s+\w+\s*\{([^}]+)\}", schema_content, re.DOTALL
        )
        if not datasource_match:
            return  # No datasource block

        datasource_block = datasource_match.group(1)
        if SCHEMA_URL_PATTERN.search(datasource_block):
            # Schema has url — good
            logger.debug(f"[PRISMA_GUARD] Schema has url in datasource — OK")
            return

        # Check 2: Does PrismaClient constructor pass url?
        prisma_client_files = self._find_prisma_client_files(project_dir)
        for filepath in prisma_client_files:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                if any(p.search(content) for p in CONSTRUCTOR_URL_PATTERNS):
                    logger.debug(
                        f"[PRISMA_GUARD] Constructor has datasourceUrl in "
                        f"{filepath} — OK"
                    )
                    return
            except Exception:
                continue

        # Neither schema nor constructor has url — inject warning
        warned.add(project_dir)
        self.agent.data["_prisma_guard_warned"] = warned

        warning = (
            "⚠️ PRISMA RUNTIME ERROR: Your `prisma/schema.prisma` datasource block "
            "has NO `url` field, and your PrismaClient constructor passes NO "
            "`datasourceUrl` or `datasources` option. This WILL cause "
            "`PrismaClientInitializationError` at runtime (HTTP 500 on all API routes).\n\n"
            "FIX (choose one):\n"
            "1. Add `url = env(\"DATABASE_URL\")` to the datasource block in schema.prisma\n"
            "2. OR pass `datasourceUrl: process.env.DATABASE_URL` to `new PrismaClient({ ... })`\n\n"
            "After fixing, run `npx prisma generate` again."
        )

        await self.agent.hist_add_warning(warning)
        logger.warning(
            f"[PRISMA_GUARD] {self.agent.agent_name}: Missing url in schema "
            f"AND constructor — warning injected"
        )

    def _find_prisma_client_files(self, project_dir):
        """Find files that import/instantiate PrismaClient."""
        patterns = [
            os.path.join(project_dir, "src", "lib", "prisma.ts"),
            os.path.join(project_dir, "src", "lib", "prisma.js"),
            os.path.join(project_dir, "lib", "prisma.ts"),
            os.path.join(project_dir, "lib", "prisma.js"),
            os.path.join(project_dir, "src", "db.ts"),
            os.path.join(project_dir, "src", "db.js"),
        ]
        # Also glob for any file containing PrismaClient in common locations
        for g in glob.glob(os.path.join(project_dir, "src", "**", "prisma.*"), recursive=True):
            if g not in patterns:
                patterns.append(g)

        return [p for p in patterns if os.path.isfile(p)]
