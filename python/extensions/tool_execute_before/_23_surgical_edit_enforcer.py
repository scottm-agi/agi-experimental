"""
Surgical Edit Enforcer Extension (P0-A — MainStreet Fidelity Hardening)

WARNS (does NOT block) agents when using write_to_file on existing files
≥20 lines, steering them toward replace_in_file or apply_diff for surgical
edits. The read-before-write guard (RCA-241) is the hard enforcement layer.

Behavior (RCA-316 — warn-not-block):
  write_to_file on existing file ≥20 lines → LOG WARNING + ALLOW (return None)
  The warning provides file preview and surgical edit guidance.
  All strike counting and escape hatch logic is retained for observability.

Bypass conditions (still relevant for logging/observability):
  - overwrite_force=True → skip warning entirely
  - File doesn't exist → skip (new file creation)
  - File has <20 lines → skip (small file, truncation risk low)
  - Scaffold grace period → skip warning for scaffold-created files (RCA-260)

Priority: 23 (after ToolPreferenceNudger at 22, before FileOwnershipGuard at 24)

5-Why RCA (RCA-316):
  1. Heredoc blocker says "use write_to_file" → surgical enforcer blocks it → catch-22
  2. replace_in_file fails on scaffold content (exact match on formatting differences)
  3. The hard block predates the read-before-write guard (RCA-241)
  4. Now that read-before-write is universally enforced, the hard block is redundant
  5. ROOT: Defense-in-depth layer was incorrectly configured as a hard gate, creating
     deadlocks. Should be advisory only since the primary protection (read-before-write)
     already prevents blind overwrites.
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from typing import Any, Optional

from python.helpers.extension import Extension
from python.helpers.phase_category import PhaseCategory
from python.helpers.tool import Response

logger = logging.getLogger("agix.surgical_edit_enforcer")

# Threshold: only guard files with this many lines or more
SURGICAL_EDIT_MIN_LINES = 20
# RCA-301 Issue 1/3: Maximum times overwrite_force can bypass enforcement
# per file. After this count, overwrite_force is ignored for that file.
# Scaffold-registered files consume their grace period instead.
MAX_OVERWRITE_FORCE_PER_FILE = 2

# After this many replace_in_file failures, allow write_to_file as escape hatch
REPLACE_FAILURE_ESCAPE_THRESHOLD = 3

# RCA-245: After this many write_to_file strikes with ZERO replace_in_file
# attempts, auto-grant escape to prevent permanent deadlock
WRITE_STRIKE_AUTO_ESCAPE_THRESHOLD = 3

# FIX-015: Phase threshold after which write_to_file on existing files
# > PHASE_OVERWRITE_MIN_BYTES is HARD BLOCKED (not just warned).
# Phase 3.5 = scaffold phase is over, all files are now "authored".
PHASE_OVERWRITE_BLOCK_THRESHOLD = 3.5
# Minimum file size (bytes) to trigger phase-aware blocking
PHASE_OVERWRITE_MIN_BYTES = 100

# ── RCA-287: Module-level state storage ──────────────────────────────
# The extension framework creates NEW instances on every call_extensions()
# invocation (extension.py line 40). Instance-level state is lost between
# calls. We store state at module level, keyed by agent context ID, so
# it persists across instantiations while remaining isolated per context.
_global_write_strikes: dict[str, dict[str, int]] = {}   # ctx_id -> {path: count}
_global_replace_failures: dict[str, dict[str, int]] = {}  # ctx_id -> {path: count}
_global_scaffold_graces: dict[str, set[str]] = {}  # ctx_id -> {path, ...}
# RCA-301 Issue 1/3: Per-file overwrite_force usage counter
_global_overwrite_counts: dict[str, dict[str, int]] = {}  # ctx_id -> {path: count}


class SurgicalEditEnforcer(Extension):
    # Context-aware: only fire for code agents, on write tools
    PROFILES = {"code"}
    TOOLS = frozenset({"write_to_file", "save_to_file"})
    CATEGORIES = {
        PhaseCategory.IMPLEMENTATION,
        PhaseCategory.INTEGRATION,
        PhaseCategory.VERIFICATION,
    }

    """Blocks write_to_file on existing files, steering toward surgical edits.

    Tracks per-file strike counts and replace_in_file failure counts to
    implement the 3-strike escalation + escape hatch pattern.
    """

    def __init__(self, agent):
        super().__init__(agent)
        # RCA-287: Use module-level state keyed by agent context ID
        ctx_id = self._get_context_id()
        if ctx_id not in _global_write_strikes:
            _global_write_strikes[ctx_id] = defaultdict(int)
        if ctx_id not in _global_replace_failures:
            _global_replace_failures[ctx_id] = defaultdict(int)
        if ctx_id not in _global_scaffold_graces:
            _global_scaffold_graces[ctx_id] = set()
        if ctx_id not in _global_overwrite_counts:
            _global_overwrite_counts[ctx_id] = defaultdict(int)
        # Expose as instance properties for backward compat
        self._write_strikes = _global_write_strikes[ctx_id]
        self._replace_failures = _global_replace_failures[ctx_id]
        self._scaffold_graces = _global_scaffold_graces[ctx_id]
        self._overwrite_force_counts = _global_overwrite_counts[ctx_id]

    def _get_context_id(self) -> str:
        """Get a stable context ID for state isolation."""
        try:
            if hasattr(self.agent, 'context') and hasattr(self.agent.context, 'id'):
                return str(self.agent.context.id)
        except Exception:
            pass
        return "_default"

    # ── RCA-260 F3: Scaffold Grace Period ──
    # Files created by the scaffold tool are boilerplate that MUST be
    # fully replaced with project-specific content. The enforcer grants
    # a one-time write_to_file pass for each registered scaffold file.
    # After the grace is consumed, subsequent writes are blocked normally.

    def register_scaffold_file(self, file_path: str) -> None:
        """Register a single file as scaffold-created, granting one-time write grace.

        Args:
            file_path: Absolute or relative path to the scaffold file.
        """
        normalized = os.path.normpath(os.path.abspath(file_path))
        self._scaffold_graces.add(normalized)
        logger.info(
            f"[SURGICAL EDIT] Scaffold grace granted for {os.path.basename(normalized)}"
        )

    def register_scaffold_files(self, file_paths: list[str]) -> None:
        """Register multiple files as scaffold-created, granting one-time write grace.

        Args:
            file_paths: List of absolute or relative paths to scaffold files.
        """
        for fp in file_paths:
            self.register_scaffold_file(fp)

    def record_replace_failure(self, file_path: str) -> None:
        """Record a replace_in_file failure for the escape hatch tracker.

        Called externally (e.g., from replace_in_file tool or a
        tool_execute_after extension) when a replace operation fails.
        """
        normalized = os.path.normpath(file_path)
        self._replace_failures[normalized] += 1
        count = self._replace_failures[normalized]
        logger.info(
            f"[SURGICAL EDIT] Replace failure #{count} on {os.path.basename(normalized)} "
            f"(escape at {REPLACE_FAILURE_ESCAPE_THRESHOLD})"
        )

    async def execute(
        self,
        tool_name: str = "",
        tool_args: dict[str, Any] | None = None,
        **kwargs,
    ) -> Optional[Any]:
        """Check if write_to_file should be blocked in favor of surgical edits.

        Returns:
            None if the tool call is allowed.
            A Response object if blocked.
        """
        # Only intercept write_to_file
        if not tool_name or tool_name.lower() != "write_to_file":
            return None

        if not tool_args or not isinstance(tool_args, dict):
            return None

        # RCA-301 Issue 1/3: overwrite_force is NO LONGER an unscoped bypass.
        # It only works for scaffold-registered files or up to
        # MAX_OVERWRITE_FORCE_PER_FILE times per file.
        if tool_args.get("overwrite_force", False):
            # Scaffold-registered files: consume grace normally (handled below)
            # Non-scaffold files: check per-file counter
            pass  # Fall through to scaffold grace check + counter logic below

        path = tool_args.get("path", "")
        if not path:
            return None

        # ISS-4: Resolve relative paths using the canonical project-aware resolver.
        # Previously used files.get_abs_path() which resolves to framework root.
        if not os.path.isabs(path):
            try:
                from python.helpers.resolve_agent_path import resolve_agent_path
                abs_path = resolve_agent_path(path, self.agent)
            except Exception:
                abs_path = os.path.abspath(path)
        else:
            abs_path = path

        # Bypass: new file (doesn't exist yet)
        if not os.path.exists(abs_path):
            return None

        normalized = os.path.normpath(abs_path)

        # RCA-260 F3: Scaffold grace period — allow one-time write for
        # scaffold-created boilerplate files. After the grace is consumed,
        # the file becomes protected like any other authored file.
        if normalized in self._scaffold_graces:
            self._scaffold_graces.discard(normalized)
            logger.info(
                f"[SURGICAL EDIT] Scaffold grace consumed for "
                f"{os.path.basename(normalized)} — subsequent writes blocked"
            )
            return None  # Grace: allow this write

        # RCA-301 Issue 1/3: Per-file overwrite_force counter
        # After scaffold grace is consumed, overwrite_force is rate-limited.
        if tool_args.get("overwrite_force", False):
            self._overwrite_force_counts[normalized] += 1
            count = self._overwrite_force_counts[normalized]
            if count <= MAX_OVERWRITE_FORCE_PER_FILE:
                logger.info(
                    f"[SURGICAL EDIT] overwrite_force accepted for "
                    f"{os.path.basename(normalized)} ({count}/{MAX_OVERWRITE_FORCE_PER_FILE})"
                )
                return None  # Allow but count it
            else:
                logger.warning(
                    f"[SURGICAL EDIT] overwrite_force REJECTED for "
                    f"{os.path.basename(normalized)} — exceeded "
                    f"{MAX_OVERWRITE_FORCE_PER_FILE} uses. Use replace_in_file instead."
                )

        # Count lines in existing file
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                line_count = sum(1 for _ in f)
        except Exception:
            return None  # Can't read → don't block

        # Bypass: small file (below threshold)
        if line_count < SURGICAL_EDIT_MIN_LINES:
            return None

        basename = os.path.basename(normalized)

        # FIX-015: Phase-aware HARD BLOCK for post-scaffold overwrites.
        # After Phase 3.5, write_to_file on existing files > 100 bytes is
        # BLOCKED to prevent destruction of working code during verification/fix.
        current_phase = 0
        try:
            current_phase = float(self.agent.data.get('_current_phase', 0) or 0)
        except (ValueError, TypeError):
            current_phase = 0

        if current_phase > PHASE_OVERWRITE_BLOCK_THRESHOLD:
            try:
                file_size = os.path.getsize(abs_path)
            except OSError:
                file_size = 0

            if file_size > PHASE_OVERWRITE_MIN_BYTES:
                logger.warning(
                    f"[SURGICAL EDIT] PHASE BLOCK: write_to_file on "
                    f"{basename} ({file_size} bytes, {line_count} lines) "
                    f"blocked — current phase {current_phase} > "
                    f"{PHASE_OVERWRITE_BLOCK_THRESHOLD}. "
                    f"Use replace_in_file or apply_diff for surgical edits."
                )
                return Response(
                    message=(
                        f"🚫 **POST-SCAFFOLD OVERWRITE BLOCKED** (Phase {current_phase})\n\n"
                        f"File `{basename}` ({file_size} bytes, {line_count} lines) "
                        f"already contains project code. After Phase 3.5, full-file "
                        f"overwrites are prohibited to prevent destroying working code.\n\n"
                        f"**Use instead:**\n"
                        f"- `replace_in_file` — for targeted text replacement\n"
                        f"- `apply_diff` — for multi-line surgical edits\n\n"
                        f"If you genuinely need to replace the entire file, read it first "
                        f"with `read_file` and use `replace_in_file` with the full content."
                    ),
                    break_loop=False,
                )

        # Check escape hatch 1: if replace_in_file failed ≥3 times on this file,
        # allow the write as a last resort
        replace_fail_count = self._replace_failures.get(normalized, 0)
        if replace_fail_count >= REPLACE_FAILURE_ESCAPE_THRESHOLD:
            logger.info(
                f"[SURGICAL EDIT] Escape hatch for {basename}: "
                f"{replace_fail_count} replace failures → allowing write_to_file"
            )
            return None  # Escape hatch — allow

        # RCA-245: Check escape hatch 2 — write-strike deadlock prevention.
        # If agent has hit N write_to_file strikes but NEVER even tried
        # replace_in_file (0 failures recorded), the agent genuinely can't
        # figure out how to use replace_in_file. Auto-escape to prevent
        # the deadlock loop: block → same-message → hard-stop → supervisor
        # redirect → agent retries write_to_file → repeat forever.
        current_strikes = self._write_strikes.get(normalized, 0)
        if (current_strikes >= WRITE_STRIKE_AUTO_ESCAPE_THRESHOLD
                and replace_fail_count == 0):
            logger.info(
                f"[SURGICAL EDIT] Auto-escape (deadlock prevention) for {basename}: "
                f"{current_strikes} write_to_file strikes with 0 replace_in_file "
                f"attempts → agent cannot use surgical edits, allowing write_to_file"
            )
            return None  # Auto-escape — prevent deadlock

        # Increment strike count (retained for observability/logging)
        self._write_strikes[normalized] += 1
        strike = self._write_strikes[normalized]

        # RCA-238: BLOCK, don't just WARN. The prompt hallucination was fixed.
        # Overwriting an existing file is a destructive anti-pattern.
        # Return a Response object to immediately block the agent and guide it
        # to use replace_in_file or apply_diff.
        logger.warning(
            f"[SURGICAL EDIT BLOCKED] (strike {strike}) on {basename} "
            f"({line_count} lines): write_to_file on existing file. "
        )

        # We return a Response here to trigger a ToolExecutionError in the agent.
        # Include a file preview so the agent can quickly use replace_in_file.
        preview = self._build_file_preview(abs_path, line_count)
        return Response(
            message=(
                f"🚫 **SURGICAL EDIT BLOCKED**\n\n"
                f"You used `write_to_file` on `{basename}` ({line_count} lines) which already exists.\n\n"
                f"**CRITICAL RULE:** `write_to_file` is for NEW files only. It causes silent truncation and amnesia loops on existing files.\n\n"
                f"**Use instead:**\n"
                f"- `replace_in_file` — for targeted text replacement (PREFERRED)\n"
                f"- `apply_diff` — for multi-line surgical edits\n\n"
                f"---\n"
                f"**File Content Preview for `{basename}`:**\n"
                f"```\n{preview}\n```\n"
                f"---\n"
                f"If this is a framework scaffold file, use `overwrite_force: true` to bypass this block (once)."
            ),
            break_loop=False,
        )

    # ── RCA-244: File content preview for auto-read recovery ──────────

    @staticmethod
    def _build_file_preview(abs_path: str, line_count: int) -> str:
        """Read file content for inclusion in block messages.

        For files ≤60 lines: returns full content.
        For larger files: returns first 30 + last 30 lines with truncation.
        """
        HEAD_LINES = 30
        TAIL_LINES = 30
        PREVIEW_THRESHOLD = HEAD_LINES + TAIL_LINES

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            return "(unable to read file)"

        if len(lines) <= PREVIEW_THRESHOLD:
            return "".join(lines).rstrip()

        head = "".join(lines[:HEAD_LINES])
        tail = "".join(lines[-TAIL_LINES:])
        omitted = len(lines) - HEAD_LINES - TAIL_LINES
        return f"{head}\n... ({omitted} lines omitted) ...\n\n{tail}".rstrip()
