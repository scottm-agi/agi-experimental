"""
Death Summary — Progress snapshot for subordinate agents hitting iteration limits.

Root Cause (P1 Fix 4):
    When a subordinate hits its 75-iteration limit, it returned a bare
    "[ITERATION_LIMIT]" tag with zero context about what it accomplished.
    The parent re-delegates to a new subordinate that starts from scratch.

Fix:
    This module extracts a deterministic progress summary from the agent's
    chat history (tool calls, file writes, commands executed) WITHOUT making
    an LLM call. The summary is fast, cheap, and always available — even
    when the agent is out of token budget.

Architecture:
    - generate_death_summary(agent) → str: Extracts tool/file/command stats
    - format_iteration_limit_message(name, iters, summary) → str: Formats
      the [ITERATION_LIMIT] return with embedded progress summary
"""
from __future__ import annotations

import json
import re
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass  # Agent type is only needed for type hints

logger = logging.getLogger("agix.death_summary")

# Max chars for the summary output
_MAX_SUMMARY_LENGTH = 2000


def generate_death_summary(agent: Any) -> str:
    """Generate a deterministic progress summary from agent's chat history.

    This does NOT call the LLM. It parses the agent's message history to
    extract:
    - Which tools were called and how many times
    - Which files were written/modified
    - Which commands were executed
    - The agent's profile and iteration count

    Args:
        agent: The dying agent instance (has .history, .config, ._total_monologue_iterations)

    Returns:
        A concise progress summary string (max 2000 chars).
    """
    profile = getattr(getattr(agent, "config", None), "profile", "unknown")
    iterations = getattr(agent, "_total_monologue_iterations", 0)

    # Parse history for tool calls
    tool_counts: dict[str, int] = {}
    files_written: list[str] = []
    commands_run: list[str] = []

    try:
        messages = getattr(agent.history, "messages_all", [])
        for msg in messages:
            if not getattr(msg, "ai", False):
                continue
            content = getattr(msg, "content", "")
            if not isinstance(content, str):
                continue

            # Extract tool_name
            tool_matches = re.findall(r'"tool_name"\s*:\s*"([^"]+)"', content)
            for tool_name in tool_matches:
                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

            # Extract filenames from write_to_file / replace_in_file calls
            file_matches = re.findall(
                r'"(?:filename|file|path)"\s*:\s*"([^"]+)"', content
            )
            for f in file_matches:
                if f not in files_written:
                    files_written.append(f)

            # Extract commands from code_execution_tool calls
            cmd_matches = re.findall(r'"command"\s*:\s*"([^"]{1,120})"', content)
            for cmd in cmd_matches:
                if cmd not in commands_run:
                    commands_run.append(cmd)
    except Exception as e:
        logger.warning(f"Failed to parse agent history for death summary: {e}")

    # Build summary
    lines: list[str] = []
    lines.append(f"**Agent**: {profile} | **Iterations used**: {iterations}")
    lines.append("")

    if tool_counts:
        lines.append("**Tools called**:")
        for tool, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
            lines.append(f"- `{tool}`: {count}x")
    else:
        lines.append("No tool calls recorded in history.")

    if files_written:
        lines.append("")
        lines.append(f"**Files created/modified** ({len(files_written)}):")
        # Show up to 20 files, with a note if truncated
        for f in files_written[:20]:
            lines.append(f"- `{f}`")
        if len(files_written) > 20:
            lines.append(f"- ... and {len(files_written) - 20} more")

    if commands_run:
        lines.append("")
        lines.append(f"**Commands executed** ({len(commands_run)}):")
        for cmd in commands_run[:10]:
            lines.append(f"- `{cmd}`")
        if len(commands_run) > 10:
            lines.append(f"- ... and {len(commands_run) - 10} more")

    summary = "\n".join(lines)

    # Cap length
    if len(summary) > _MAX_SUMMARY_LENGTH:
        summary = summary[:_MAX_SUMMARY_LENGTH - 20] + "\n\n... (truncated)"

    return summary


def format_iteration_limit_message(
    agent_name: str, iterations: int, progress_summary: str
) -> str:
    """Format the [ITERATION_LIMIT] return value with embedded progress summary.

    This replaces the bare "[ITERATION_LIMIT] Agent X hit limit" with a rich
    message that the parent can forward to a replacement subordinate.

    Args:
        agent_name: The dying agent's display name
        iterations: Number of iterations consumed
        progress_summary: Output from generate_death_summary()

    Returns:
        Formatted string with [ITERATION_LIMIT] tag + progress summary + handoff instructions
    """
    return (
        f"[ITERATION_LIMIT] Agent {agent_name} hit iteration limit ({iterations}). "
        f"Stopping this agent only.\n\n"
        f"## Progress Summary\n"
        f"{progress_summary}\n\n"
        f"## Handoff Instructions\n"
        f"Pass this progress summary to any replacement subordinate so it can "
        f"continue from where this agent left off. Do NOT re-delegate without "
        f"including this context — the replacement needs to know what was already "
        f"accomplished to avoid repeating work."
    )
