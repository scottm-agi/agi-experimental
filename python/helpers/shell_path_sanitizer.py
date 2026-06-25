"""
Shell Path Sanitizer — quotes paths with shell metacharacters.

Root Cause (LP_Smoke_1776731913 — RCA-5):
    Agents construct shell commands with unquoted paths like:
      cd /Volumes/Macintosh HD/Users/.../project (copy)/src
    The parentheses and spaces break shell parsing, causing "syntax error"
    or "no such file" failures.

Architecture:
    1. sanitize_shell_path(path): Wraps a single path in quotes if it
       contains shell metacharacters (spaces, parens, &, etc.)
    2. sanitize_command_paths(command): Scans a full shell command for
       unquoted path-like tokens and wraps them.

Integration:
    Can be called from code_execution.py before sending commands to the
    terminal, or from tool_execute_before extensions as a preprocessing step.
"""
from __future__ import annotations

import re
import logging
from typing import List

logger = logging.getLogger("agix.shell_path_sanitizer")


# Characters that require quoting in shell paths
SHELL_METACHARACTERS = set(" ()\t&;|<>$`!#*?[]{}~")


def sanitize_shell_path(path: str) -> str:
    """Wrap a path in double quotes if it contains shell metacharacters.
    
    Already-quoted paths are returned as-is.
    Paths with $ get single-quoted to prevent variable expansion.
    
    Args:
        path: A filesystem path
    
    Returns:
        The path, wrapped in quotes if necessary
    """
    if not path:
        return path
    
    # Already quoted — leave alone
    stripped = path.strip()
    if (stripped.startswith('"') and stripped.endswith('"')) or \
       (stripped.startswith("'") and stripped.endswith("'")):
        return path
    
    # Check if any metacharacter is present
    needs_quoting = any(c in SHELL_METACHARACTERS for c in path)
    
    if not needs_quoting:
        return path
    
    # Use single quotes if $ is present (prevents variable expansion)
    if "$" in path:
        return f"'{path}'"
    
    return f'"{path}"'


# ── Shell operator splitting ──
# Split commands on &&, ||, ;, | while preserving the separators
_SHELL_OPERATOR_RE = re.compile(r"(\s*(?:&&|\|\||[;|])\s*)")

# Detect path-like tokens: starts with / or ~/
_PATH_START_RE = re.compile(r"(?:^|\s)((?:/|~/)\S*)")


def sanitize_command_paths(command: str) -> str:
    """Scan a shell command for unquoted paths and wrap them in quotes.
    
    Strategy:
      1. Split command on shell operators (&&, ||, ;, |)
      2. In each segment, find path-like tokens (starting with / or ~/)
      3. If the path + adjacent tokens form a path with spaces/parens,
         reconstruct and quote them
    
    Already-quoted paths are left untouched.
    
    Args:
        command: A shell command string
    
    Returns:
        The command with unsafe paths wrapped in double quotes
    """
    if not command:
        return command
    
    # Quick check: if no spaces at all, no paths need quoting
    if " " not in command and "\t" not in command:
        return command
    
    # Split on shell operators, keeping separators
    segments = _SHELL_OPERATOR_RE.split(command)
    
    result_parts = []
    for segment in segments:
        # Preserve shell operators as-is
        if _SHELL_OPERATOR_RE.fullmatch(segment):
            result_parts.append(segment)
            continue
        
        result_parts.append(_sanitize_segment(segment))
    
    return "".join(result_parts)


def _sanitize_segment(segment: str) -> str:
    """Sanitize a single command segment (between shell operators).
    
    Finds the path argument (the longest path-like token that may span
    multiple space-separated words) and quotes it if needed.
    """
    stripped = segment.strip()
    if not stripped:
        return segment
    
    # Already has quotes — leave alone
    if '"' in segment or "'" in segment:
        return segment
    
    # Split into tokens
    tokens = segment.split()
    if not tokens:
        return segment
    
    # Find the first token that looks like a path
    path_start_idx = None
    for i, token in enumerate(tokens):
        if token.startswith("/") or token.startswith("~/") or token.startswith("./"):
            path_start_idx = i
            break
    
    if path_start_idx is None:
        return segment
    
    # Reconstruct: the path extends to the end of the segment
    # (since we already split on shell operators, everything after
    # the path-starting token is part of the path or flags)
    
    # Walk forward from path start to find where the path ends
    # A path ends when we hit a token that starts with - (flag)
    # or looks like a command (all lowercase, no /)
    path_tokens = [tokens[path_start_idx]]
    path_end_idx = path_start_idx
    
    for i in range(path_start_idx + 1, len(tokens)):
        token = tokens[i]
        # If token starts with -, it's a flag — path ended
        if token.startswith("-"):
            break
        # If token looks like a known command, path ended
        if token in {"npm", "npx", "node", "python", "python3", "pip",
                     "yarn", "pnpm", "bun", "cargo", "go", "make", 
                     "git", "cat", "ls", "rm", "cp", "mv", "mkdir",
                     "echo", "grep", "find", "chmod", "chown", "curl",
                     "wget", "tar", "zip", "unzip"}:
            break
        # Otherwise it's part of the path (space-separated path component)
        path_tokens.append(token)
        path_end_idx = i
    
    full_path = " ".join(path_tokens)
    
    # Only quote if the path actually has metacharacters
    if not any(c in full_path for c in " ()\t&"):
        return segment
    
    # Reconstruct the segment with the quoted path
    before = " ".join(tokens[:path_start_idx])
    quoted_path = f'"{full_path}"'
    after = " ".join(tokens[path_end_idx + 1:])
    
    # Preserve original leading whitespace
    leading = ""
    if segment and segment[0] in " \t":
        leading = segment[:len(segment) - len(segment.lstrip())]
    
    parts = [p for p in [before, quoted_path, after] if p]
    return leading + " ".join(parts)

