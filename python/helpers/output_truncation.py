"""Middle-out output truncation for CLI tool output.

Inspired by Roo Code's truncateOutput() — keeps 20% of head + 80% of tail
and replaces the middle with an omission indicator.

Fixes Forgejo #989: Agent runs grep/find on node_modules → megabytes of
minified JS crashes the SSE stream.

RCA-365 F-8: Full pre-truncation output is saved to disk so agents can
recover the un-truncated content via `cat /tmp/last_cmd_output.log`.
"""
from __future__ import annotations
import logging as _logging

# Default limits — tuned to prevent SSE overflow while preserving useful context
MAX_OUTPUT_LINES = 500       # Max lines before truncation kicks in
MAX_OUTPUT_CHARS = 30_000    # ~30KB hard cap (character limit takes precedence)
HEAD_RATIO = 0.2             # 20% from start, 80% from end

# RCA-365 F-2a: Command patterns whose output is low-signal (install/scaffold noise).
# These commands get tighter truncation thresholds to prevent context pollution.
import re as _re

_LOW_SIGNAL_COMMAND_PATTERNS = [
    _re.compile(r'\bnpm\s+(install|ci|i)\b'),
    _re.compile(r'\bnpx\s+create-'),
    _re.compile(r'\byarn\s+(install|add)\b'),
    _re.compile(r'\bpnpm\s+(install|add)\b'),
]

# Tight thresholds for low-signal commands
_LOW_SIGNAL_MAX_LINES = 100
_LOW_SIGNAL_MAX_CHARS = 5000

# RCA-365 F-8: Default path for full pre-truncation output
FULL_OUTPUT_LOG_PATH = "/tmp/last_cmd_output.log"

_logger = _logging.getLogger("output_truncation")


def save_full_output(output: str, filepath: str = FULL_OUTPUT_LOG_PATH) -> None:
    """Save full pre-truncation output to disk for later recovery.

    RCA-365 F-8: When truncation discards important error context, agents
    can recover the full output by reading this file.

    Args:
        output: The complete, un-truncated output string.
        filepath: Destination path (default: /tmp/last_cmd_output.log).
    """
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(output)
    except Exception as exc:
        _logger.warning(f"[TRUNCATION] Failed to save full output to {filepath}: {exc}")


def get_thresholds_for_command(command: str | None) -> tuple[int, int]:
    """Return (max_lines, max_chars) based on the command that produced output.

    Low-signal commands (npm install, scaffold tools) get tighter thresholds
    (100 lines / 5KB) to prevent context pollution. Everything else gets the
    module defaults (500 lines / 30KB).

    Args:
        command: The shell command string, or None/empty for defaults.

    Returns:
        Tuple of (max_lines, max_chars).
    """
    if not command:
        return (MAX_OUTPUT_LINES, MAX_OUTPUT_CHARS)

    for pattern in _LOW_SIGNAL_COMMAND_PATTERNS:
        if pattern.search(command):
            return (_LOW_SIGNAL_MAX_LINES, _LOW_SIGNAL_MAX_CHARS)

    return (MAX_OUTPUT_LINES, MAX_OUTPUT_CHARS)


def truncate_output_middle_out(
    output: str,
    max_lines: int = MAX_OUTPUT_LINES,
    max_chars: int = MAX_OUTPUT_CHARS,
    head_ratio: float = HEAD_RATIO,
) -> str:
    """Truncate large output using middle-out strategy.

    Keeps head_ratio (20%) from the start and (1 - head_ratio) (80%) from
    the end, replacing the middle with an indicator showing how much was omitted.
    
    Character limit takes precedence over line limit (a single line with
    millions of characters could bypass line limits).
    
    Args:
        output: The raw command output string.
        max_lines: Max lines before line-based truncation (default 500).
        max_chars: Max characters before char-based truncation (default 30000).
        head_ratio: Fraction of kept content from the start (default 0.2).
        
    Returns:
        Original output if under limits, or truncated output with middle omission indicator.
    """
    if not output:
        return output

    # Character limit takes precedence (like Roo Code)
    if len(output) > max_chars:
        # RCA-365 F-8: Save full output to disk BEFORE truncating
        save_full_output(output)
        head_chars = int(max_chars * head_ratio)
        tail_chars = max_chars - head_chars
        omitted = len(output) - max_chars
        return (
            output[:head_chars]
            + f"\n\n[... {omitted:,} characters omitted "
              f"— full output saved to {FULL_OUTPUT_LOG_PATH} "
              f"— use cat {FULL_OUTPUT_LOG_PATH} to see complete output ...]\n\n"
            + output[-tail_chars:]
        )

    # Line limit
    lines = output.split("\n")
    if len(lines) <= max_lines:
        return output

    # RCA-365 F-8: Save full output to disk BEFORE truncating
    save_full_output(output)
    head_lines = int(max_lines * head_ratio)
    tail_lines = max_lines - head_lines
    omitted = len(lines) - max_lines

    return (
        "\n".join(lines[:head_lines])
        + f"\n\n[... {omitted:,} lines omitted "
          f"— full output saved to {FULL_OUTPUT_LOG_PATH} "
          f"— use cat {FULL_OUTPUT_LOG_PATH} to see complete output ...]\n\n"
        + "\n".join(lines[-tail_lines:])
    )
