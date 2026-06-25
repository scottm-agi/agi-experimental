"""
Lint Injector (ADR-009) — Runs lint on modified files and formats
diagnostics into a string suitable for inline tool response injection.

Inspired by Roo-Code's DiffViewProvider.newProblemsMessage pattern:
after each file write, diagnostics are captured and returned inline
so the agent sees problems on the same turn it caused them.

Design decisions:
- File-scoped: Only lint the specific file that was just modified
- Error-only: Filter to errors and warnings, skip info/hints
- Truncated: Cap at 10 diagnostics per file to avoid flooding context
- Formatted: [path:line:col] severity: message (rule-id)
"""
from __future__ import annotations
import os
import json
import logging
import subprocess
from dataclasses import dataclass
from typing import Optional, List

logger = logging.getLogger("agix.lint_injector")


@dataclass
class LintDiagnostic:
    """A single lint diagnostic (error or warning)."""
    file_path: str
    line: int
    column: int
    severity: str  # "error" or "warning"
    message: str
    rule_id: Optional[str] = None


def detect_lint_command(project_root: str) -> Optional[str]:
    """Detect the project's lint command from package.json.

    Looks for a 'lint' script in package.json. Falls back to None
    if no lint script is configured.

    Args:
        project_root: Absolute path to the project root directory.

    Returns:
        The lint command string, or None if not found.
    """
    pkg_path = os.path.join(project_root, "package.json")
    if not os.path.exists(pkg_path):
        return None

    try:
        with open(pkg_path, "r", encoding="utf-8") as f:
            pkg = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    scripts = pkg.get("scripts", {})

    # Look for lint-related scripts
    for key in ("lint", "lint:check", "eslint"):
        if key in scripts:
            return scripts[key]

    return None


def parse_eslint_output(output: str) -> List[LintDiagnostic]:
    """Parse ESLint JSON format output into LintDiagnostic objects.

    Only parses the JSON array format (eslint --format json).
    Returns empty list for non-JSON output or parse errors.

    Args:
        output: Raw eslint output string.

    Returns:
        List of LintDiagnostic objects.
    """
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(data, list):
        return []

    diagnostics = []
    for file_entry in data:
        if not isinstance(file_entry, dict):
            continue

        file_path = file_entry.get("filePath", "unknown")
        messages = file_entry.get("messages", [])

        for msg in messages:
            if not isinstance(msg, dict):
                continue

            severity_num = msg.get("severity", 0)
            severity = "error" if severity_num >= 2 else "warning"

            diagnostics.append(LintDiagnostic(
                file_path=file_path,
                line=msg.get("line", 0),
                column=msg.get("column", 0),
                severity=severity,
                message=msg.get("message", ""),
                rule_id=msg.get("ruleId"),
            ))

    return diagnostics


def format_diagnostics(
    diagnostics: List[LintDiagnostic],
    max_count: int = 10,
) -> Optional[str]:
    """Format lint diagnostics into a human-readable string for tool responses.

    Format: [basename:line:col] severity: message (rule-id)

    Args:
        diagnostics: List of LintDiagnostic objects.
        max_count: Maximum number of diagnostics to include.

    Returns:
        Formatted string, or None if no diagnostics.
    """
    if not diagnostics:
        return None

    lines = []
    shown = diagnostics[:max_count]

    for diag in shown:
        basename = os.path.basename(diag.file_path)
        rule_suffix = f" ({diag.rule_id})" if diag.rule_id else ""
        lines.append(
            f"  [{basename}:{diag.line}:{diag.column}] {diag.severity}: "
            f"{diag.message}{rule_suffix}"
        )

    result = "\n".join(lines)

    if len(diagnostics) > max_count:
        remaining = len(diagnostics) - max_count
        result += f"\n  ... and {remaining} more (truncated)"

    return result


# ── RCA-453: Raw linter messages — no bespoke hint mappings ──────────────
# Previously contained _RULE_FIX_HINTS dict that mapped ESLint rule IDs to
# custom fix strings. Removed because:
# 1. Bespoke hints REPLACED the actual error message, losing context (e.g.,
#    the specific variable name, the exact missing dependency).
# 2. Wrong hints caused LLM oscillation loops — the LLM applied the wrong
#    fix, lint failed again, the same wrong hint appeared, repeat forever.
# 3. LLMs already understand TypeScript/JavaScript linting well enough to
#    fix errors given the RAW diagnostic message.
# The _generate_fix_hint function now simply returns the raw message.

# Maximum hint length to prevent table bloat
_MAX_HINT_LENGTH = 100


def _generate_fix_hint(diag: LintDiagnostic) -> str:
    """Return the raw linter message as the fix hint.

    RCA-453: Previously used a _RULE_FIX_HINTS dictionary to map rule IDs
    to custom fix strings. This caused oscillation loops because the bespoke
    hints often lost the specific context (variable names, dependency names)
    that the LLM needs to fix the error. Now returns the raw message directly.

    Args:
        diag: The lint diagnostic to generate a hint for.

    Returns:
        The raw linter message, truncated to _MAX_HINT_LENGTH chars.
    """
    message = diag.message or ""
    if not message:
        # Fallback: use rule_id if available
        if diag.rule_id:
            return f"Fix per `{diag.rule_id}` rule"
        return "Fix the reported issue"

    # Truncate long messages to prevent table overflow
    if len(message) > _MAX_HINT_LENGTH:
        return message[:_MAX_HINT_LENGTH - 3] + "..."
    return message



def format_structured_diagnostics(
    diagnostics: List[LintDiagnostic],
    max_count: int = 10,
    failed_replace_files: Optional[set] = None,
) -> Optional[str]:
    """Format lint diagnostics as a markdown table with actionable fix hints.

    RCA-300: Replaces flat text format that caused agents to re-read files
    in loops. Table format is LLM-native (markdown) and each row includes
    a specific fix hint so the agent can apply surgical edits directly.

    ITR-29: When replace_in_file has recently failed on a file listed in
    diagnostics, the instruction changes from "DO NOT re-read" to "re-read
    before retrying" to break the lint/replace feedback trap.

    Args:
        diagnostics: List of LintDiagnostic objects.
        max_count: Maximum number of diagnostics to include.
        failed_replace_files: Optional set of file paths where replace_in_file
            has recently failed. When a diagnostic file matches (by basename),
            the instruction will tell the agent to re-read instead of
            "DO NOT re-read".

    Returns:
        Formatted markdown table string, or None if no diagnostics.
    """
    if not diagnostics:
        return None

    # Build basename set for fast matching against failed files
    failed_basenames: set = set()
    if failed_replace_files:
        failed_basenames = {os.path.basename(f) for f in failed_replace_files}

    # Check if ANY diagnostic file has replace failures
    shown = diagnostics[:max_count]
    has_failed_files = any(
        os.path.basename(d.file_path) in failed_basenames for d in shown
    )

    # Header changes based on whether any files have replace failures
    if has_failed_files:
        lines = [
            "📋 **Lint Errors — Fix These (⚠️ some files had `replace_in_file` failures — "
            "use `read_file` first to get current content)**\n",
            "| # | File | Line | Rule | Severity | Fix |",
            "|---|------|------|------|----------|-----|",
        ]
    else:
        lines = [
            "📋 **Lint Errors — Fix These (DO NOT re-read the file, apply fixes directly)**\n",
            "| # | File | Line | Rule | Severity | Fix |",
            "|---|------|------|------|----------|-----|",
        ]

    for i, diag in enumerate(shown, 1):
        basename = os.path.basename(diag.file_path)
        rule = diag.rule_id or "—"
        fix_hint = _generate_fix_hint(diag)
        lines.append(
            f"| {i} | {basename} | {diag.line} | {rule} | {diag.severity} | {fix_hint} |"
        )

    if len(diagnostics) > max_count:
        remaining = len(diagnostics) - max_count
        lines.append(f"\n... and {remaining} more (truncated)")

    # Instruction changes based on replace failure state
    if has_failed_files:
        failed_names = [
            os.path.basename(d.file_path) for d in shown
            if os.path.basename(d.file_path) in failed_basenames
        ]
        unique_failed = sorted(set(failed_names))
        lines.append(
            f"\n⚠️ `replace_in_file` previously FAILED on: {', '.join(f'`{n}`' for n in unique_failed)}. "
            f"You MUST use `read_file` on these files first to get the current content, "
            f"then use `replace_in_file` with the EXACT text from the file."
        )
    else:
        lines.append(
            "\n⚠️ Use `replace_in_file` to fix each issue at the specified line. "
            "Do NOT re-read the entire file."
        )
    return "\n".join(lines)


async def get_file_diagnostics(
    abs_path: str,
    project_root: str,
    failed_replace_files: Optional[set] = None,
) -> Optional[str]:
    """Run lint on a single file and return formatted diagnostics string.

    Detects the lint command from package.json, runs it scoped to the
    specific file, and formats the output for tool response injection.

    Args:
        abs_path: Absolute path to the file that was just written.
        project_root: Absolute path to the project root.
        failed_replace_files: Optional set of file paths where replace_in_file
            has recently failed. Passed through to format_structured_diagnostics
            to break the lint/replace feedback trap (ITR-29).

    Returns:
        Formatted diagnostics string, or None if clean or lint unavailable.
    """
    lint_cmd = detect_lint_command(project_root)
    if not lint_cmd:
        return None

    # Try to run eslint on the specific file with JSON format
    try:
        # Use npx eslint directly for file-specific linting
        result = subprocess.run(
            ["npx", "eslint", "--format", "json", "--no-error-on-unmatched-pattern", abs_path],
            capture_output=True,
            text=True,
            cwd=project_root,
            timeout=30,
        )
        diagnostics = parse_eslint_output(result.stdout)
        # RCA-300: Use structured table format with fix hints
        # ITR-29: Pass failed_replace_files to break feedback trap
        return format_structured_diagnostics(
            diagnostics, failed_replace_files=failed_replace_files
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.debug(f"[LINT INJECTOR] Could not run lint: {e}")
        return None


