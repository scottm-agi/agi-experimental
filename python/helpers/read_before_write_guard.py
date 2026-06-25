"""
Read-Before-Write Guard — enforces that agents read existing files before
attempting to overwrite them via write_to_file or replace_in_file.

The system prompt says "Read Before Write (MANDATORY)" but there was zero
enforcement. This module provides tool-level enforcement by:
1. Tracking which files each agent has read (via read_file tool calls)
2. Blocking writes to existing files that haven't been read first
3. Detecting file reads from terminal commands (cat, head, tail, less)

Exempt from enforcement:
- New files (creating, not modifying)
- Small files (<20 lines — low regression risk)
- Config/data files (.json, .env, .yml, .yaml, .toml, .lock, .md)
- Generated files (package-lock.json, etc.)

See: docs/rca/rca_iteration15_content_regression_overwrite.md
"""
from __future__ import annotations
import os
import re
import logging
from dataclasses import dataclass
from typing import Optional, Set, Dict, List

logger = logging.getLogger("agix.read_before_write_guard")

# ── Configuration ──
MIN_LINES_TO_ENFORCE = 20  # Don't enforce on files under this line count

# File extensions that are exempt from read-before-write
EXEMPT_EXTENSIONS = {
    ".json", ".env", ".yml", ".yaml", ".toml", ".lock",
    ".txt", ".csv", ".svg", ".png", ".jpg", ".jpeg", ".gif",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map",
}

# ── Per-agent read history ──
# Key: agent_id, Value: set of absolute paths the agent has read
_read_history: Dict[str, Set[str]] = {}

# Per-agent file mtime snapshots (captured at read-time) — RCA-316
# Key: agent_id, Value: dict mapping abs_path → mtime at time of read
_read_mtimes: Dict[str, Dict[str, float]] = {}

# RCA-15 F3-b: Shared write log for cross-agent visibility
_shared_write_log: Dict[str, tuple] = {}  # path -> (agent_id, timestamp)


def record_file_read(agent_id: str, abs_path: str) -> None:
    """Record that an agent has read a file.

    Call this from the read_file tool after a successful read.

    Args:
        agent_id: Unique identifier for the agent (e.g., agent number or name).
        abs_path: Absolute path of the file that was read.
    """
    if agent_id not in _read_history:
        _read_history[agent_id] = set()
    normalized = os.path.normpath(abs_path)
    _read_history[agent_id].add(normalized)

    # Snapshot mtime for stale file detection (RCA-316)
    try:
        _read_mtimes.setdefault(agent_id, {})[normalized] = os.path.getmtime(normalized)
    except OSError:
        pass  # File may not exist — skip mtime snapshot


def broadcast_write(agent_id: str, abs_path: str) -> None:
    """Record a write event visible to ALL agents.

    RCA-15 RC-3: In parallel execution, Agent A's writes were invisible
    to Agent B. This shared log enables cross-agent stale detection.
    """
    normalized = os.path.normpath(abs_path)
    import time
    _shared_write_log[normalized] = (agent_id, time.time())
    logger.info(
        f"[WRITE_BROADCAST] Agent '{agent_id}' wrote '{normalized}'"
    )


def record_file_write(agent_id: str, abs_path: str) -> None:
    """Record that an agent has written a file.

    Call this from write_to_file / replace_in_file tools after a successful write.
    Broadcasts the write to the shared log for cross-agent stale detection.

    Args:
        agent_id: Unique identifier for the agent.
        abs_path: Absolute path of the file that was written.
    """
    broadcast_write(agent_id, abs_path)



def check_read_before_write(
    agent_id: str,
    abs_path: str,
    force: bool = False,
) -> Optional[str]:
    """Check if the agent has read this file before attempting to write it.

    Args:
        agent_id: Unique identifier for the agent.
        abs_path: Absolute path of the file being written.
        force: If True, bypass the guard entirely.

    Returns:
        None if the write is allowed.
        A warning message string if the file hasn't been read first.
    """
    if force:
        return None

    # New files don't need to be read first
    if not os.path.exists(abs_path):
        return None

    # Check file extension — exempt config/data/generated files
    _, ext = os.path.splitext(abs_path)
    basename = os.path.basename(abs_path)

    if ext.lower() in EXEMPT_EXTENSIONS:
        return None

    # Also exempt dotfiles that aren't code or env files
    if basename.startswith(".") and ext.lower() not in {".js", ".ts", ".jsx", ".tsx", ".py", ".css", ".local", ".env"}:
        return None

    # Small files are exempt — low regression risk
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            line_count = sum(1 for _ in f)
        if line_count < MIN_LINES_TO_ENFORCE:
            return None
    except Exception:
        return None  # Can't read file — don't block

    # Check if the agent has read this file
    normalized = os.path.normpath(abs_path)
    agent_reads = _read_history.get(agent_id, set())

    if normalized in agent_reads:
        return None  # Agent has read this file — allow write

    basename = os.path.basename(abs_path)
    logger.warning(
        f"[READ-BEFORE-WRITE GUARD] Blocked write to '{basename}' by agent "
        f"'{agent_id}' — file exists ({line_count} lines) but was never read"
    )

    return (
        f"⚠️ READ-BEFORE-WRITE GUARD: You are trying to modify '{basename}' "
        f"({line_count} lines) but you have not read it first. You MUST use "
        f"the `read_file` tool to read the existing content BEFORE modifying it. "
        f"Do NOT use `cat` via `code_execution_tool` — the guard only recognizes "
        f"the `read_file` tool. Read the file with `read_file` first, then retry."
    )


# ── ADR-010: Proactive Read-Before-Write ──

@dataclass
class ProactiveReadResult:
    """Result from proactive auto-read when agent writes to an unread file.

    Instead of blocking the write and forcing a retry loop, the proactive
    guard auto-reads the file, registers it in the history, and returns
    both the content and a warning. The write proceeds — but the agent
    learns to read first next time.
    """
    file_content: str   # Auto-read file content
    warning: str        # Educational message for the agent
    line_count: int     # Number of lines in the file


def check_read_before_write_proactive(
    agent_id: str,
    abs_path: str,
    force: bool = False,
) -> Optional[ProactiveReadResult]:
    """Proactive read-before-write check (ADR-010).

    Instead of blocking writes to unread files, this function:
    1. Auto-reads the file content
    2. Registers the read in the guard's history
    3. Returns the content + warning for the agent to learn from

    The write should PROCEED regardless — this is advisory, not blocking.
    This eliminates the 2-turn retry loop that wastes ~34 iterations/test.

    Args:
        agent_id: Unique identifier for the agent.
        abs_path: Absolute path of the file being written.
        force: If True, bypass the guard entirely.

    Returns:
        None if no action needed (file already read, new file, exempt, etc.)
        ProactiveReadResult if the file was auto-read (contains content + warning)
    """
    if force:
        return None

    # New files don't need to be read first
    if not os.path.exists(abs_path):
        return None

    # Check file extension — exempt config/data/generated files
    _, ext = os.path.splitext(abs_path)
    basename = os.path.basename(abs_path)

    if ext.lower() in EXEMPT_EXTENSIONS:
        return None

    # Exempt dotfiles that aren't code or env files
    if basename.startswith(".") and ext.lower() not in {".js", ".ts", ".jsx", ".tsx", ".py", ".css", ".local", ".env"}:
        return None

    # Small files are exempt — low regression risk
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        if line_count < MIN_LINES_TO_ENFORCE:
            return None
    except Exception:
        return None  # Can't read file — don't interfere

    # Check if the agent has already read this file
    normalized = os.path.normpath(abs_path)
    agent_reads = _read_history.get(agent_id, set())

    # RCA-15 F3-a: Check if file was modified by another agent since our last read
    stale_files = get_stale_files(agent_id)
    stale_paths = {s[0] for s in stale_files}
    if normalized in stale_paths:
        # File was modified externally — invalidate our read and force re-read
        agent_reads.discard(normalized)
        if agent_id in _read_mtimes and normalized in _read_mtimes[agent_id]:
            del _read_mtimes[agent_id][normalized]
        logger.warning(
            f"[STALE_DETECT] File '{normalized}' modified by another agent — "
            f"invalidating read for agent '{agent_id}', forcing re-read"
        )

    if normalized in agent_reads:
        return None  # Agent has read this file — no action needed

    # ── Auto-read: register the read and return content ──
    record_file_read(agent_id, abs_path)

    logger.info(
        f"[PROACTIVE RBW] Auto-read '{basename}' for agent '{agent_id}' "
        f"({line_count} lines) — write will proceed with advisory warning"
    )

    warning = (
        f"📄 PROACTIVE READ: '{basename}' ({line_count} lines) was auto-read because "
        f"you didn't read it with `read_file` before writing. The file content is "
        f"included below. Next time, use `read_file` FIRST to avoid this warning.\n"
        f"File content of '{basename}' ({line_count} lines) follows."
    )

    return ProactiveReadResult(
        file_content=content,
        warning=warning,
        line_count=line_count,
    )


def clear_read_history(agent_id: str) -> None:
    """Clear the read history for an agent. Used in tests."""
    _read_history.pop(agent_id, None)
    _read_mtimes.pop(agent_id, None)


def get_read_history(agent_id: str) -> Set[str]:
    """Get the set of files read by an agent. Used for diagnostics."""
    return _read_history.get(agent_id, set()).copy()


# ── Terminal read detection ──
# Patterns that indicate a file-read command in terminal output
_TERMINAL_READ_CMDS = re.compile(
    r"(?:^|&&|;|\|\s*)\s*"
    r"(?:cat|head|tail|less|more|bat|batcat)"
    r"(?:\s+-[a-zA-Z](?:\s+\d+)?|\s+-\d+)*"  # flags: -n 50, -20, -f
    r"\s+"
    r"([^|;><&\s]+)",  # the file path (no pipes/redirects/spaces)
    re.MULTILINE,
)

# Patterns to EXCLUDE (heredocs, pipes without file args)
_HEREDOC_PATTERN = re.compile(r"<<[-~]?\s*['\"]?\w+['\"]?")


def extract_file_reads_from_terminal(command: str) -> List[str]:
    """Parse a terminal command string to detect file-read operations.

    Detects: cat, head, tail, less, more, bat/batcat with file path arguments.
    Excludes: heredocs (cat <<EOF), piped cat (echo | cat), redirects.

    Args:
        command: The terminal command string to parse.

    Returns:
        List of file paths that were read by the command.
    """
    if not command:
        return []

    # Exclude heredoc patterns entirely
    if _HEREDOC_PATTERN.search(command):
        return []

    paths = []
    for match in _TERMINAL_READ_CMDS.finditer(command):
        path = match.group(1).strip("'\"")
        # Must look like a file path (starts with / or ./ or relative)
        # Skip if it's a flag, URL, or nonsensical
        if path and not path.startswith("-") and not path.startswith("http"):
            paths.append(path)

    return paths


def record_terminal_reads(agent_id: str, command: str) -> List[str]:
    """Parse a terminal command for file reads and record them.

    Call this from code_execution_tool after executing a terminal command
    to automatically register file reads with the guard.

    Args:
        agent_id: Unique identifier for the agent.
        command: The terminal command that was executed.

    Returns:
        List of file paths that were detected and recorded.
    """
    reads = extract_file_reads_from_terminal(command)
    for path in reads:
        record_file_read(agent_id, path)
        logger.debug(
            f"[READ-BEFORE-WRITE GUARD] Terminal read detected and recorded: "
            f"'{path}' by agent '{agent_id}'"
        )
    return reads


# ── RCA-316: Stale File Detection ──

def get_stale_files(agent_id: str) -> List[tuple]:
    """Return files read by agent that have been modified since read.

    Compares mtime snapshot (captured at read-time) against current
    filesystem mtime. Files that have been modified externally (by other
    agents, user edits, or filesystem changes) are returned.

    Args:
        agent_id: Unique identifier for the agent.

    Returns:
        List of (path, read_mtime, current_mtime) tuples for stale files.
    """
    mtimes = _read_mtimes.get(agent_id, {})
    if not mtimes:
        return []

    stale = []
    for path, read_mtime in mtimes.items():
        try:
            current_mtime = os.path.getmtime(path)
            if current_mtime > read_mtime:
                stale.append((path, read_mtime, current_mtime))
        except OSError:
            # File deleted — also stale, but not our concern here
            pass

    return stale


def invalidate_stale_reads(agent_id: str) -> List[str]:
    """Remove stale files from agent's read history.

    Called by the stale file awareness extension to force re-reads.
    Files that have been modified since the agent read them are removed
    from read history, so the read-before-write guard will require
    a fresh read.

    Args:
        agent_id: Unique identifier for the agent.

    Returns:
        List of paths that were invalidated.
    """
    stale = get_stale_files(agent_id)
    if not stale:
        return []

    invalidated = []
    agent_reads = _read_history.get(agent_id, set())
    agent_mtimes = _read_mtimes.get(agent_id, {})

    for path, _, _ in stale:
        agent_reads.discard(path)
        agent_mtimes.pop(path, None)
        invalidated.append(path)
        logger.info(
            f"[STALE FILE] Invalidated '{os.path.basename(path)}' for agent "
            f"'{agent_id}' — file was modified externally"
        )

    return invalidated
