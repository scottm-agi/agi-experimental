"""
Tool Failure Tracker — tool_execute_after extension

Ported from Roo-Code's Task.didToolFailInCurrentTurn + consecutiveMistakeCount.

Runs AFTER every tool execution and inspects the response message for error
indicators. Sets two agent.data flags consumed by other extensions:

1. _tool_failed_in_current_turn (bool): True if ANY tool in the current
   message-loop iteration returned an error. Reset at the start of each
   new iteration by _06_tool_failure_reset.py. The completion gate blocks
   `response` when this flag is True.

2. _consecutive_mistake_count (int): Incremented on each error, reset to 0
   on each successful tool execution. When >= CONSECUTIVE_MISTAKE_THRESHOLD,
   the reset extension injects supervisor guidance.

3. _timeout_command_counts (dict[str, int]): Tracks per-command timeout
   retries. After MAX_TIMEOUT_RETRIES of the same command, injects a
   system message telling the agent to try a different approach (#1083).

COMPLEMENTARY to _50_error_supervisor_trigger.py which handles Python
exceptions/terminal failures via the event bus. This extension handles
"logical" errors visible in tool output text (build failures, missing
modules, npm errors, test failures, etc.).

Hooks into: tool_execute_after (order 12 — early, before gate at 22)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from python.helpers.extension import Extension
from python.helpers.output_truncation import truncate_output_middle_out
from python.helpers.tool_failure_weight import get_failure_weight, INFRASTRUCTURE_TOOLS

logger = logging.getLogger("agix.tool_failure_tracker")

# Max consecutive timeouts of the same command before injecting a redirect hint.
# Prevents unbounded retry loops like 'jest --coverage' being called 14 times (#1083).
MAX_TIMEOUT_RETRIES = 3

# Max consecutive failures of the same tool before injecting a strategy-change hint.
# Lowered from 3→2 (5-Why RCA, MSR smoke test 2026-04-18): At 3, the agent burns
# 3 LLM calls per cycle with advisory-only hints and counter reset, enabling
# infinite spin loops (75 iterations / 3 = 25 wasted hint cycles).
MAX_SAME_TOOL_RETRIES = 2

# S4 (RCA MSR_1777396305 RC-4): Universal circuit breaker thresholds.
# At WARN: inject a strong "change approach" system message.
# At ESCALATE: set a flag for supervisor to force-redirect.
CIRCUIT_BREAKER_WARN = 5
CIRCUIT_BREAKER_ESCALATE = 8

# RCA-251 §5.2 SessionHintEscalator: After this many cumulative spin-loop
# hints for the same tool across the ENTIRE session, escalate to the L2
# supervisor instead of repeating advisory hints. Reduces hint noise from
# ~315 events per run to single-digit escalations.
SESSION_HINT_ESCALATION_THRESHOLD = 5

# F-4 Escalation Ladder (Deep Audit MSR_Smoke_1777847233, 5-Why RCA):
# After HINT_ESCALATION_REDIRECT session hints → inject concrete alternative approach.
# After HINT_ESCALATION_BLOCK session hints → temporarily block the tool.
# User specified N=2 for first escalation tier (was previously advisory-only).
HINT_ESCALATION_REDIRECT = 2  # After 2 hints: inject redirect with alternative
HINT_ESCALATION_BLOCK = 4     # After 4 hints: temporarily block the tool

# RCA-356: Tool family normalization for MCP tool variants.
# MCP tools use 'server.tool_name' format (e.g., 'tavily-mcp.tavily_search'),
# which fragments per-tool failure counters. This map normalizes variants of
# the same logical tool to a single family key so counters accumulate correctly.
# Without this, the researcher can loop indefinitely by alternating between
# tavily_search, tavily-mcp.tavily_search, and tavily-mcp.tavily_research —
# each getting its own counter that never reaches the threshold.
_TOOL_FAMILY_MAP: dict[str, str] = {
    'tavily_search': 'tavily_search',
    'tavily_research': 'tavily_search',
    'tavily_extract': 'tavily_search',
    'tavily_crawl': 'tavily_search',
    'tavily_map': 'tavily_search',
    'perplexity_ask': 'perplexity_ask',
}

# P0-1 A-2: Test command patterns for mistake counter exemption.
# Test failures are data, not mistakes — they should NOT increment
# _consecutive_mistake_count. Hints are still injected so the agent
# gets feedback, but the circuit breaker / supervisor escalation path
# is not triggered by normal TDD red-green cycles.
TEST_COMMAND_PATTERNS = re.compile(
    r'\b(npm\s+(test|run\s+test)|npx\s+(vitest|jest)|pytest|python\s+-m\s+pytest)\b',
    re.IGNORECASE
)


def _is_test_command(tool_name: str, tool_args: dict) -> bool:
    """Detect whether a tool invocation is running a test command.

    P0-1 A-2: Test failures are data, not mistakes. When the agent runs
    npm test, vitest, jest, or pytest and the tests fail, the mistake
    counter should NOT be incremented. The agent is doing TDD — test
    failures are expected and informative.

    Args:
        tool_name: Name of the tool (only 'code_execution_tool' can be a test).
        tool_args: Arguments dict with 'code' or 'command' key.

    Returns:
        True if this is a test command execution, False otherwise.
    """
    if tool_name != "code_execution_tool":
        return False
    code = tool_args.get("code", "") or tool_args.get("command", "") or ""
    return bool(TEST_COMMAND_PATTERNS.search(code))


def _normalize_tool_family(tool_name: str) -> str:
    """Normalize MCP tool variants to a family key for counter tracking.

    RCA-356: Without normalization, 'tavily-mcp.tavily_search' and
    'tavily-mcp.tavily_research' are tracked as separate tools, so
    per-tool failure counters never reach their threshold.

    Examples:
        'tavily-mcp.tavily_search'      → 'tavily_search'
        'tavily-mcp.tavily_research'    → 'tavily_search'
        'perplexity-ask.perplexity_ask' → 'perplexity_ask'
        'code_execution_tool'           → 'code_execution_tool' (passthrough)
    """
    # Strip MCP server prefix: 'tavily-mcp.tavily_search' → 'tavily_search'
    base_name = tool_name.split('.')[-1] if '.' in tool_name else tool_name
    return _TOOL_FAMILY_MAP.get(base_name, tool_name)


def unblock_tool_on_success(
    agent_data: dict,
    tool_name: str,
    family_name: str | None = None,
) -> None:
    """Remove a tool from _tracker_blocked_tools on successful execution.

    RCA-371: _tracker_blocked_tools was write-only — tools added at TIER 3
    were NEVER removed, even after the underlying issue was fixed. This
    created a Catch-22: the agent couldn't verify its code fixes because
    code_execution_tool stayed permanently blocked.

    This function MUST be called on the success path alongside the existing
    _tool_failure_counts reset (line ~780).

    Args:
        agent_data: The agent's data dict.
        tool_name: The raw tool name (e.g., 'code_execution_tool').
        family_name: The normalized family name if different (e.g., 'code_execution').
    """
    blocked_tools: set = agent_data.get("_tracker_blocked_tools", set())
    if not blocked_tools:
        return

    removed = False
    if tool_name in blocked_tools:
        blocked_tools.discard(tool_name)
        removed = True
    if family_name and family_name in blocked_tools:
        blocked_tools.discard(family_name)
        removed = True

    if removed:
        agent_data["_tracker_blocked_tools"] = blocked_tools
        logger.info(
            f"[TOOL BLOCK UNBLOCK] Unblocked '{tool_name}'"
            f"{f' (family: {family_name})' if family_name else ''} "
            f"after successful execution. Remaining blocks: {blocked_tools or 'none'}"
        )

# F-3b: Profile-specific circuit breaker recommendation overrides.
# When a circuit breaker fires, these profile-keyed messages are appended
# to the generic tool recommendations to give contextually appropriate advice.
# Profiles not listed here fall through to the generic advice only.
PROFILE_RECOMMENDATION_OVERRIDES: dict[str, str] = {
    "debug": (
        "- As a debug agent, run a diagnosis on the failing component and "
        "report your findings to the orchestrator rather than retrying blindly"
    ),
    "code": (
        "- As a code agent, try write_to_file to create the file directly "
        "instead of running shell commands that keep failing"
    ),
}

# Patterns that indicate a timeout occurred (from code_execution.py timeout handlers)
# These match the actual framework prompt text from prompts/fw.code.*.md
TIMEOUT_PATTERNS = [
    re.compile(r"Returning control to agent after \d+ seconds", re.IGNORECASE),
    re.compile(r"with no output", re.IGNORECASE),
    re.compile(r"since last output update", re.IGNORECASE),
    re.compile(r"seconds of execution.*still running", re.IGNORECASE),
    re.compile(r"No output was produced", re.IGNORECASE),  # Legacy/fallback
]

def _extract_timeout_command(text: str) -> Optional[str]:
    """Extract the command from a timeout message.
    
    Timeout output format: 'bash> <command>\n\n[system message] No output...'
    Returns the normalized command string, or None if not a timeout.
    """
    if not text:
        return None
    
    # Check if this is actually a timeout
    is_timeout = any(p.search(text) for p in TIMEOUT_PATTERNS)
    if not is_timeout:
        return None
    
    # Extract command from 'bash> <command>' prefix
    match = re.match(r"^(?:bash|PS|node)>\s*(.+?)\n", text, re.DOTALL)
    if match:
        cmd = match.group(1).strip()
        # Normalize: remove varying args like timestamps, truncate
        cmd = cmd[:100]  # Normalize length
        return cmd
    
    return None

# Tools whose output text should NOT be inspected for error indicators.
# These are tools where the response MESSAGE is not a reliable error signal:
#   - Meta-tools: output contains delegated/user context, not errors.
#   - Content-producing tools: output contains source code with try/catch/throw
#     patterns. Their REAL errors (missing path, FileGuard, permission denied)
#     are caught by the framework's exception handler in agent_process_tools.py
#     (line 646) via Python exceptions — they never reach this extension.
#
# The ONLY tool that needs content scanning is code_execution_tool, where
# shell commands fail inside the terminal (exit code != 0) but the tool
# itself returns successfully.
EXCLUDED_TOOLS = {
    # Meta-tools
    "response",                  # Final response to user — never an "error"
    "call_subordinate",          # Subordinate results have their own error context
    "call_subordinate_batch",    # Batch delegation — same as above
    "fan_out_subordinates",      # Fan-out delegation
    "input",                     # User input — not an error source
    "wait",                      # Intentional waiting
    "behaviour_adjustment",      # Meta-tool for self-regulation
    # Content-producing tools — real errors flow through Python exceptions
    # (caught by framework at agent_process_tools.py:646), not through
    # response.message. Scanning their output for error keywords generates
    # 8,500+ false positives from source code patterns.
    "write_to_file",
    "replace_in_file",
    "save_to_file",
    # Read tools — output is raw file content, naturally contains
    # error-handling patterns (throw new Error, console.error, try/catch)
    # that would cause false positive error detection. Real errors
    # (missing path, FileGuard, permission denied) flow through Python
    # exceptions at agent_process_tools.py:646, not through response.message.
    # F-2 fix: Without this, Researcher agents reading source code get
    # false-positive failure detections that escalate to TIER 2 REDIRECT.
    "read_file",
    "search_files",
    "list_dir",
    "list_code_definition_names",
    # Planning/guidance tools — output is instructional markdown containing
    # error keyword examples (e.g., Known Error Patterns table). Not terminal output.
    "node_project",
}

# RCA-301 Issue 5: Tools that must NEVER be Tier 3 blocked.
# These are diagnostic / read-only tools. Blocking them creates a death
# spiral: the agent can't read context -> can't fix the issue -> retries
# blindly -> more failures.  Cap these at Tier 2 (redirect) only.
NEVER_BLOCK_TOOLS = frozenset({
    "read_file",
    "list_dir",
    "list_code_definition_names",
    "search_files",
    "search_replace",
    "ast_symbol_search",
    "browser_agent",
    # RCA-400: Verification tools must NEVER be TIER 3 blocked.
    # Blocking code_execution_tool creates a Catch-22: the agent can't
    # verify its build fixes → can't succeed → can't unblock → diagnostic loop.
    # Cap at TIER 2 (redirect) instead.
    "code_execution_tool",
    "code_execution",
    # Planning tools must never be blocked — they provide guidance, not execution
    "node_project",
})

# Exit code pattern for terminal output
_EXIT_CODE_PATTERN = re.compile(r"exit code (\d+)", re.IGNORECASE)

# Patterns that indicate a tool execution failed logically.
# Each tuple: (compiled regex, human-readable label)
ERROR_PATTERNS = [
    (re.compile(r"error:", re.IGNORECASE), "error keyword"),
    (re.compile(r"Error:"), "Error keyword"),
    (re.compile(r"failed", re.IGNORECASE), "failed keyword"),
    (re.compile(r"ENOENT", re.IGNORECASE), "ENOENT"),
    (re.compile(r"Cannot find module", re.IGNORECASE), "missing module"),
    (re.compile(r"ModuleNotFoundError", re.IGNORECASE), "python module missing"),
    (re.compile(r"command not found", re.IGNORECASE), "command missing"),
    (re.compile(r"Permission denied", re.IGNORECASE), "permission denied"),
    (re.compile(r"FATAL", re.IGNORECASE), "fatal error"),
    (re.compile(r"Traceback \(most recent call last\)"), "python traceback"),
    (re.compile(r"npm ERR!", re.IGNORECASE), "npm error"),
    (re.compile(r"Build failed", re.IGNORECASE), "build failure"),
    (re.compile(r"SyntaxError", re.IGNORECASE), "syntax error"),
    (re.compile(r"TypeError", re.IGNORECASE), "type error"),
    (re.compile(r"ReferenceError", re.IGNORECASE), "reference error"),
    (re.compile(r"exit code [1-9]", re.IGNORECASE), "non-zero exit"),
    (re.compile(r"FAIL\b"), "test failure"),
]

# Patterns that look like errors but are actually benign (false positives)
FALSE_POSITIVE_PATTERNS = [
    re.compile(r"0 errors?", re.IGNORECASE),
    re.compile(r"no errors?", re.IGNORECASE),
    re.compile(r"error.{0,3}free", re.IGNORECASE),
    re.compile(r"successfully", re.IGNORECASE),
    re.compile(r"Build completed", re.IGNORECASE),
    re.compile(r"compiled successfully", re.IGNORECASE),
    re.compile(r"All \d+ tests? passed", re.IGNORECASE),
    re.compile(r"PASS\b"),
    # Timeout messages should NOT be treated as generic errors — they have
    # their own dedicated tracking via _timeout_command_counts (#1083)
    re.compile(r"Returning control to agent after", re.IGNORECASE),
    # 5-Why RCA (Iteration 139): Dev server noise false positives.
    # These patterns appear in dev server output and npm install logs
    # but are NOT tool execution failures. They generated 500+ false
    # warnings per smoke test, drowning real errors.
    re.compile(r"DeprecationWarning:", re.IGNORECASE),
    re.compile(r"npm WARN", re.IGNORECASE),
    re.compile(r"ExperimentalWarning:", re.IGNORECASE),
    re.compile(r"Warning: .* is using incorrect casing", re.IGNORECASE),  # React casing warnings
    re.compile(r"Compiled .* with warnings", re.IGNORECASE),  # Next.js compiled-with-warnings
    re.compile(r"exit code 0", re.IGNORECASE),  # Explicit success exit code
]

# U-9 (RCA-314 5-Why #2): Transient/environmental error patterns.
# These are real errors (something failed to display/encode), but they are
# NOT tool execution failures — the underlying command may have succeeded.
# They occur when Python's stdout capture encounters non-UTF-8 output from
# npm/node processes. Matching these EXEMPTS the line from failure counting
# while still logging a warning.
# Category: "transient/environmental — log but don't count"
TRANSIENT_ERROR_PATTERNS = [
    re.compile(r"UnicodeEncodeError", re.IGNORECASE),
    re.compile(r"UnicodeDecodeError", re.IGNORECASE),
    re.compile(r"surrogatepass", re.IGNORECASE),
    re.compile(r"charmap.*codec", re.IGNORECASE),
    re.compile(r"codec can't encode", re.IGNORECASE),
    re.compile(r"codec can't decode", re.IGNORECASE),
]

# RCA-322: Patterns that indicate INFRASTRUCTURE failures (tool itself broken).
# These override exit-code classification — if present, failure is always "infra".
INFRA_ERROR_PATTERNS = [
    re.compile(r"Permission denied", re.IGNORECASE),
    re.compile(r"command not found", re.IGNORECASE),
    re.compile(r"SIGKILL|SIGTERM|signal \d+", re.IGNORECASE),
    re.compile(r"Cannot connect|Connection refused", re.IGNORECASE),
    re.compile(r"out of memory|ENOMEM", re.IGNORECASE),
    re.compile(r"segmentation fault|segfault", re.IGNORECASE),
]

# RCA-322: Patterns that confirm BUILD/TEST errors (code bugs, not tool bugs).
# Only applied to code_execution_tool with exit codes 1-2.
BUILD_CODE_ERROR_PATTERNS = [
    re.compile(r"error TS\d+", re.IGNORECASE),
    re.compile(r"ELIFECYCLE", re.IGNORECASE),
    re.compile(r"Build failed|Build error", re.IGNORECASE),
    re.compile(r"Failed to compile", re.IGNORECASE),
    re.compile(r"test.*fail|FAIL\b", re.IGNORECASE),
    re.compile(r"SyntaxError:", re.IGNORECASE),
    re.compile(r"Module not found|Cannot find module", re.IGNORECASE),
    re.compile(r"Compilation failed", re.IGNORECASE),
    re.compile(r"lint.*error", re.IGNORECASE),
    re.compile(r"TypeError:|ReferenceError:", re.IGNORECASE),
]

# G-2 (RCA-353b): Additional source code detection patterns.
# Numbered line format: lines starting with "123: " (file viewer output).
_NUMBERED_LINE_RE = re.compile(r'^\d+:\s')
# Code statement keywords: lines starting with common language keywords.
_CODE_STATEMENT_RE = re.compile(
    r'^\s*(?:import|export|from|const|let|var|class|def|function|async|return|'
    r'if|for|while|try|catch|except|finally|with|raise|throw|yield)\b'
)


def _is_source_code_context(text: str) -> bool:
    """Detect if text is primarily source code (heredoc, numbered lines, or raw code).

    Returns True if the output is detected as source code via any of:
    1. Heredoc blocks (cat > file << 'EOF' ... EOF) with all errors inside
    2. Numbered line format (≥60% of lines match '^\\\\d+:\\\\s')
    3. Code statement keywords (≥60% of lines start with import/def/class/etc.)

    Iteration 130 — P0 fix for false-positive tool failure signals.
    G-2 (RCA-353b) — Defense-in-depth: added numbered-line and code-statement detection.
    """
    lines = text.split("\n")

    # ── Identify heredoc regions ──────────────────────────────────────
    # A heredoc starts with: cat ... << 'DELIM' or cat ... << DELIM
    # and ends with a line that is exactly DELIM (possibly with whitespace)
    heredoc_start = re.compile(
        r"(?:cat|tee)\b.*<<\s*['\"]?(\w+)['\"]?\s*$", re.IGNORECASE
    )
    in_heredoc = False
    heredoc_delim = ""
    heredoc_line_indices: set = set()

    for i, line in enumerate(lines):
        if in_heredoc:
            heredoc_line_indices.add(i)
            if line.strip() == heredoc_delim:
                in_heredoc = False
        else:
            m = heredoc_start.search(line)
            if m:
                heredoc_delim = m.group(1)
                in_heredoc = True
                heredoc_line_indices.add(i)

    if heredoc_line_indices:
        # ── Check if ALL error-matching lines are inside heredoc blocks ───
        all_errors_in_heredoc = True
        for i, line in enumerate(lines):
            if i in heredoc_line_indices:
                continue  # Skip lines inside heredocs
            for pattern, _label in ERROR_PATTERNS:
                if pattern.search(line):
                    # Found a REAL error outside a heredoc — not just source code
                    all_errors_in_heredoc = False
                    break
            if not all_errors_in_heredoc:
                break

        if all_errors_in_heredoc:
            return True

    # ── G-2 (RCA-353b): Detect source code output formats ────────────
    # Numbered-line format (file viewers) or raw code statements.
    non_empty = [l for l in lines if l.strip()]
    if len(non_empty) >= 3:
        code_lines = sum(
            1 for l in non_empty
            if _NUMBERED_LINE_RE.match(l) or _CODE_STATEMENT_RE.match(l)
        )
        if code_lines / len(non_empty) > 0.6:
            return True

    return False


def _has_error_indicators(text: str) -> bool:
    """Check if text contains error indicators, net of false positives.

    Iteration 130: Added code-context awareness — if all error patterns
    are inside heredoc blocks (source code being written to disk), they
    are suppressed as false positives.
    
    Iteration 139 (5-Why RCA): Changed from full-text false-positive check
    to LINE-LEVEL analysis. The old approach suppressed the entire output
    if ANY false positive matched ANYWHERE — hiding real errors when they
    co-occurred with benign warnings (e.g., 'npm WARN' + 'Cannot find module'
    in the same npm install output). Now: a line is only suppressed if IT
    matches a false positive. If ANY line has an error WITHOUT a false
    positive on that same line, the output is treated as an error.
    """
    if not text or len(text) < 10:
        return False

    # Quick check: does it contain any error pattern?
    found_error = False
    for pattern, _label in ERROR_PATTERNS:
        if pattern.search(text):
            found_error = True
            break

    if not found_error:
        return False

    # LINE-LEVEL analysis: check each line individually
    # A line is a "real error" only if it matches an error pattern
    # AND does NOT match any false positive pattern on that same line
    # AND does NOT match a transient/environmental pattern (U-9).
    real_error_lines = 0
    for line in text.split("\n"):
        line_stripped = line.strip()
        if not line_stripped:
            continue
        
        # Does this line match any error pattern?
        line_has_error = False
        for pattern, _label in ERROR_PATTERNS:
            if pattern.search(line_stripped):
                line_has_error = True
                break
        
        if not line_has_error:
            continue
        
        # Does this same line also match a false positive?
        line_is_false_positive = False
        for fp_pattern in FALSE_POSITIVE_PATTERNS:
            if fp_pattern.search(line_stripped):
                line_is_false_positive = True
                break
        
        if line_is_false_positive:
            continue

        # U-9: Does this line match a transient/environmental pattern?
        # Encoding errors are real (something failed to display), but they
        # are NOT tool execution failures. Log but don't count.
        line_is_transient = False
        for tp in TRANSIENT_ERROR_PATTERNS:
            if tp.search(line_stripped):
                line_is_transient = True
                break

        if not line_is_transient:
            real_error_lines += 1
    
    if real_error_lines == 0:
        return False  # All error-matching lines were false positives

    # P0 (Iteration 130): Code-context awareness — if all error patterns
    # are inside heredoc/file-write blocks, this is source code, not a failure
    if _is_source_code_context(text):
        return False

    return True


def _detect_tool_failure(
    tool_name: str, response: Any
) -> "bool | None":
    """Deterministic failure detection for tool responses.

    Returns:
        True  — tool output indicates a failure
        False — tool output indicates success
        None  — tool is excluded from tracking (meta-tools, content-producers)

    Architecture:

    This tracker exists for ONE purpose: detecting when a shell command
    fails inside code_execution_tool. That's the only case where a
    command can fail (exit code != 0, npm ERR!, etc.) while the TOOL
    itself returns successfully.

    All other tool errors flow through Python exceptions, which are caught
    by the framework's exception handler (agent_process_tools.py:646) and
    recorded with success=False via ObserverMesh. Those never reach this
    extension.

    Detection layers:
    1. Structured signal: response.additional["is_error"] / ["success"]
    2. Exit code: parse "exit code N" from terminal output
    3. Regex fallback: text pattern scan (existing ERROR_PATTERNS)
    """
    tool_lower = tool_name.lower() if tool_name else ""

    # ── Skip excluded tools entirely ──
    if tool_lower in EXCLUDED_TOOLS:
        return None

    # ── Extract response text ──
    msg = ""
    if hasattr(response, "message") and response.message:
        msg = str(response.message)
    elif isinstance(response, str):
        msg = response

    if not msg:
        return False  # No output = no detectable failure

    # ── LAYER 1: Structured signal (highest trust) ──
    additional = getattr(response, "additional", None)
    if additional and isinstance(additional, dict):
        if "is_error" in additional:
            return bool(additional["is_error"])
        if "success" in additional:
            return not bool(additional["success"])

    # ── LAYER 2: Exit code parsing (terminal output) ──
    exit_match = _EXIT_CODE_PATTERN.search(msg)
    if exit_match:
        exit_code = int(exit_match.group(1))
        return exit_code != 0

    # ── LAYER 2.5 (U-9): Transient error pre-check ──────────────
    # If the output contains ONLY transient errors (encoding/codec) and
    # no other real error indicators, treat as success. This prevents
    # UnicodeEncodeError tracebacks from incrementing failure counts.
    has_transient = any(tp.search(msg) for tp in TRANSIENT_ERROR_PATTERNS)
    if has_transient:
        # Check if there are ALSO real errors mixed in
        # (handled by _has_error_indicators's line-level analysis)
        if not _has_error_indicators(msg):
            logger.info(
                f"[TOOL FAILURE TRACKER] Transient encoding error in "
                f"{tool_lower} output — exempted from failure count"
            )
            return False

    # ── LAYER 3: Regex text scanning (fallback) ──
    return _has_error_indicators(msg)


def _classify_failure_type(
    tool_name: str, msg: str, exit_code: "int | None" = None
) -> str:
    """Classify whether a detected failure is infrastructure or code-level.

    RCA-322: Build errors (TypeScript, npm, test failures) are CODE BUGS —
    they should not escalate to TIER 3 tool blocking. Only infrastructure
    failures (command not found, permission denied, OOM, crashes) should
    escalate.

    Returns:
        "infra"   — infrastructure failure (command not found, permission denied, crash)
        "code"    — build/test failure (TypeScript errors, npm lifecycle, test failures)
        "unknown" — can't classify (defaults to infra behavior for safety)
    """
    # High exit codes (>=126) are almost always infrastructure issues
    if exit_code is not None and exit_code >= 126:
        return "infra"

    # Check for infra patterns — these ALWAYS mean infra, regardless of exit code
    has_infra = any(p.search(msg) for p in INFRA_ERROR_PATTERNS)
    if has_infra:
        return "infra"

    # Check for build/code patterns — only code_execution_tool produces these
    tool_lower = (tool_name or "").lower()
    if tool_lower in {"code_execution_tool", "code_execution"}:
        has_build = any(p.search(msg) for p in BUILD_CODE_ERROR_PATTERNS)
        if has_build:
            # RCA-400: Removed exit_code gate. Build errors from `next build`,
            # webpack, etc. often omit "exit code N" in output. If the text
            # clearly matches BUILD_CODE_ERROR_PATTERNS, it IS a code error
            # regardless of exit code presence. The old `exit_code is not None
            # and exit_code in (1, 2)` guard caused misclassification as
            # "unknown", which escalated to TIER 3 blocking.
            return "code"

    # Can't classify → safe default
    return "unknown"


class ToolFailureTracker(Extension):
    """Track tool execution failures via layered detection."""

    async def execute(self, tool_name: str = "", response: Any = None, **kwargs):
        if not tool_name or response is None:
            return

        # Extract response text (still needed for timeout tracking)
        msg = ""
        if hasattr(response, "message") and response.message:
            msg = str(response.message)
        elif isinstance(response, str):
            msg = response

        if not msg:
            return

        # ─── TIMEOUT-SPECIFIC TRACKING (#1083) ─────────────────────
        timeout_cmd = _extract_timeout_command(msg)
        if timeout_cmd:
            await self._track_timeout_retry(timeout_cmd)

        # ─── A-1 WIRING: Record test output for progress tracking ────
        # Must run BEFORE failure detection so output is recorded on both
        # success and failure paths. Without this, has_test_output_changed()
        # in agent.py has no data and the progress exemption is blind.
        tool_args = kwargs.get("tool_args", {}) or {}
        if _is_test_command(tool_name, tool_args):
            from python.helpers.same_message_bridge import record_test_output
            record_test_output(self.agent.data, msg)

        # ─── LAYERED FAILURE DETECTION ─────────────────────────────
        failure_result = _detect_tool_failure(tool_name, response)
        if failure_result is None:
            return  # Meta-tool — excluded from tracking

        if failure_result:
            # ─── ERROR PATH ──────────────────────────────────────────
            self.agent.data["_tool_failed_in_current_turn"] = True

            # ─── RCA-322: CLASSIFY FAILURE TYPE ──────────────────────
            exit_match = _EXIT_CODE_PATTERN.search(msg)
            exit_code = int(exit_match.group(1)) if exit_match else None
            failure_type = _classify_failure_type(tool_name, msg, exit_code)

            # ─── ITR-29b: AUTH ERROR ESCAPE HATCH ────────────────────
            # Detect consecutive API auth errors (401/403/invalid key)
            # and inject a hard SKIP directive. Without this, the agent
            # burns 20+ iterations trying to fix unfixable API keys.
            try:
                from python.helpers.auth_error_detector import is_auth_error, AuthErrorTracker
                if is_auth_error(msg):
                    # Get or create tracker on agent.data
                    auth_tracker = self.agent.data.get("_auth_error_tracker")
                    if auth_tracker is None:
                        auth_tracker = AuthErrorTracker(threshold=3)
                        self.agent.data["_auth_error_tracker"] = auth_tracker
                    escape = auth_tracker.record(msg)
                    if escape:
                        await self.agent.hist_add_warning(escape)
                        logger.error(
                            f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                            f"AUTH ESCAPE HATCH — {auth_tracker._count} consecutive "
                            f"auth errors, injecting SKIP directive"
                        )
                        # A6/RCA-475: Wire mark_failed for requirements linked to
                        # the current delegation. Auth failures are structurally
                        # unrecoverable — the API key is missing/invalid.
                        try:
                            from python.helpers.requirements_delegation_tracker import mark_failed
                            # Get requirement_ids from agent.data delegation context
                            req_ids = self.agent.data.get("_current_delegation_requirement_ids", [])
                            if not req_ids:
                                # Fallback: check ledger for assigned reqs
                                ledger = self.agent.data.get("_requirements_ledger", {})
                                for req in ledger.get("requirements", []):
                                    if req.get("status") == "assigned":
                                        req_ids.append(req["id"])
                            for req_id in req_ids:
                                mark_failed(
                                    self.agent.data,
                                    req_id,
                                    reason=f"Auth escape hatch: {auth_tracker._count} "
                                           f"consecutive auth errors (API key unavailable)",
                                )
                            if req_ids:
                                logger.warning(
                                    f"[TOOL FAILURE TRACKER] A6: Marked {len(req_ids)} "
                                    f"requirements as FAILED (auth escape): "
                                    f"{', '.join(str(r) for r in req_ids[:5])}"
                                )
                        except Exception as e:
                            logger.debug(f"[TOOL FAILURE TRACKER] A6 mark_failed wiring failed: {e}")
                else:
                    # Non-auth error — reset the auth tracker streak
                    auth_tracker = self.agent.data.get("_auth_error_tracker")
                    if auth_tracker:
                        auth_tracker.reset()
            except ImportError:
                pass  # Module not available — skip gracefully

            # Store last error snippet per tool for context-aware hints
            # (recorded regardless of failure type).
            error_ctx: dict = self.agent.data.get("_tool_failure_error_context", {})
            error_ctx[tool_name] = truncate_output_middle_out(
                msg, max_lines=30, max_chars=600, head_ratio=0.3
            )
            self.agent.data["_tool_failure_error_context"] = error_ctx

            # ─── ErrorLedger recording (#1185) ──────────────────────
            try:
                from python.helpers.error_ledger import get_error_ledger, ErrorEntry
                context_id = self.agent.context.id if self.agent.context else None
                if context_id:
                    get_error_ledger().record(context_id, ErrorEntry(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source="tool",
                        severity="low" if failure_type == "code" else "medium",
                        summary=truncate_output_middle_out(
                            msg, max_lines=5, max_chars=200, head_ratio=0.5
                        ),
                        details=truncate_output_middle_out(
                            msg, max_lines=30, max_chars=600, head_ratio=0.3
                        ),
                        tool_name=tool_name,
                        five_why_hint=(
                            f"Tool '{tool_name}' returned an error. Before retrying: "
                            f"1) Check arguments are correct, "
                            f"2) Verify the target resource exists, "
                            f"3) Try a different approach if this keeps failing."
                        ),
                    ))
            except Exception:
                pass  # Ledger recording must never break tool execution

            # ─── RCA-322 + ITR-35 RC-W3: BRANCH ON FAILURE TYPE ────────
            if failure_type == "code":
                # BUILD/TEST ERROR: Inject hint but ALSO track for escalation.
                # ITR-35 RC-W3 FIX: Previously this had `return` which skipped
                # ALL counter increments, creating an "escalation black hole"
                # where 20+ consecutive build failures never triggered the
                # circuit breaker or supervisor. Now: code failures increment
                # _consecutive_mistake_count at weight 1.0 and fall through
                # to the standard escalation path. The BuildLoopDetector
                # (_24_build_loop_hook.py) still handles build-specific
                # diagnostics separately.
                build_hint = (
                    f"🔧 BUILD/TEST ERROR in `{tool_name}` — this is a code bug, "
                    f"not a tool failure. Fix the underlying errors and retry.\n\n"
                    f"The tool is NOT blocked — you can keep using it."
                )
                await self.agent.hist_add_warning(build_hint)
                logger.info(
                    f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                    f"Build/test error in {tool_name} (classified as 'code', "
                    f"tracking with standard weight)"
                )
                # Fall through to escalation path below (no return)


            # ─── INFRA/UNKNOWN ERROR: Full escalation path ──────────
            # RCA-356 §2c: Verification/test script failures count at half
            # weight (0.5) to avoid premature circuit breaker escalation.
            tool_args = kwargs.get("tool_args", {})
            weight = get_failure_weight(tool_name, tool_args)

            # P0-1 A-2: Test failures are data, not mistakes.
            # When the agent runs a test command (npm test, vitest, jest,
            # pytest) and it fails, the mistake counter is NOT incremented.
            # Hints are still injected (above) so the agent gets feedback,
            # but the circuit breaker / supervisor escalation path is not
            # triggered by normal TDD red-green cycles.
            is_test = _is_test_command(tool_name, tool_args or {})
            if is_test:
                logger.info(
                    f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                    f"Test command failure in {tool_name} — exempted from "
                    f"mistake counter (test failures are data, not mistakes)"
                )
                # §12: TDD cycle state awareness — log when TDD mode is active
                tdd_state = self.agent.data.get("_tdd_cycle_state")
                if tdd_state and isinstance(tdd_state, dict):
                    if tdd_state.get("phase") not in ("IDLE", "COMPLETE", None):
                        logger.info(
                            f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                            f"TDD cycle active — test failure fully exempted "
                            f"from all escalation paths"
                        )
                # Still fall through to same-tool spin detection below,
                # but skip counter increment and circuit breaker path.
            else:
                count = self.agent.data.get("_consecutive_mistake_count", 0) + weight
                self.agent.data["_consecutive_mistake_count"] = count

                # ITR-20 F-5: Track which tool is accumulating failures.
                # On SUCCESS, only reset _consecutive_mistake_count if the
                # succeeding tool matches this. Prevents planning tools
                # (node_project) from resetting execution tool failure counters.
                self.agent.data["_last_consecutive_fail_tool"] = _normalize_tool_family(tool_name)

                logger.warning(
                    f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                    f"Error detected in {tool_name} output "
                    f"(consecutive_mistakes={count}, type={failure_type})"
                )

                # ─── S4: UNIVERSAL CIRCUIT BREAKER ──────────────────────
                # At CIRCUIT_BREAKER_ESCALATE: set flag for supervisor redirect
                if count >= CIRCUIT_BREAKER_ESCALATE:
                    escalate_msg = (
                        f"🚨 [CIRCUIT BREAKER — HARD ESCALATION] {count} consecutive "
                        f"failures on `{tool_name}`. ESCALATING to supervisor. "
                        f"Stop retrying and await redirect."
                    )
                    await self.agent.hist_add_warning(escalate_msg)
                    self.agent.data["_circuit_breaker_triggered"] = True
                    self.agent.data["_circuit_breaker_tool"] = tool_name
                    self.agent.data["_circuit_breaker_count"] = count

                    # P0-1 Shadow-mode dual-write: record circuit breaker in new RetryBudgetManager.
                    # Old counters still drive all decisions. Shadow call is passive.
                    from python.helpers.retry_budget_bridge import shadow_blocked_tools_event
                    shadow_blocked_tools_event(
                        self.agent.data, old_decision_would_stop=True,
                    )

                    # RCA-289 wiring fix: Emit L1 escalation signal so L2
                    # IntelligentSupervisor is immediately triggered on next
                    # message_loop_start. Previously these flags were write-only.
                    signals: list = self.agent.data.get("_l2_escalation_signals", [])
                    signals.append({
                        "source": "tool_failure_tracker",
                        "detector": "circuit_breaker",
                        "severity": "critical",
                        "detail": (
                            f"Circuit breaker triggered: {tool_name} has failed "
                            f"{count} consecutive times. Agent must change approach."
                        ),
                    })
                    self.agent.data["_l2_escalation_signals"] = signals

                    logger.error(
                        f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                        f"CIRCUIT BREAKER ESCALATE at {count} for {tool_name}"
                    )
                elif count >= CIRCUIT_BREAKER_WARN:
                    # At CIRCUIT_BREAKER_WARN: inject strong "change approach" message
                    # SS-5 Fix: Profile-aware recommendations via helper function
                    from python.helpers.blocked_response_builder import get_profile_aware_tool_recommendations
                    agent_profile = getattr(self.agent.config, "profile", "") or ""
                    profile_advice = get_profile_aware_tool_recommendations(
                        tool_name, agent_profile
                    )
                    # F-3b: Append profile-specific override if available
                    profile_override = PROFILE_RECOMMENDATION_OVERRIDES.get(agent_profile, "")
                    if profile_override:
                        profile_advice = f"{profile_advice}\n{profile_override}"
                    warn_msg = (
                        f"⚠️ [CIRCUIT BREAKER — WARNING] {count} consecutive "
                        f"failures on `{tool_name}`. You MUST try a DIFFERENT approach:\n"
                        f"{profile_advice}\n"
                        f"Try a DIFFERENT approach — do not repeat the same failing pattern."
                    )
                    await self.agent.hist_add_warning(warn_msg)
                    logger.warning(
                        f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                        f"CIRCUIT BREAKER WARN at {count} for {tool_name}"
                    )

            # ─── SAME-TOOL SPIN DETECTION (#iter72) ─────────────────
            await self._track_same_tool_failures(tool_name)
        else:
            # ─── SUCCESS PATH — reset consecutive count ──────────────
            # ITR-20 F-5: Only reset _consecutive_mistake_count if the
            # succeeding tool is the SAME tool (or family) as the one that
            # was accumulating failures. An unrelated tool's success
            # (e.g., node_project succeeding while code_execution_tool is
            # failing) must NOT reset the counter. This is the same bug
            # class as RCA-356 RC-1 (which fixed _tool_failure_counts
            # but missed _consecutive_mistake_count).
            family_name = _normalize_tool_family(tool_name)
            last_fail_tool = self.agent.data.get("_last_consecutive_fail_tool", "")
            prev = self.agent.data.get("_consecutive_mistake_count", 0)

            if prev > 0:
                if last_fail_tool and last_fail_tool != family_name:
                    # Different tool succeeded — do NOT reset the counter.
                    # The failing tool hasn't recovered yet.
                    logger.info(
                        f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                        f"Success in {tool_name} (family: {family_name}), but "
                        f"last failing tool was {last_fail_tool} — NOT resetting "
                        f"consecutive_mistakes ({prev})"
                    )
                else:
                    # Same tool (or family) succeeded — reset the counter.
                    logger.info(
                        f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                        f"Success in {tool_name}, resetting consecutive_mistakes "
                        f"({prev} → 0)"
                    )
                    self.agent.data["_consecutive_mistake_count"] = 0
                    self.agent.data["_last_consecutive_fail_tool"] = ""
            else:
                # No failures to reset — noop
                pass

            # Clear timeout counters on genuine success (NOT on timeout messages,
            # which reach this branch because they're false-positives for errors)
            if not timeout_cmd:
                self.agent.data["_timeout_command_counts"] = {}

                # ── ITR-48: Auto-resolve UEM errors on success ────────────
                # The same hook that records errors also sees success output.
                # When a build/test passes, resolve matching error categories
                # so they don't leak into delegation results and cause the
                # orchestrator to hallucinate Recovery tasks.
                try:
                    from python.helpers.universal_error_manager import UniversalErrorManager
                    uem = UniversalErrorManager(self.agent)
                    uem.resolve_errors_on_success(tool_name, msg)
                except Exception as resolve_err:
                    logger.debug(f"[UEM RESOLVE] Auto-resolution failed (non-fatal): {resolve_err}")

                # RCA-356 RC-1 Fix: Only reset the SPECIFIC tool's failure counter.
                # The old code reset ALL counters (`_tool_failure_counts = {}`),
                # which erased failure memory for other tools. This enabled
                # cross-tool loops: perplexity fails → tavily succeeds → perplexity
                # counter resets to 0 → perplexity fails again → never escalates.
                tool_counts = self.agent.data.get("_tool_failure_counts", {})
                if family_name in tool_counts:
                    del tool_counts[family_name]
                # Also clear the raw name in case it was tracked before normalization
                if tool_name in tool_counts and tool_name != family_name:
                    del tool_counts[tool_name]
                self.agent.data["_tool_failure_counts"] = tool_counts

                # RCA-371: Also clear tool from _tracker_blocked_tools.
                # Previously, TIER 3 blocked tools were NEVER unblocked,
                # creating a Catch-22 where the agent couldn't verify fixes.
                unblock_tool_on_success(
                    self.agent.data, tool_name, family_name=family_name
                )

                # RCA-371 Cooldown: After COOLDOWN_UNBLOCK_AFTER successful
                # tool calls, unblock ALL TIER 3 blocked tools. This solves
                # the Catch-22: agent uses write_to_file to fix code, and
                # after enough fixes, gets code_execution_tool back to verify.
                # If the tool fails again, it'll be re-blocked naturally.
                COOLDOWN_UNBLOCK_AFTER = 3
                blocked = self.agent.data.get("_tracker_blocked_tools", set())
                if blocked:
                    cooldown = self.agent.data.get("_block_cooldown_counter", 0) + 1
                    self.agent.data["_block_cooldown_counter"] = cooldown
                    if cooldown >= COOLDOWN_UNBLOCK_AFTER:
                        logger.info(
                            f"[TOOL BLOCK COOLDOWN] {self.agent.agent_name}: "
                            f"{cooldown} successful tool calls — unblocking all "
                            f"TIER 3 blocked tools: {blocked}. Agent gets a "
                            f"fresh chance to verify fixes."
                        )
                        self.agent.data["_tracker_blocked_tools"] = set()
                        self.agent.data["_block_cooldown_counter"] = 0
                        # Also reset consecutive mistake count so the agent
                        # doesn't immediately re-trigger HARD LIMIT
                        self.agent.data["_consecutive_mistake_count"] = 0

        # ─── P2-C: ADAPTER SYNC — consolidate raw keys → typed state ──
        # Runs at every exit point that wrote ToolFailureState keys.
        # WRAP-not-replace: raw keys still drive all decisions.
        from python.helpers.agent_data_adapter import sync_tool_failure_state
        sync_tool_failure_state(self.agent.data)

    async def _track_timeout_retry(self, command: str):
        """Track per-command timeout retries and inject hint at cap (#1083)."""
        counts: dict = self.agent.data.get("_timeout_command_counts", {})
        counts[command] = counts.get(command, 0) + 1
        self.agent.data["_timeout_command_counts"] = counts

        logger.warning(
            f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
            f"Timeout retry {counts[command]}/{MAX_TIMEOUT_RETRIES} "
            f"for command: {command[:60]}"
        )

        if counts[command] >= MAX_TIMEOUT_RETRIES:
            hint = (
                f"⚠️ TOOL TIMEOUT LOOP DETECTED: The command `{command[:80]}` "
                f"has timed out {counts[command]} consecutive times. "
                f"This command is not working. You MUST try a different approach:\n"
                f"1. Use a different test runner or command\n"
                f"2. Skip this step and move to the next task\n"
                f"3. Check if the required binary/package is actually installed\n"
                f"Try a DIFFERENT command or approach — do not repeat the same one."
            )
            await self.agent.hist_add_warning(hint)
            logger.error(
                f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                f"Injected timeout loop hint for: {command[:60]}"
            )
            # Reset counter so hint can fire again if agent ignores it
            counts[command] = 0
            self.agent.data["_timeout_command_counts"] = counts

    async def _track_same_tool_failures(self, tool_name: str):
        """Track per-tool failure counts and inject strategy-change hint at cap (#iter72).
        
        Prevents spin loops where the same tool (e.g., services_mgt) is called
        repeatedly with the same error result. The supervisor can observe the
        _tool_failure_counts data for its own decision-making.

        5-Why RCA (2026-04-18): Old implementation reset counter to 0 after hint,
        enabling infinite advisory-only cycles. Now resets to 1 (single cooldown
        turn) and includes actual error context so the agent can make an
        informed pivot.

        F-4 Escalation Ladder (Deep Audit 2026-05-03): Added session-level
        hint counting with 3-tier escalation:
          Tier 1 (advisory): Standard hint with error context
          Tier 2 (redirect):  After HINT_ESCALATION_REDIRECT session hints → inject
                              concrete alternative and set redirect flag
          Tier 3 (block):     After HINT_ESCALATION_BLOCK session hints → temp-block
                              the tool for this session
        """
        counts: dict = self.agent.data.get("_tool_failure_counts", {})
        # RCA-356 RC-3 Fix: Normalize MCP tool variants to family key.
        # 'tavily-mcp.tavily_search' and 'tavily-mcp.tavily_research'
        # now both increment the 'tavily_search' counter.
        family_name = _normalize_tool_family(tool_name)
        counts[family_name] = counts.get(family_name, 0) + 1
        self.agent.data["_tool_failure_counts"] = counts

        logger.warning(
            f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
            f"Same-tool failure {counts[family_name]}/{MAX_SAME_TOOL_RETRIES} "
            f"for tool: {tool_name} (family: {family_name})"
        )

        if counts[family_name] >= MAX_SAME_TOOL_RETRIES:
            # ─── SESSION HINT COUNTING (F-4 Escalation Ladder) ──────
            session_hints: dict = self.agent.data.get("_session_hint_counts", {})
            session_hints[family_name] = session_hints.get(family_name, 0) + 1
            self.agent.data["_session_hint_counts"] = session_hints
            hint_count = session_hints[family_name]

            # Pull the actual error context for this tool
            error_ctx = self.agent.data.get("_tool_failure_error_context", {})
            last_error = error_ctx.get(tool_name, error_ctx.get(family_name, "(no error details captured)"))

            # ─── F-ERR-5: Record via UEM + inject override authorization ──
            try:
                from python.helpers.universal_error_manager import UniversalErrorManager
                uem = UniversalErrorManager(self.agent)
                uem.record_tool_error(
                    tool_name=tool_name,
                    error_text=last_error,
                )
                if hint_count >= 2:
                    retry_decision = uem.get_retry_decision(last_error)
                    override_msg = (
                        f"\U0001f504 AGENT OVERRIDE AUTHORIZED \u2014 `{tool_name}` has failed "
                        f"{hint_count} hint cycles with the same error pattern.\n\n"
                        f"**Error category:** {retry_decision.get('category', 'unknown')}\n"
                        f"**Guidance:** {retry_decision.get('guidance', 'Change approach')}\n\n"
                        f"You are AUTHORIZED to override your current approach and "
                        f"use a completely different strategy. Previous approaches "
                        f"have failed \u2014 do NOT repeat them."
                    )
                    await self.agent.hist_add_warning(override_msg)
                    logger.warning(
                        f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                        f"F-ERR-5 override authorization for {tool_name} "
                        f"(hint_count={hint_count})"
                    )
            except Exception as uem_err:
                logger.debug(f"F-ERR-5 UEM recording failed (non-fatal): {uem_err}")

            # ─── TIER 3: BLOCK (after HINT_ESCALATION_BLOCK session hints) ─
            if hint_count >= HINT_ESCALATION_BLOCK:
                # RCA-301 Issue 5: NEVER_BLOCK_TOOLS — diagnostic read-only
                # tools must not be Tier 3 blocked.  Blocking read_file
                # creates a death spiral where the agent can't gather context
                # to recover.  Cap at Tier 2 (redirect) instead.
                if tool_name in NEVER_BLOCK_TOOLS:
                    # H-16 / Systems Audit: After NEVER_BLOCK_ADVISORY_CAP hints,
                    # escalate to supervisor instead of infinite advisory loop.
                    from python.helpers.thresholds_registry import Thresholds
                    if hint_count >= Thresholds.NEVER_BLOCK_ADVISORY_CAP:
                        logger.warning(
                            f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                            f"NEVER_BLOCK tool {tool_name} exceeded advisory cap "
                            f"({hint_count} >= {Thresholds.NEVER_BLOCK_ADVISORY_CAP}). "
                            f"Escalating to supervisor."
                        )
                        try:
                            from python.helpers.supervisor_redirect_cap import attempt_supervisor_redirect
                            redirect_msg = (
                                f"Agent {self.agent.agent_name} has failed {hint_count} times "
                                f"with diagnostic tool `{tool_name}`. The tool is not blocked "
                                f"(NEVER_BLOCK), but the agent cannot self-recover. "
                                f"Last error: {truncate_output_middle_out(last_error, max_chars=400, head_ratio=0.3)}"
                            )
                            await attempt_supervisor_redirect(self.agent, redirect_msg)
                        except Exception:
                            pass  # Don't let escalation failure block the agent
                        counts[family_name] = 1
                        self.agent.data["_tool_failure_counts"] = counts
                        return

                    redirect_msg = (
                        f"⚠️ DIAGNOSTIC TOOL REPEATED FAILURE — `{tool_name}` has "
                        f"failed {hint_count} hint cycles. This tool is NOT blocked "
                        f"(it's a diagnostic tool), but you MUST change your approach:\n\n"
                        f"- If reading a file outside the project, check the path is correct\n"
                        f"- If the file doesn't exist, stop trying to read it\n"
                        f"- If permission denied, try a different path or report the issue\n"
                        f"- If the file is too large, read specific line ranges\n\n"
                        f"Last error: {truncate_output_middle_out(last_error, max_chars=400, head_ratio=0.3)}\n"
                    )
                    await self.agent.hist_add_warning(redirect_msg)
                    logger.warning(
                        f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                        f"NEVER_BLOCK tool {tool_name} at Tier 3 threshold — "
                        f"capped at Tier 2 redirect (session hints: {hint_count})"
                    )
                    counts[family_name] = 1
                    self.agent.data["_tool_failure_counts"] = counts
                    return

                # RCA-289 wiring fix: Use _tracker_blocked_tools (set) to
                # avoid type collision with ModeToolFilter's _blocked_tools
                # (list of dicts). Previously both used _blocked_tools.
                blocked_tools: set = self.agent.data.get("_tracker_blocked_tools", set())
                # RCA-356: Block both raw and family names to catch MCP variants
                blocked_tools.add(tool_name)
                if family_name != tool_name:
                    blocked_tools.add(family_name)
                self.agent.data["_tracker_blocked_tools"] = blocked_tools
                # SS-5 Fix: Profile-aware recommendations
                from python.helpers.blocked_response_builder import get_profile_aware_tool_recommendations
                agent_profile = getattr(self.agent.config, "profile", "") or ""
                profile_recs = get_profile_aware_tool_recommendations(
                    tool_name, agent_profile
                )
                block_msg = (
                    f"🚫 TOOL BLOCKED — `{tool_name}` has failed {hint_count} hint cycles "
                    f"in this session. This tool is now TEMPORARILY BLOCKED.\n\n"
                    f"You MUST use a different tool or approach:\n"
                    f"{profile_recs}\n\n"
                    f"DO NOT attempt to call `{tool_name}` again."
                )
                await self.agent.hist_add_warning(block_msg)
                logger.error(
                    f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                    f"ESCALATION TIER 3 — BLOCKED tool: {tool_name} "
                    f"(session hints: {hint_count})"
                )
                counts[family_name] = 1
                self.agent.data["_tool_failure_counts"] = counts
                return

            # ─── TIER 2: REDIRECT (after HINT_ESCALATION_REDIRECT session hints) ─
            if hint_count >= HINT_ESCALATION_REDIRECT:
                # SS-5 Fix: Profile-aware recommendations
                from python.helpers.blocked_response_builder import get_profile_aware_tool_recommendations
                agent_profile = getattr(self.agent.config, "profile", "") or ""
                profile_recs = get_profile_aware_tool_recommendations(
                    tool_name, agent_profile
                )
                redirect_msg = (
                    f"⚠️ ESCALATION — `{tool_name}` has failed {hint_count} hint cycles. "
                    f"Advisory hints are NOT working. You MUST change strategy NOW:\n\n"
                    f"**Last error:**\n```\n{truncate_output_middle_out(last_error, max_chars=400, head_ratio=0.3)}\n```\n\n"
                    f"**MANDATORY alternative approaches:**\n"
                    f"{profile_recs}\n\n"
                    f"WARNING: {HINT_ESCALATION_BLOCK - hint_count} more failures and `{tool_name}` will be BLOCKED."
                )
                await self.agent.hist_add_warning(redirect_msg)
                logger.warning(
                    f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                    f"ESCALATION TIER 2 — REDIRECT for tool: {tool_name} "
                    f"(session hints: {hint_count})"
                )
                counts[family_name] = 1
                self.agent.data["_tool_failure_counts"] = counts
                return

            # ─── TIER 1: ADVISORY (default — first hint cycles) ──────
            # Check if this is a KNOWN error with a specific fix BEFORE
            # falling back to the generic hint.
            from python.helpers.tool_error_patterns import classify_error
            classified = classify_error(last_error)

            if classified:
                hint = (
                    f"🔧 KNOWN ERROR — `{tool_name}` failed {counts[family_name]}x with a **{classified['category']}** error.\n\n"
                    f"**Diagnosis:** {classified['category'].upper()} error detected (severity: {classified['severity']})\n\n"
                    f"**Specific fix:**\n{classified['fix']}\n\n"
                    f"**Last error output:**\n```\n{truncate_output_middle_out(last_error, max_chars=400, head_ratio=0.3)}\n```\n\n"
                    f"Apply the fix above. DO NOT retry the same command without making the specified changes first."
                )
                logger.info(
                    f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                    f"Classified error as '{classified['category']}' "
                    f"(severity={classified['severity']})"
                )
            else:
                # P2-1: Try L2 LLM classification before generic fallback
                l2_classified = None
                try:
                    from python.helpers.tool_error_patterns import classify_error_l2
                    l2_classified = await classify_error_l2(
                        last_error,
                        tool_name=tool_name,
                        agent_profile=getattr(self.agent, "agent_name", ""),
                    )
                except Exception:
                    pass  # L2 is optional — graceful degradation

                if l2_classified:
                    hint = (
                        f"🔧 ERROR CLASSIFIED (L2) — `{tool_name}` failed {counts[family_name]}x with a **{l2_classified['category']}** error.\n\n"
                        f"**Diagnosis:** {l2_classified['category'].upper()} error (severity: {l2_classified['severity']})\n\n"
                        f"**Suggested fix:**\n{l2_classified['fix']}\n\n"
                        f"**Last error output:**\n```\n{truncate_output_middle_out(last_error, max_chars=400, head_ratio=0.3)}\n```\n\n"
                        f"Apply the fix above. Try a DIFFERENT approach if the fix doesn't work."
                    )
                    logger.info(
                        f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                        f"L2 classified error as '{l2_classified['category']}'"
                    )
                else:
                    hint = (
                        f"🛑 SPIN LOOP — `{tool_name}` failed {counts[family_name]}x consecutively.\n\n"
                        f"**Last error output:**\n```\n{truncate_output_middle_out(last_error, max_chars=400, head_ratio=0.3)}\n```\n\n"
                        f"**STOP and do this:**\n"
                        f"1. Read the error above carefully — what is the ROOT obstacle?\n"
                        f"2. Consider: is there a completely different approach that avoids this failure mode?\n"
                        f"3. If this step is non-critical, skip it and move on.\n"
                        f"4. If you truly cannot solve this, call `response` with your progress so far and explain the blocker.\n\n"
                        f"Try a DIFFERENT approach. Repeating the same failing strategy will not produce a different result."
                    )
            await self.agent.hist_add_warning(hint)
            logger.error(
                f"[TOOL FAILURE TRACKER] {self.agent.agent_name}: "
                f"Injected spin loop hint for tool: {tool_name} "
                f"(session hints: {hint_count})"
            )
            # Reset to 1 (not 0) — gives ONE cooldown turn before next hint.
            # Old reset-to-0 enabled infinite cycles with zero escalation.
            counts[family_name] = 1
            self.agent.data["_tool_failure_counts"] = counts

