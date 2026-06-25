"""
Workpackage Coverage Checker — FIX-5 (RC-4)

Checks whether a file/route is covered by a workpackage in the project's
decomposition_index.json. Used by integration gate checks to augment block
messages with "MISSING WORKPACKAGE" guidance when the blocked file was
never assigned to any agent.

RC-4 (MainStreet Iter3): Gates demanded fixes for files (e.g., landing page)
that had NO workpackage in the decomposition. The agent couldn't fix something
it was never asked to build, exhausting the circuit breaker with irreconcilable
blocks. By detecting uncovered files, the gate can provide escalation guidance
("create a new workpackage") instead of just blocking.
"""

import json
import logging
import os
import re
from typing import Optional

from python.helpers.projects import get_decomp_index_path

logger = logging.getLogger("agix.workpackage_coverage")

# Route-to-file mapping for Next.js App Router
ROUTE_FILE_MAPPINGS = {
    "/": ["src/app/page.tsx", "src/app/page.jsx", "src/app/page.js",
          "app/page.tsx", "app/page.jsx", "app/page.js"],
}


def _extract_route_from_filepath(filepath: str) -> Optional[str]:
    """Extract the route from a Next.js App Router file path.

    Args:
        filepath: Relative path like 'src/app/dashboard/page.tsx'

    Returns:
        Route string like '/dashboard', or '/' for root page.tsx
    """
    # Normalize path
    fp = filepath.replace("\\", "/")

    # Match src/app/.../page.{tsx,jsx,js,ts}
    match = re.match(r'(?:src/)?app/(.+?)/?page\.\w+$', fp)
    if match:
        route_segment = match.group(1).rstrip("/")
        if not route_segment:
            return "/"
        return f"/{route_segment}"

    # Direct root: src/app/page.tsx
    if re.match(r'(?:src/)?app/page\.\w+$', fp):
        return "/"

    return None


def check_workpackage_coverage(
    project_dir: str,
    filepath: str,
) -> Optional[dict]:
    """Check if a file is covered by any workpackage in the decomposition.

    Args:
        project_dir: Root directory of the project.
        filepath: Relative path to the file being checked (e.g., 'src/app/page.tsx').

    Returns:
        None if no decomposition_index.json exists (graceful skip).
        Dict with:
            - covered: bool — True if file/route is in a workpackage
            - workpackage: str|None — Title of matching workpackage if covered
    """
    decomp_path = get_decomp_index_path(project_dir)
    if not os.path.isfile(decomp_path):
        return None

    try:
        with open(decomp_path, "r", encoding="utf-8") as f:
            decomp = json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return None

    if not isinstance(decomp, list):
        return None

    # Extract route from filepath
    route = _extract_route_from_filepath(filepath)

    # Normalize filepath for matching
    fp_lower = filepath.lower().replace("\\", "/")
    fp_segments = [s for s in fp_lower.split("/") if s and s not in ("src", "app")]

    for entry in decomp:
        title = str(entry.get("title", "")).lower()

        # 1. Direct filepath reference in workpackage title
        if fp_lower in title:
            return {"covered": True, "workpackage": entry.get("title", "")}

        # 2. Route reference in workpackage title
        if route and route != "/":
            route_segment = route.strip("/").split("/")[0].lower()
            if route_segment in title:
                return {"covered": True, "workpackage": entry.get("title", "")}
            if route.lower() in title:
                return {"covered": True, "workpackage": entry.get("title", "")}

        # 3. Root route special matching
        if route == "/":
            root_indicators = [
                "landing page", "home page", "main page",
                "landing +", "homepage", "root page",
                "landing section", "hero section",
                "page.tsx",
            ]
            for indicator in root_indicators:
                if indicator in title:
                    return {"covered": True, "workpackage": entry.get("title", "")}

        # 4. File segment matching (e.g., 'settings' from 'settings/page.tsx')
        for segment in fp_segments:
            if segment in ("page.tsx", "page.jsx", "page.js", "page.ts"):
                continue
            if len(segment) > 2 and segment in title:
                # Check it's a meaningful word match, not substring
                pattern = rf'\b{re.escape(segment)}\b'
                if re.search(pattern, title):
                    return {"covered": True, "workpackage": entry.get("title", "")}

    return {"covered": False, "workpackage": None}


def augment_block_with_workpackage_guidance(
    message: str,
    coverage: Optional[dict],
) -> str:
    """Augment a gate block message with workpackage coverage info.

    When a file is NOT covered by any workpackage, adds MISSING WORKPACKAGE
    guidance that tells the orchestrator to create a new workpackage instead
    of endlessly retrying a fix that no agent was assigned to make.

    Args:
        message: Original block message.
        coverage: Result from check_workpackage_coverage(), or None.

    Returns:
        Original message (if covered/None) or augmented message with guidance.
    """
    if coverage is None:
        return message

    if coverage.get("covered", True):
        return message

    # Augment with MISSING WORKPACKAGE guidance
    guidance = (
        "\n\n🚨 **MISSING WORKPACKAGE**: This file is NOT covered by any "
        "workpackage in the decomposition_index.json. The current agent "
        "was never assigned to build this file. To fix this:\n"
        "1. Create a NEW workpackage specifically for this file/route\n"
        "2. Delegate the new workpackage to the appropriate agent\n"
        "3. Do NOT retry the current task — this file needs its own plan"
    )
    return message + guidance
