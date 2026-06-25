"""
Anti-Pattern Scanner — tool_execute_after extension (F-1C).

Fires after `write_to_file` or `replace_in_file` on .tsx/.ts/.jsx files.
Checks for known anti-patterns that cause build failures:

1. 'use client' + force-dynamic in the same file
2. next/document imports in App Router files (src/app/)
3. Server-only imports in client components

Injects warning into agent history when an anti-pattern is detected,
preventing the agent from introducing known-bad patterns that will
fail at build time.

Hooks into: tool_execute_after (order 35 — after build retry gate)
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from python.helpers.extension import Extension

logger = logging.getLogger("agix.antipattern_scanner")

# Tools that write files
WRITE_TOOLS = {"write_to_file", "replace_in_file"}

# File extensions to scan
SCANNABLE_EXTENSIONS = {".tsx", ".ts", ".jsx", ".js"}

# ── Anti-Pattern Definitions ──────────────────────────────────────────

ANTIPATTERNS = [
    {
        "id": "use-client-force-dynamic",
        "name": "'use client' + force-dynamic conflict",
        "description": (
            "A file cannot be both a Client Component ('use client') and "
            "use `export const dynamic = 'force-dynamic'`. The `dynamic` "
            "export is a Server Component/Route Segment Config option. "
            "Remove 'use client' OR remove the dynamic export."
        ),
        "detect": lambda content, path: (
            bool(re.search(r"""['"]use client['"]""", content))
            and bool(re.search(r"""dynamic\s*=\s*['"]force-dynamic['"]""", content))
        ),
    },
    {
        "id": "next-document-in-app-router",
        "name": "next/document import in App Router",
        "description": (
            "`next/document` (`Html`, `Head`, `Main`, `NextScript`) is for "
            "Pages Router only (`pages/_document.tsx`). In App Router "
            "(`src/app/`), use `<html>`, `<head>`, `<body>` directly in "
            "layout.tsx. Remove the next/document import."
        ),
        "detect": lambda content, path: (
            bool(re.search(r"""from\s+['"]next/document['"]""", content))
            and "/app/" in path
        ),
    },
    {
        "id": "server-only-in-client",
        "name": "server-only import in client component",
        "description": (
            "A file marked with 'use client' cannot import 'server-only'. "
            "Either remove 'use client' or move the server logic to a "
            "separate Server Component."
        ),
        "detect": lambda content, path: (
            bool(re.search(r"""['"]use client['"]""", content))
            and bool(re.search(r"""import\s+['"]server-only['"]""", content))
        ),
    },
    {
        "id": "metadata-in-client-component",
        "name": "metadata export in client component",
        "description": (
            "`export const metadata` is only valid in Server Components. "
            "A file with 'use client' cannot export metadata. Remove "
            "'use client' or move metadata to a parent layout/page."
        ),
        "detect": lambda content, path: (
            bool(re.search(r"""['"]use client['"]""", content))
            and bool(re.search(r"""export\s+(const|let)\s+metadata""", content))
        ),
    },
    {
        "id": "generate-metadata-in-client",
        "name": "generateMetadata in client component",
        "description": (
            "`generateMetadata` is a Server Component async function. "
            "It cannot be used in a file marked with 'use client'. "
            "Remove 'use client' or move generateMetadata to a Server Component."
        ),
        "detect": lambda content, path: (
            bool(re.search(r"""['"]use client['"]""", content))
            and bool(re.search(r"""export\s+(async\s+)?function\s+generateMetadata""", content))
        ),
    },
]


class AntipatternScanner(Extension):
    # Context-aware: code agents, write tools (frontend files)
    PROFILES = {"code"}
    TOOLS = frozenset({"write_to_file", "replace_in_file"})

    """Scan written .tsx/.ts files for known anti-patterns.

    F-1C: Post-write anti-pattern scanner that fires after write_to_file
    or replace_in_file on TypeScript/JSX files. Detects patterns that
    will inevitably cause build failures and injects warnings before
    the agent runs `npm run build`.
    """

    async def execute(self, tool_name: str = "", response: Any = None, **kwargs):
        """Check written file content for anti-patterns."""
        if not tool_name:
            return

        # Only act on write tools
        if tool_name.lower() not in WRITE_TOOLS:
            return

        # Extract tool args
        tool_args = kwargs.get("tool_args", {})
        if not tool_args or not isinstance(tool_args, dict):
            return

        # Get file path
        file_path = tool_args.get("path", "") or tool_args.get("target_file", "")
        if not file_path:
            return

        # Check file extension
        _, ext = os.path.splitext(file_path)
        if ext.lower() not in SCANNABLE_EXTENSIONS:
            return

        # Get content — from tool_args for write_to_file
        content = tool_args.get("content", "") or tool_args.get("new_content", "")
        if not content:
            return

        # Check all anti-patterns
        detected = []
        for pattern in ANTIPATTERNS:
            try:
                if pattern["detect"](content, file_path):
                    detected.append(pattern)
            except Exception as e:
                logger.debug(f"Anti-pattern check '{pattern['id']}' failed: {e}")

        if not detected:
            return

        # Build warning message
        lines = [
            "## ⚠️ Anti-Pattern Detected in Written File",
            "",
            f"**File**: `{file_path}`",
            "",
            "The following anti-pattern(s) were detected. These WILL cause "
            "build failures. Fix them BEFORE running `npm run build`:",
            "",
        ]

        for ap in detected:
            lines.append(f"### 🔴 {ap['name']}")
            lines.append(f"{ap['description']}")
            lines.append("")

        lines.append(
            "**Fix these issues NOW** — do not run `npm run build` until resolved."
        )

        warning_text = "\n".join(lines)

        # Inject warning into agent history
        try:
            await self.agent.hist_add_warning(warning_text)
            logger.info(
                f"[ANTIPATTERN SCANNER] {getattr(self.agent, 'agent_name', 'agent')}: "
                f"Detected {len(detected)} anti-pattern(s) in {file_path}: "
                f"{[ap['id'] for ap in detected]}"
            )
        except Exception as e:
            logger.warning(f"Failed to inject anti-pattern warning: {e}")
