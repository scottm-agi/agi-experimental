"""
Blocked Response Builder — Context-Rich BLOCKED Response Templates.

Provides:
1. build_blocked_response_template() — Ready-to-use BLOCKED response template
   for leaf agents when a tool is blocked by profile enforcement.
2. suggest_alternative_tool() — Given a blocked tool, finds the best available
   alternative within the agent's own profile categories.

ADR: adr_context_rich_error_messaging.md — CRIS Protocol
RCA: U-4 Dead-End Recovery — 82 HARD_STOPs from context-free block messages.
"""
from __future__ import annotations

import logging
from typing import Optional, Set

logger = logging.getLogger("agix.blocked_response_builder")

# ── Known functional-group fallback chains ──────────────────────────────────
# Maps tool → list of alternatives in priority order.
# These are tools that serve SIMILAR purposes (e.g., all do web search).
# Used by suggest_alternative_tool() when the blocked tool has a known fallback.
_TOOL_FALLBACK_CHAINS: dict[str, list[str]] = {
    # Web search / research
    "perplexity_ask": ["tavily_search", "tavily_research", "search_engine"],
    "tavily_search": ["perplexity_ask", "tavily_research", "search_engine"],
    "tavily_research": ["perplexity_ask", "tavily_search", "search_engine"],
    "search_engine": ["perplexity_ask", "tavily_search"],
    # Code execution
    "code_execution_tool": ["code_execution", "terminal"],
    "code_execution": ["code_execution_tool", "terminal"],
    "terminal": ["code_execution_tool", "code_execution"],
    # File writes
    "write_to_file": ["replace_in_file", "apply_diff"],
    "replace_in_file": ["write_to_file", "apply_diff"],
    "apply_diff": ["replace_in_file", "write_to_file"],
    # Browser/web
    "browser_agent": ["browser_subagent", "scrape_url"],
    "browser_subagent": ["browser_agent", "scrape_url"],
    "scrape_url": ["browser_agent", "tavily_extract"],
    # Deliverables
    "save_deliverable": ["write_to_file"],
    "replace_in_deliverable": ["apply_diff_deliverable"],
    "apply_diff_deliverable": ["replace_in_deliverable"],
}


def suggest_alternative_tool(
    blocked_tool: str,
    profile: str,
) -> Optional[str]:
    """Suggest the best available alternative tool within the agent's profile.

    Looks up the blocked tool in the fallback chain, then checks which
    alternatives are available in the agent's profile (via ontology).

    Args:
        blocked_tool: The tool that was blocked by profile enforcement.
        profile: The agent's ontology profile name.

    Returns:
        The name of the best available alternative tool, or None if no
        alternative exists within the agent's profile.
    """
    try:
        from python.helpers.tool_selector import ToolSelector
        selector = ToolSelector.get_instance()
        allowed_tools = selector.get_allowed_tools(profile)
    except Exception:
        return None

    # Check the known fallback chain first
    fallbacks = _TOOL_FALLBACK_CHAINS.get(blocked_tool, [])
    for alt in fallbacks:
        # Normalize for matching (hyphens → underscores)
        alt_norm = alt.replace("-", "_")
        allowed_norm = {t.replace("-", "_") for t in allowed_tools}
        if alt_norm in allowed_norm:
            return alt

    # No known fallback found in profile
    return None


def get_profile_aware_tool_recommendations(
    failed_tool: str,
    profile: str,
    tier: int = 1,
) -> str:
    """Generate profile-aware tool recommendations for failure recovery.

    Instead of hardcoding tool names like 'write_to_file', this function
    dynamically generates recommendations based on what tools the agent's
    profile actually includes. The `tier` parameter controls escalation:

    - TIER 1 (default): Suggestion-style recommendations
    - TIER 2: Stronger 'MUST switch' language
    - TIER 3: Explicit permanent block notice with specific alternatives

    Args:
        failed_tool: The tool that is failing.
        profile: The agent's ontology profile name.
        tier: Escalation tier (1=suggestions, 2=must-switch, 3=permanent block).
              Values ≤0 are treated as 1, values ≥3 are treated as 3.

    Returns:
        A formatted recommendation string with only profile-valid tools.
    """
    # Normalize tier to 1-3 range
    if tier <= 0:
        tier = 1
    elif tier >= 3:
        tier = 3

    lines = []

    # ── TIER 3: Permanent block notice ──────────────────────────────────────
    if tier == 3:
        lines.append(
            f"⛔ `{failed_tool}` is PERMANENTLY BLOCKED for this session. "
            f"Do NOT attempt to call it again — all calls will be rejected."
        )
        # Specific alternatives for code_execution_tool
        if failed_tool in ("code_execution_tool", "code_execution", "terminal"):
            lines.append(
                "- Use `write_to_file` + `replace_in_file` for all remaining file operations."
            )
            lines.append(
                "- Report your findings via `response` — the orchestrator can delegate to another agent."
            )
        lines.append("")

    # ── TIER 2: Must-switch language ─────────────────────────────────────────
    if tier == 2:
        lines.append(
            f"⚠️ `{failed_tool}` has failed repeatedly. "
            f"You MUST switch to a different tool immediately."
        )

    # Try to find a direct alternative via fallback chains
    alt = suggest_alternative_tool(failed_tool, profile)
    if alt:
        verb = "MUST use" if tier >= 2 else "Try"
        lines.append(f"- {verb} `{alt}` instead of `{failed_tool}`")

    # Get all allowed tools for this profile
    try:
        from python.helpers.tool_selector import ToolSelector
        selector = ToolSelector.get_instance()
        allowed = selector.get_allowed_tools(profile)
    except Exception:
        allowed = set()

    # Add generic profile-valid suggestions
    allowed_norm = {t.replace("-", "_") for t in allowed}
    if "write_to_file" in allowed_norm and failed_tool != "write_to_file":
        if tier >= 2:
            lines.append("- You MUST use `write_to_file` for file creation instead of running commands")
        else:
            lines.append("- Use `write_to_file` for file creation instead of running commands")
    if "response" in allowed_norm:
        lines.append("- Use `response` to report what you've completed so far")

    if tier >= 2:
        lines.append("- You MUST break the task into smaller steps with different tools")
    else:
        lines.append("- Break the task into smaller steps with different tools")
    lines.append("- Verify your inputs are correct before retrying")

    if not lines:
        lines.append("- Try a different approach or tool")
        lines.append("- Call `response` with partial progress if truly stuck")

    return "\n".join(lines)



def build_blocked_response_template(
    tool_name: str,
    profile: str,
    error_message: str,
    target_profiles: Set[str],
    alternative_tool: Optional[str] = None,
) -> str:
    """Build a ready-to-use BLOCKED response template for leaf agents.

    Instead of telling the agent "report back to orchestrator" generically,
    this gives them an exact template they can fill in and call `response`
    with immediately — eliminating guesswork.

    ADR: adr_context_rich_error_messaging.md (CRIS Protocol)

    Args:
        tool_name: The tool that was blocked.
        profile: The agent's profile name.
        error_message: The specific error/reason for the block.
        target_profiles: Set of profiles that CAN use this tool.
        alternative_tool: Optional alternative tool the agent could use instead.

    Returns:
        A formatted BLOCKED response template string.
    """
    profiles_str = ", ".join(f"`{p}`" for p in sorted(target_profiles)) if target_profiles else "unknown"

    alt_section = ""
    if alternative_tool:
        alt_section = (
            f"\n**Suggested Alternative**: Try `{alternative_tool}` instead — "
            f"it's available in your profile and serves a similar purpose."
        )

    template = (
        f"**BLOCKED RESPONSE TEMPLATE** — Copy this into your `response` tool call:\n\n"
        f"```\n"
        f"BLOCKED: I cannot complete this subtask.\n"
        f"- blocked_tool: {tool_name}\n"
        f"- error: {error_message}\n"
        f"- alternatives_tried: [list what you tried]\n"
        f"- remaining_work: [describe what's left undone]\n"
        f"- suggested_profile: {', '.join(sorted(target_profiles)) if target_profiles else 'unknown'}\n"
        f"```\n"
        f"{alt_section}\n\n"
        f"The orchestrator will delegate to {profiles_str} to handle this."
    )
    return template
