"""
Profile-aware tool enforcement message builder.

Provides context-appropriate error messages when a tool is blocked:
- Orchestration profiles (can delegate) → "Delegate to subordinate"
- Leaf profiles (cannot delegate) → "Use your available tools"

RCA: MSR_Smoke_1777164282 Cluster #1 — delegation dead-end for subordinates.
GAP-4 (RCA-343): Smart redirect — dynamically resolves which profile(s)
own the blocked tool via ontology reverse lookup instead of hardcoded list.
"""

from __future__ import annotations
import logging
from typing import List, Set

logger = logging.getLogger("agix.tool_enforcement_messages")

# Profiles that are orchestrators (should not be recommended as redirect targets)
_ORCHESTRATOR_PROFILES = {"multiagentdev", "architect", "alex", "account-leader",
                          "marketing-lead", "sales-enabler"}


def find_profiles_for_tool(tool_name: str) -> Set[str]:
    """Reverse-lookup: find which profiles have access to a given tool.

    Walks the ontology categories → profiles mapping to find all profiles
    that include a category containing the requested tool.

    Args:
        tool_name: The tool name to look up.

    Returns:
        Set of profile names that have access to this tool.
        Excludes orchestrator profiles (they delegate, not implement).
    """
    try:
        from python.helpers.tool_selector import ToolSelector
        selector = ToolSelector.get_instance()
        ontology = selector._ontology
    except Exception:
        return set()

    categories = ontology.get("categories", {})
    profiles = ontology.get("profiles", {})

    # Step 1: Find which categories contain this tool
    tool_categories: Set[str] = set()
    for cat_name, cat_tools in categories.items():
        if tool_name in cat_tools:
            tool_categories.add(cat_name)

    if not tool_categories:
        return set()

    # Step 2: Find which profiles include those categories
    result: Set[str] = set()
    for profile_name, profile_cats in profiles.items():
        if profile_name in _ORCHESTRATOR_PROFILES:
            continue  # Skip orchestrators — they're the callers, not targets
        if any(cat in profile_cats for cat in tool_categories):
            result.add(profile_name)

    return result


def build_blocked_message(
    tool_name: str,
    profile: str,
    can_delegate: bool,
    profile_categories: List[str],
    delegation_type: str | None = None,
) -> str:
    """Build a profile-aware blocked tool message.

    Args:
        tool_name: The blocked tool's name.
        profile: The agent's profile name.
        can_delegate: Whether the profile has access to delegation tools.
        profile_categories: The list of category names the profile has access to.
        delegation_type: 'routing' (route_to_agent), 'orchestration' (call_subordinate),
                         or None (leaf agent).

    Returns:
        A formatted error message string.
    """
    if can_delegate:
        if delegation_type == "routing":
            return _build_router_message(tool_name, profile)
        return _build_orchestrator_message(tool_name, profile)
    else:
        return _build_leaf_message(tool_name, profile, profile_categories)


def _build_orchestrator_message(tool_name: str, profile: str) -> str:
    """Message for profiles that CAN delegate via call_subordinate.

    GAP-4 (RCA-343): Uses ontology reverse-lookup to recommend the specific
    profile(s) that own the blocked tool, instead of a hardcoded list.

    U-4 (CRIS Protocol): Enhanced with Context, Reason, Issues, Redirect.
    """
    # Dynamically resolve which profiles have this tool
    target_profiles = find_profiles_for_tool(tool_name)

    if target_profiles:
        # Build specific redirect guidance
        profiles_str = ", ".join(f"`{p}`" for p in sorted(target_profiles))
        redirect_line = (
            f"**Redirect:** Delegate to `call_subordinate` with profile {profiles_str}.\n"
        )
    else:
        # Fallback to generic guidance when tool not found in ontology
        redirect_line = (
            f"**Redirect — delegate to the appropriate subordinate profile:**\n"
            f"- Code/files/terminal → `call_subordinate` with profile `code`\n"
            f"- Research/docs → `call_subordinate` with profile `researcher`\n"
            f"- Frontend work → `call_subordinate` with profile `frontend`\n"
        )

    return (
        f"🚫 **TOOL BLOCKED — `{tool_name}` is not available to your role.**\n\n"
        f"**Context:** You attempted to call `{tool_name}`.\n"
        f"**Reason:** Your profile (`{profile}`) does not include `{tool_name}` "
        f"in its ontology categories. This is a permanent architectural restriction.\n"
        f"**Constraints:** Orchestrators delegate work — they don't execute directly.\n\n"
        f"{redirect_line}\n"
        f"Do NOT attempt to call `{tool_name}` again. Delegate immediately."
    )


def _build_router_message(tool_name: str, profile: str) -> str:
    """Message for profiles that delegate via route_to_agent (routing category).

    The default profile uses route_to_agent instead of call_subordinate.
    This message guides the agent to use route_to_agent with the correct
    target profile, preventing dead-ends in scheduled tasks.
    """
    target_profiles = find_profiles_for_tool(tool_name)

    if target_profiles:
        profiles_str = ", ".join(f"`{p}`" for p in sorted(target_profiles))
        redirect_line = (
            f"**Redirect:** Use `route_to_agent` with the user's original request. "
            f"The tool `{tool_name}` is available on profile(s): {profiles_str}.\n"
            f"Example: `route_to_agent` with message=\"<original request>\"\n"
        )
    else:
        redirect_line = (
            f"**Redirect:** Use `route_to_agent` to delegate this request "
            f"to the appropriate specialist agent.\n"
        )

    return (
        f"🚫 **TOOL BLOCKED — `{tool_name}` is not available to your role.**\n\n"
        f"**Context:** You attempted to call `{tool_name}`.\n"
        f"**Reason:** Your profile (`{profile}`) does not include `{tool_name}` "
        f"in its ontology categories. This is a permanent architectural restriction.\n"
        f"**Constraints:** Your role is to ROUTE requests to specialist agents, "
        f"not execute specialized tools directly.\n\n"
        f"{redirect_line}\n"
        f"Do NOT attempt to call `{tool_name}` again. Route immediately."
    )


def _build_leaf_message(
    tool_name: str,
    profile: str,
    profile_categories: List[str],
) -> str:
    """Message for profiles that CANNOT delegate (leaf agents).

    Leaf agents must report back to the multiagentdev hub with actionable
    routing information. The message includes which profile(s) own the
    blocked tool so the hub can immediately delegate correctly.

    GAP-4 (RCA-343): Uses ontology reverse-lookup to include smart
    profile suggestions in the escalation guidance — information flows
    back UP through the hub.

    U-4 (CRIS Protocol): Enhanced with:
    - Suggested alternative tool from the agent's own profile
    - Pre-filled BLOCKED response template
    - Target profile capabilities
    """
    categories_str = ", ".join(f"`{c}`" for c in profile_categories)

    # Dynamically resolve which profiles have the blocked tool
    target_profiles = find_profiles_for_tool(tool_name)

    # U-4: Suggest an alternative tool within the agent's own profile
    alternative_tool = None
    try:
        from python.helpers.blocked_response_builder import (
            suggest_alternative_tool,
            build_blocked_response_template,
        )
        alternative_tool = suggest_alternative_tool(
            blocked_tool=tool_name,
            profile=profile,
        )
    except Exception:
        pass  # Graceful degradation if builder not available

    # Build the alternative suggestion line
    alt_line = ""
    if alternative_tool:
        alt_line = (
            f"\n**Suggested Alternative:** Try `{alternative_tool}` instead — "
            f"it's available in your profile and serves a similar purpose.\n"
        )

    # Build BLOCKED response template
    blocked_template = ""
    if target_profiles:
        try:
            blocked_template = build_blocked_response_template(
                tool_name=tool_name,
                profile=profile,
                error_message=f"PROFILE_ENFORCEMENT — tool not in {profile} profile",
                target_profiles=target_profiles,
                alternative_tool=alternative_tool,
            )
        except Exception:
            pass  # Graceful degradation

    if target_profiles:
        profiles_str = ", ".join(f"`{p}`" for p in sorted(target_profiles))
        escalation = (
            f"**Redirect:** Use `response` to report back to your parent "
            f"orchestrator (multiagentdev) that this task requires `{tool_name}`, "
            f"which is available on profile(s): {profiles_str}.\n"
        )
    else:
        escalation = (
            f"**Redirect:** Complete your task using available tools. "
            f"If `{tool_name}` is essential, report this limitation in your "
            f"`response` so the orchestrator can reassign the work."
        )

    parts = [
        f"🚫 **TOOL BLOCKED — `{tool_name}` is not in your role's toolset.**",
        f"",
        f"**Context:** You attempted to call `{tool_name}` on profile `{profile}`.",
        f"**Reason:** Your profile does not include `{tool_name}` in its "
        f"ontology categories. This is a permanent PROFILE_ENFORCEMENT block.",
        f"**Your available tool categories:** {categories_str}",
    ]

    if alt_line:
        parts.append(alt_line)

    parts.append(escalation)

    if blocked_template:
        parts.append(f"\n{blocked_template}")

    parts.append(f"\nDo NOT attempt to call `{tool_name}` again.")

    return "\n".join(parts)


def build_escalated_message(
    tool_name: str,
    profile: str,
    block_count: int,
    profile_categories: List[str] | None = None,
) -> str:
    """Escalated message for repeat blocks — forces the agent to stop retrying.

    Triggered when the same agent attempts the same blocked tool 2+ times.
    Uses stronger language and sets break_loop=True in the caller to
    terminate the agent's current action loop.

    Args:
        tool_name: The blocked tool's name.
        profile: The agent's profile name.
        block_count: How many times this tool has been blocked for this agent.
        profile_categories: Optional list of the profile's categories, used to
                           determine if route_to_agent guidance should be given.

    Returns:
        A formatted escalation message string.

    RCA: Iteration 208 — orchestrator post-build stall. The orchestrator
    retried code_execution_tool indefinitely because each block returned
    the same break_loop=False message with no escalation.
    """
    # Determine delegation guidance based on profile capabilities
    has_routing = profile_categories and "routing" in profile_categories
    has_orchestration = profile_categories and "orchestration" in profile_categories

    if has_routing:
        delegation_step = (
            "1. Use `route_to_agent` to delegate this request to the correct specialist agent\n"
        )
    elif has_orchestration:
        delegation_step = (
            "1. If you already delegated this task → trust the subordinate's result\n"
        )
    else:
        delegation_step = (
            "1. If you already delegated this task → trust the subordinate's result\n"
        )

    return (
        f"🛑 **REPEATED TOOL BLOCK — `{tool_name}` blocked {block_count} times.**\n\n"
        f"You have attempted `{tool_name}` {block_count} times. It is PERMANENTLY "
        f"unavailable to your profile (`{profile}`).\n\n"
        f"**STOP retrying. Your loop is being broken.**\n\n"
        f"**What to do NOW:**\n"
        f"{delegation_step}"
        f"2. Proceed to your NEXT phase immediately\n"
        f"3. If all phases are complete → call `response` with your current results\n\n"
        f"Do NOT attempt `{tool_name}` again. This message will not repeat — "
        f"your current action loop has been terminated."
    )
