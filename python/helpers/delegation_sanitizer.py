"""
Delegation Message Sanitizer (F1-C)

RCA 214: Orchestrators sometimes instruct subordinates to use tools they
don't have access to (e.g., telling a 'code' agent to 'push to GitHub using
github_push'). This causes the subordinate to waste iterations attempting
blocked tool calls.

This module scans delegation messages for tool name references and injects
warnings when those tools are outside the target profile's capabilities.

Design decision: We WARN rather than strip, because:
1. Stripping could remove valid context (e.g., "don't use github_push")
2. The warning gives the agent context about what's NOT available
3. The hard enforcement gate (_15_profile_tool_enforcement.py) is the backstop
"""
import re
import logging
from typing import List, Optional

logger = logging.getLogger("agix.delegation_sanitizer")

# Orchestrator-only tool names that non-orchestrator profiles cannot use
ORCHESTRATOR_ONLY_TOOLS = {
    "call_subordinate",
    "call_subordinate_batch",
    "delegate",
}

# Known tool names across the system (extend as needed)
# These are tools that exist but may not be available to all profiles
KNOWN_TOOL_NAMES = {
    # Orchestrator-only
    "call_subordinate", "call_subordinate_batch",
    # Research-only
    "search_engine", "perplexity_ask", "scrape_url",
    # Code-only
    "code_execution_tool", "terminal", "write_to_file", "apply_diff",
    "replace_in_file",
    # Browser-only
    "browser", "screenshot",
    # GitHub/deploy
    "github_push", "github_commit",
    # Universal (most profiles)
    "read_file", "read_deliverables", "save_deliverable",
    "sequential_thinking", "response",
}


def _get_profile_tool_names(profile: str) -> set:
    """Get the set of tool names available to a profile.

    This is a lightweight lookup that doesn't load the full ontology.
    For precise results, use ToolSelector.should_include_tool().
    """
    try:
        from python.helpers.tool_selector import ToolSelector
        selector = ToolSelector.get_instance()
        # Get all known tools and filter by profile
        return {
            tool for tool in KNOWN_TOOL_NAMES
            if selector.should_include_tool(tool, profile)
        }
    except Exception:
        # Fallback: allow all tools (don't block if ToolSelector unavailable)
        return KNOWN_TOOL_NAMES


def _find_tool_references(message: str) -> List[str]:
    """Find tool name references in a delegation message.

    Returns a list of tool names found in the message text.
    Only matches known tool names to avoid false positives.
    """
    found = []
    message_lower = message.lower()

    for tool in KNOWN_TOOL_NAMES:
        # Match tool name as a word boundary (avoid partial matches)
        pattern = r'\b' + re.escape(tool) + r'\b'
        if re.search(pattern, message_lower):
            found.append(tool)

    return found


def sanitize_delegation_message(
    message: str,
    profile: str,
    strict: bool = False,
) -> str:
    """Scan a delegation message for tool references outside the target profile.

    Args:
        message: The delegation message text.
        profile: Target subordinate profile (e.g., 'code', 'researcher').
        strict: If True, strip tool references. If False (default), inject warnings.

    Returns:
        The message with warnings injected (or stripped if strict=True).
        Returns the original message unchanged if no issues found.
    """
    if not message or not profile:
        return message

    # Find tool references in the message
    referenced_tools = _find_tool_references(message)
    if not referenced_tools:
        return message

    # Get tools available to this profile
    available_tools = _get_profile_tool_names(profile)

    # Identify unavailable tools
    unavailable = [t for t in referenced_tools if t not in available_tools]

    # Also flag orchestrator-only tools for non-orchestrator profiles
    if profile not in ("orchestrator", "default"):
        for tool in referenced_tools:
            if tool in ORCHESTRATOR_ONLY_TOOLS and tool not in unavailable:
                unavailable.append(tool)

    if not unavailable:
        return message

    # Build warning
    tool_list = ", ".join(f"`{t}`" for t in unavailable)
    warning = (
        f"\n\n⚠️ TOOL AVAILABILITY WARNING: The following tools referenced in "
        f"this task are NOT available to the '{profile}' profile: {tool_list}. "
        f"Do NOT attempt to call them — they will be blocked. Use only the "
        f"tools available to your profile."
    )

    logger.info(
        f"[DELEGATION_SANITIZER] Flagged {len(unavailable)} unavailable "
        f"tools for profile '{profile}': {unavailable}"
    )

    return message + warning
