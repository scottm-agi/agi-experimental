"""
Subordinate Error Relay — System-level error extraction + injection
for cross-subordinate knowledge transfer.

Root Cause Fix for MSR_Smoke_1777085719:
The system must ensure the orchestrator LLM has deterministic support
for avoiding repeated failed strategies. This module:

1. Extracts error signatures from failed subordinate result text
2. Records them in agent.data (persistent across delegations)
3. Builds injection context for prepending to next delegation message

This removes the LLM from the error relay path — the system handles it.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any

# ── Configuration ──
MAX_ERROR_LOG_ENTRIES = 10     # Keep last N failures
MAX_ERRORS_PER_ENTRY = 5      # Cap errors stored per failure
MAX_RESULT_PREVIEW_LENGTH = 1500  # Cap result preview chars
MAX_SIGNATURES = 10            # Max error signatures to extract
ERROR_LOG_KEY = "_subordinate_error_log"


# ── Error Severity Classification (RCA-270) ──
class ErrorSeverity(str, Enum):
    """Severity level for subordinate error signatures.
    
    BLOCKING errors prevent task completion and warrant re-delegation.
    RECOVERED errors are self-corrected by the agent and should NOT
    trigger re-delegation to avoid causing duplicate work loops.
    """
    BLOCKING = "blocking"    # Build failures, TS errors, exit codes, etc.
    RECOVERED = "recovered"  # Nudges, replace_in_file mismatches, etc.


# Patterns that indicate an error was self-recovered or non-blocking.
# If ANY of these appear in the signature, classify as RECOVERED.
_RECOVERED_PATTERNS: list[re.Pattern] = [
    re.compile(r"tool\s+preference\s+nudge", re.IGNORECASE),
    re.compile(r"SEARCH_BLOCK\s+was\s+not\s+found", re.IGNORECASE),
    re.compile(r"nudge.*(?:use|instead)", re.IGNORECASE),
    re.compile(r"self[- ]correct", re.IGNORECASE),
    re.compile(r"informational\s+only", re.IGNORECASE),
]

# F-13a: Hard-stop loop patterns are ALWAYS BLOCKING — they indicate
# the subordinate is stuck in an unrecoverable loop. These take
# priority over RECOVERED patterns.
_HARDSTOP_BLOCKING_PATTERNS: list[re.Pattern] = [
    re.compile(r"SAME_MSG_DIAG", re.IGNORECASE),
    re.compile(r"hard[\s_-]*stop", re.IGNORECASE),
    re.compile(r"loop\s+detected", re.IGNORECASE),
    re.compile(r"iter=\d+", re.IGNORECASE),
    re.compile(r"identical\s+messages?\s+detected", re.IGNORECASE),
]


def classify_error_severity(signature: str) -> ErrorSeverity:
    """Classify an error signature as BLOCKING or RECOVERED.
    
    BLOCKING: Compilation errors, build failures, missing modules, exit codes,
              hard-stop loop patterns (SAME_MSG_DIAG, iter=N).
    RECOVERED: Tool nudges, replace_in_file mismatches, informational warnings.
    
    Args:
        signature: The error signature string to classify.
        
    Returns:
        ErrorSeverity.BLOCKING or ErrorSeverity.RECOVERED
    """
    if not signature or not signature.strip():
        return ErrorSeverity.RECOVERED
    
    # F-13a: Hard-stop patterns are ALWAYS BLOCKING — check first
    for pattern in _HARDSTOP_BLOCKING_PATTERNS:
        if pattern.search(signature):
            return ErrorSeverity.BLOCKING
    
    # Check if it matches a known recovered pattern
    for pattern in _RECOVERED_PATTERNS:
        if pattern.search(signature):
            return ErrorSeverity.RECOVERED
    
    # Default: any error signature that passed the extraction patterns
    # (TS errors, build failures, npm errors, etc.) is BLOCKING
    return ErrorSeverity.BLOCKING

# ── Error Signature Patterns ──
# These are ordered by specificity — most specific first
_ERROR_PATTERNS: list[re.Pattern] = [
    # F-13a: Hard-stop loop patterns (highest priority)
    re.compile(r"SAME_MSG_DIAG[^\n]*(?:iter=\d+)?", re.IGNORECASE),
    re.compile(r"hard[\s_-]*stop[^\n]{0,80}", re.IGNORECASE),
    re.compile(r"loop\s+detected[^\n]{0,80}", re.IGNORECASE),
    # TypeScript errors: TS2322, TS2345, etc.
    re.compile(r"(?:error\s+)?(TS\d{4,5}):?\s*(.{10,120})", re.IGNORECASE),
    # Prisma errors: P2002, P1001, etc.
    re.compile(r"(?:Error:\s*)?P\d{4}\s+(.{10,120})", re.IGNORECASE),
    # React/Next.js specific
    re.compile(r"Cannot read properties of null \(reading '(\w+)'\)", re.IGNORECASE),
    re.compile(r"Error occurred prerendering page \"(.+?)\"", re.IGNORECASE),
    # Module resolution
    re.compile(r"Module not found:?\s*(?:Can't resolve\s*)?['\"]?(.{5,100})['\"]?", re.IGNORECASE),
    # npm errors
    re.compile(r"npm ERR!\s*(code\s+\w+|ERESOLVE.{0,100})", re.IGNORECASE),
    re.compile(r"unable to resolve dependency tree", re.IGNORECASE),
    # Generic build errors
    re.compile(r"Build (?:error|failed)(?:\s+occurred)?(?::?\s*(.{0,120}))?", re.IGNORECASE),
    # Exit code failures
    re.compile(r"(?:Command\s+)?failed with exit code (\d+)", re.IGNORECASE),
    # Generic Error: lines (catch-all)
    re.compile(r"^Error:\s+(.{10,150})", re.MULTILINE),
]


def extract_error_signatures(result: str) -> list[str]:
    """
    Extract deduplicated error signatures from subordinate result text.
    
    Args:
        result: The full result text from a failed subordinate
        
    Returns:
        List of unique, human-readable error signature strings (max MAX_SIGNATURES)
    """
    if not result or not result.strip():
        return []

    seen: set[str] = set()
    signatures: list[str] = []

    for pattern in _ERROR_PATTERNS:
        for match in pattern.finditer(result):
            # Use the full match as the signature, trimmed
            sig = match.group(0).strip()
            # Normalize whitespace
            sig = re.sub(r'\s+', ' ', sig)
            # Truncate long signatures
            if len(sig) > 150:
                sig = sig[:147] + "..."

            # Deduplicate by a normalized key (lowercase, stripped)
            dedup_key = sig.lower()[:80]
            if dedup_key not in seen:
                seen.add(dedup_key)
                signatures.append(sig)

            if len(signatures) >= MAX_SIGNATURES:
                return signatures

    return signatures


def record_subordinate_failure(
    data: dict[str, Any],
    profile: str,
    errors: list[str],
    result_preview: str = "",
    severity: str = "",
) -> None:
    """
    Record a subordinate failure into the agent's data store.
    
    Args:
        data: Agent's data dict (agent.data)
        profile: The subordinate's agent profile name
        errors: Error signatures extracted from the result
        result_preview: Truncated preview of the full result
        severity: Override severity ('blocking' or 'recovered'). If empty,
                  each error is auto-classified via classify_error_severity().
    """
    log: list[dict] = data.get(ERROR_LOG_KEY, [])

    # Classify each error by severity
    classified_errors = []
    for err in errors[:MAX_ERRORS_PER_ENTRY]:
        if severity:
            sev = severity
        else:
            sev = classify_error_severity(err).value
        classified_errors.append({"text": err, "severity": sev})

    entry = {
        "profile": profile,
        "iteration": len(log) + 1,
        "errors": classified_errors,
        "result_preview": result_preview[:MAX_RESULT_PREVIEW_LENGTH],
    }
    log.append(entry)

    # Cap the log size — keep the most recent entries
    if len(log) > MAX_ERROR_LOG_ENTRIES:
        log = log[-MAX_ERROR_LOG_ENTRIES:]

    data[ERROR_LOG_KEY] = log


def get_error_log(data: dict[str, Any]) -> list[dict]:
    """Get the current error log from agent data."""
    return data.get(ERROR_LOG_KEY, [])


def clear_error_log(data: dict[str, Any]) -> None:
    """Clear the error log (e.g., when starting a fresh task context)."""
    data.pop(ERROR_LOG_KEY, None)


def build_error_injection(data: dict[str, Any]) -> str:
    """
    Build the error context string to prepend to the next delegation message.
    
    ONLY injects BLOCKING errors. RECOVERED errors (nudges, self-corrected
    issues) are filtered out to prevent the orchestrator from re-delegating
    already-completed work. (RCA-270)
    
    If no BLOCKING errors are recorded, returns empty string (no injection).
    Shows the last 3 failures with their BLOCKING error signatures.
    
    Args:
        data: Agent's data dict (agent.data)
        
    Returns:
        Error context string to prepend, or "" if no BLOCKING errors
    """
    log = get_error_log(data)
    if not log:
        return ""

    # Filter to only entries with at least one BLOCKING error
    blocking_entries = []
    for entry in log:
        errors = entry.get("errors", [])
        blocking_errors = _extract_blocking_errors(errors)
        if blocking_errors:
            blocking_entries.append({**entry, "_blocking_errors": blocking_errors})

    if not blocking_entries:
        return ""

    total_failures = len(blocking_entries)
    # Show last 3 blocking failures
    recent = blocking_entries[-3:]

    lines = [
        f"## ⚠️ PREVIOUS SUBORDINATE FAILURES — {total_failures} failed attempt(s) so far (Do NOT repeat these)",
        "",
    ]

    for entry in recent:
        profile = entry.get("profile", "unknown")
        blocking_errors = entry.get("_blocking_errors", [])
        iteration = entry.get("iteration", "?")
        errors_str = "; ".join(blocking_errors[:3]) if blocking_errors else "unknown error"
        lines.append(f"- **Attempt {iteration}** ({profile}): {errors_str}")

    lines.extend([
        "",
        "You MUST try a DIFFERENT approach than what was attempted above.",
        "Do NOT repeat the same fixes that already failed.",
        "",
    ])

    return "\n".join(lines)


def _extract_blocking_errors(errors: list) -> list[str]:
    """Extract only BLOCKING error texts from classified error list.
    
    Handles both old format (list of strings) and new format
    (list of dicts with 'text' and 'severity' keys).
    """
    result = []
    for err in errors:
        if isinstance(err, dict):
            if err.get("severity", "blocking") == ErrorSeverity.BLOCKING.value:
                result.append(err.get("text", str(err)))
        elif isinstance(err, str):
            # Legacy format: classify on-the-fly
            if classify_error_severity(err) == ErrorSeverity.BLOCKING:
                result.append(err)
    return result


# ═══════════════════════════════════════════════════════════════════════
# P2: Diagnostic Carryover — Tool-call & file-level state relay
# ═══════════════════════════════════════════════════════════════════════
#
# Root Cause: Subordinates die, and the next subordinate starts from
# scratch with NO knowledge of what was already tried. Error signatures
# alone aren't enough — the next agent needs to know WHICH files were
# modified, WHICH tool calls were made, and WHAT the last build output was.

DIAGNOSTIC_LOG_KEY = "_subordinate_diagnostic_log"
MAX_DIAGNOSTIC_LOG_ENTRIES = 5   # Keep last N diagnostic snapshots
MAX_TRIED_FIXES = 10             # Cap number of tried fixes per entry

# Tool names that indicate file modifications
_FILE_WRITE_TOOLS = {"write_to_file", "replace_in_file", "save_file", "create_file"}
# Tool names that indicate build/exec attempts
_BUILD_TOOLS = {"code_execution_tool", "code_execution", "terminal"}


def extract_subordinate_diagnostics(history: list) -> dict:
    """Extract diagnostic context from failed subordinate's message history.

    Scans the history for:
    1. Tool calls made (tried_fixes)
    2. Files modified (modified_files)
    3. Last build output (last_build_output)

    Args:
        history: List of message objects from the subordinate's history

    Returns:
        Dict with keys: tried_fixes, modified_files, last_build_output
    """
    tried_fixes: list[str] = []
    modified_files: list[str] = []
    last_build_output: str = ""

    if not history:
        return {
            "tried_fixes": [],
            "modified_files": [],
            "last_build_output": "",
        }

    for msg in history:
        # Extract tool calls
        tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in tool_calls:
            tool_name = getattr(tc, "name", "") or ""
            tool_args = getattr(tc, "args", {}) or {}

            # Record file modifications
            if tool_name in _FILE_WRITE_TOOLS:
                filename = (
                    tool_args.get("filename", "")
                    or tool_args.get("path", "")
                    or tool_args.get("file", "")
                )
                if filename and filename not in modified_files:
                    modified_files.append(filename)

            # Record what was tried (summarize tool call)
            if tool_name:
                # Build a concise description of the action
                if tool_name in _FILE_WRITE_TOOLS:
                    desc = f"Modified {tool_args.get('filename', tool_args.get('path', 'file'))}"
                elif tool_name in _BUILD_TOOLS:
                    code = str(tool_args.get("code", tool_args.get("command", "")))[:100]
                    desc = f"Ran: {code}"
                else:
                    desc = f"Called {tool_name}"

                if desc not in tried_fixes:
                    tried_fixes.append(desc)

        # Extract build output from tool/system messages
        role = getattr(msg, "role", "")
        content = getattr(msg, "content", "") or ""
        if role in ("tool", "system") and content:
            # Keep the last build-related output
            if any(kw in content.lower() for kw in ["error", "failed", "npm err", "ts"]):
                last_build_output = content[:MAX_RESULT_PREVIEW_LENGTH]

    return {
        "tried_fixes": tried_fixes[:MAX_TRIED_FIXES],
        "modified_files": modified_files,
        "last_build_output": last_build_output,
    }


def record_subordinate_diagnostic(
    data: dict,
    profile: str,
    diagnostics: dict,
) -> None:
    """Record diagnostic state from a failed subordinate into agent data.

    Args:
        data: Agent's data dict (agent.data)
        profile: The subordinate's agent profile name
        diagnostics: Dict from extract_subordinate_diagnostics()
    """
    log: list = data.get(DIAGNOSTIC_LOG_KEY, [])

    entry = {
        "profile": profile,
        "iteration": len(log) + 1,
        "diagnostics": diagnostics,
    }
    log.append(entry)

    # Cap the log size
    if len(log) > MAX_DIAGNOSTIC_LOG_ENTRIES:
        log = log[-MAX_DIAGNOSTIC_LOG_ENTRIES:]

    data[DIAGNOSTIC_LOG_KEY] = log


def build_diagnostic_injection(data: dict) -> str:
    """Build diagnostic context string for next delegation message.

    Summarizes what was already tried (tool calls, modified files)
    so the next subordinate doesn't repeat failed strategies.

    Args:
        data: Agent's data dict (agent.data)

    Returns:
        Diagnostic context string, or "" if no diagnostics
    """
    log = data.get(DIAGNOSTIC_LOG_KEY, [])
    if not log:
        return ""

    lines = [
        "## ⚠️ ALREADY TRIED — Previous subordinate diagnostic state",
        "",
    ]

    for entry in log[-3:]:  # Show last 3
        profile = entry.get("profile", "unknown")
        diag = entry.get("diagnostics", {})
        iteration = entry.get("iteration", "?")
        tried = diag.get("tried_fixes", [])
        files = diag.get("modified_files", [])
        build_out = diag.get("last_build_output", "")

        lines.append(f"### Attempt {iteration} ({profile})")

        if tried:
            lines.append("**Actions taken:**")
            for fix in tried[:5]:
                lines.append(f"  - {fix}")

        if files:
            lines.append(f"**Modified files:** {', '.join(files)}")

        if build_out:
            # Truncate for injection
            preview = build_out[:500]
            lines.append(f"**Last build output:** `{preview}`")

        lines.append("")

    lines.extend([
        "DO NOT repeat the same modifications. Try a fundamentally different approach.",
        "",
    ])

    return "\n".join(lines)
