"""
FileGuard — Pure Allowlist Project Sandbox Enforcement (ADR-012b).

ARCHITECTURE: ALLOWLIST, not blocklist.
    ONLY paths under /agix/usr/projects/<active_project>/ are allowed.
    EVERYTHING ELSE is blocked by default — no enumeration needed.

WHY: The original FileGuard (blocklist) tried to enumerate every bad path.
Every new directory or config file required a new rule. Unknown paths
defaulted to ALLOWED-with-warning — the exact hole that caused agents to
pollute the framework root with 14 files. An allowlist inverts the default:
unknown paths are BLOCKED. Only explicitly allowed paths pass.
"""
from __future__ import annotations

import os
import re
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


def _get_framework_base_dir() -> str:
    """Get the framework root directory dynamically.

    In Docker: /agix/
    Locally: /path/to/agix/
    """
    try:
        from python.helpers import files
        return files.get_base_dir()
    except ImportError:
        return "/agix"


# The ONLY writable base path for agents
PROJECTS_BASE = "/agix/usr/projects/"

# Known project-like subdirectories for auto-resolve heuristic.
# When an agent writes to /agix/src/app/page.tsx, we redirect to
# /agix/usr/projects/<project>/src/app/page.tsx.
_PROJECT_SUBDIRS = (
    "src/", "docs/", "public/", "components/", "lib/",
    "app/", "pages/", "styles/", "assets/", "test/",
    "tests/", "config/", "scripts/", "prisma/", "api/",
    "hooks/", "utils/", "types/", "services/", "store/",
)


class FileGuard:
    """
    Validates file write paths against the active project scope.

    Pure allowlist: ONLY /agix/usr/projects/<active_project>/** is writable.
    Everything else — framework dirs, /tmp/, /exe/, system paths — is blocked.
    """

    @staticmethod
    def _normalize_to_virtual_path(path: str) -> str:
        """Normalize a local filesystem path to its /agix/ equivalent.

        ADR-012: FileGuard must work in both Docker (/agix/) and local
        development environments. This normalizes local paths so the same
        validation logic applies everywhere.

        Examples:
            /Volumes/.../agix/src/app/page.tsx → /agix/src/app/page.tsx
            /agix/src/app/page.tsx → /agix/src/app/page.tsx (unchanged)
        """
        normalized = os.path.normpath(path)
        base_dir = os.path.normpath(_get_framework_base_dir())

        if normalized.startswith(base_dir + os.sep) or normalized == base_dir:
            rel = normalized[len(base_dir):].lstrip(os.sep)
            return "/agix/" + rel if rel else "/agix/"

        return normalized

    @staticmethod
    def validate_write_path(
        path: str,
        active_project: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Validate whether a file write path is allowed.

        ALLOWLIST LOGIC:
            1. Normalize path → /agix/ equivalent
            2. Is it under /agix/usr/projects/<active_project>/ ? → ALLOW
            3. Is it a bare project subdir we can auto-resolve? → REDIRECT
            4. EVERYTHING ELSE → BLOCK

        Args:
            path: Absolute path to write to.
            active_project: Name of the active project (e.g. 'launchpad_123').
                          None if no project is active.

        Returns:
            Tuple of (is_allowed, message).
            - is_allowed=True, message="" for clean passes.
            - is_allowed=True, message="WARNING:..." for soft warnings.
            - is_allowed=True, message="AUTO_RESOLVED:..." for redirected paths.
            - is_allowed=False, message="BLOCKED:..." for hard blocks.
        """
        normalized = FileGuard._normalize_to_virtual_path(path)

        # ── RULE 1: Is it under the projects directory? ──
        if normalized.startswith(PROJECTS_BASE):
            return FileGuard._validate_project_path(normalized, active_project)

        # ── RULE 2: Auto-resolve bare project subdirs ──
        # Agent wrote /agix/src/app/page.tsx → redirect to project sandbox
        if normalized.startswith("/agix/") and active_project:
            rel_path = normalized[len("/agix/"):]
            if any(rel_path.startswith(sd) for sd in _PROJECT_SUBDIRS):
                resolved = PROJECTS_BASE + active_project + "/" + rel_path
                logger.warning(
                    f"FileGuard AUTO-RESOLVED: {normalized} → {resolved}"
                )
                return True, f"AUTO_RESOLVED:{resolved}"

        # ── DEFAULT: BLOCK EVERYTHING ELSE ──
        logger.warning(f"FileGuard BLOCKED write outside project sandbox: {normalized}")
        return False, (
            f"BLOCKED: Cannot write to '{path}'. "
            f"All agent file writes must target the project sandbox at "
            f"/agix/usr/projects/<project_name>/. "
            f"No other paths are writable."
        )

    @staticmethod
    def _validate_project_path(
        normalized: str,
        active_project: Optional[str],
    ) -> Tuple[bool, str]:
        """Validate a path that IS under /agix/usr/projects/."""
        remainder = normalized[len(PROJECTS_BASE):]
        parts = remainder.split("/")

        # Writing to /agix/usr/projects/ itself (no project name)
        if not parts or not parts[0]:
            return False, (
                f"BLOCKED: Cannot write to projects root '{normalized}'. "
                f"Must write inside a specific project directory."
            )

        target_project = parts[0]

        # Single segment = file at projects root, not inside a project
        if len(parts) == 1 and not os.path.isdir(
            os.path.join(PROJECTS_BASE, target_project)
        ):
            return False, (
                f"BLOCKED: Cannot write to projects root '{normalized}'. "
                f"Must write inside a specific project directory."
            )

        # Cross-project write check
        if active_project:
            if target_project != active_project:
                # WORKTREE EXCEPTION: Build tasks work in build-* worktree
                # directories that are separate from the repo-* base clone.
                # These are legitimate write targets for build agents.
                if target_project.startswith("build-"):
                    logger.info(
                        f"FileGuard ALLOWED worktree write: "
                        f"active={active_project}, target={target_project}"
                    )
                    return True, ""

                logger.warning(
                    f"FileGuard BLOCKED cross-project write: "
                    f"active={active_project}, target={target_project}"
                )
                return False, (
                    f"BLOCKED: Cannot write to project '{target_project}' — "
                    f"active project is '{active_project}'. "
                    f"Agents can only write within their assigned project."
                )
            # Writing to active project — ALLOWED
            return True, ""

        # No active project — allow with warning
        logger.info(f"FileGuard WARNING: No active project, allowing write to {normalized}")
        return True, (
            f"WARNING: No active project scope. Writing to '{normalized}' "
            f"without project scope enforcement."
        )

    # ── Route Group validation (preserved from v1) ──
    _ROUTE_GROUP_RE = re.compile(r"\(([^)]+)\)")

    @staticmethod
    def _extract_route_groups(path: str) -> list[str]:
        """Extract all route group names from a path."""
        return FileGuard._ROUTE_GROUP_RE.findall(path)

    @staticmethod
    def validate_route_group_move(
        source: str,
        destination: Optional[str],
        active_project: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Validate whether a file move respects route group boundaries.

        Blocks destructive moves that cross route group boundaries, which breaks
        the Next.js App Router layout hierarchy.
        """
        source_groups = FileGuard._extract_route_groups(source)

        if destination is None:
            if source_groups:
                source_norm = source.rstrip("/")
                last_segment = os.path.basename(source_norm)
                if FileGuard._ROUTE_GROUP_RE.fullmatch(last_segment):
                    return False, (
                        f"BLOCKED: Cannot delete route group directory '{last_segment}'. "
                        f"Route groups like {last_segment} are architectural decisions "
                        f"in the Next.js App Router. Deleting them breaks the layout hierarchy."
                    )
            return True, ""

        dest_groups = FileGuard._extract_route_groups(destination)

        if not source_groups and not dest_groups:
            return True, ""

        if source_groups == dest_groups:
            return True, ""

        if source_groups and not dest_groups:
            return False, (
                f"BLOCKED: Cannot move file out of route group '({source_groups[0]})'. "
                f"Route groups are App Router layout boundaries — moving files out "
                f"breaks the layout hierarchy (auth wrappers, nav, providers). "
                f"If the route returns 404, clear the .next cache and restart the dev server first."
            )

        if source_groups and dest_groups and source_groups != dest_groups:
            return False, (
                f"BLOCKED: Cannot move file from route group '({source_groups[0]})' "
                f"to '({dest_groups[0]})'. Each route group has its own layout.tsx "
                f"and provider hierarchy. Cross-group moves break the architecture."
            )

        return True, ""

    # ── ITR-31 Fix B: .feature file write protection ─────────────────

    # Phase threshold: Phase 3.0+ is implementation/coding territory
    _IMPLEMENTATION_PHASE_THRESHOLD = 3.0

    @staticmethod
    def is_feature_spec_file(path: str) -> bool:
        """Check if a path points to a BDD .feature specification file.

        Returns True if the path ends with '.feature' (case-insensitive).
        Step definition files (.steps.ts, .steps.js, .steps.py) are NOT
        considered spec files — agents create these during implementation.

        Args:
            path: File path (relative or absolute) to check.

        Returns:
            True if the file is a .feature spec file, False otherwise.
        """
        # Normalize path separators for Windows compatibility
        normalized = path.replace("\\", "/")

        # Extract the file extension (case-insensitive)
        _, ext = os.path.splitext(normalized)

        return ext.lower() == ".feature"

    @staticmethod
    def validate_feature_file_protection(
        path: str, agent_data: Optional[dict] = None
    ) -> Tuple[bool, str]:
        """Validate whether a .feature file write should be allowed.

        During Phase 3+ (implementation), .feature files are immutable.
        The BDD specifications created by the architect in Phase 2 define
        the behavior contract — code agents must implement to match the
        spec, not modify the spec to match their code.

        Args:
            path: File path being written to.
            agent_data: Agent's data dict containing '_current_phase'.
                        If None or missing '_current_phase', writes are
                        allowed for backward compatibility.

        Returns:
            Tuple of (is_allowed: bool, message: str).
            - (True, "") if the write is allowed.
            - (False, "BLOCKED: ...") if the write is blocked.
        """
        # ── Backward compatibility: no agent_data or no phase info ────
        if agent_data is None:
            return (True, "")

        current_phase = agent_data.get("_current_phase")
        if current_phase is None:
            return (True, "")

        # ── Only check .feature files ─────────────────────────────────
        if not FileGuard.is_feature_spec_file(path):
            return (True, "")

        # ── Phase check: block at implementation threshold ────────────
        try:
            phase_num = float(current_phase)
        except (TypeError, ValueError):
            # Can't parse phase — allow for safety
            return (True, "")

        if phase_num >= FileGuard._IMPLEMENTATION_PHASE_THRESHOLD:
            logger.warning(
                f"FEATURE FILE GUARD: Blocked write to '{path}' at Phase "
                f"{phase_num}. .feature files are immutable during "
                f"implementation."
            )
            return (
                False,
                f"BLOCKED: Cannot modify .feature files during "
                f"implementation (Phase {phase_num}). BDD specifications "
                f"are the behavior contract created by the architect in "
                f"Phase 2 — they are immutable during Phase 3+. Fix your "
                f"code to match the spec, do NOT weaken the spec to match "
                f"your code. File: {path}",
            )

        # ── Pre-implementation phases: allow (architect creates them) ─
        return (True, "")
