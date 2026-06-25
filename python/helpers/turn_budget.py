"""
Turn Budget Injection — Fix F6 (RCA Iteration 158, Issue F)

Root Cause: Subordinate agents have no visibility into their remaining
turn budget. They iterate blindly (try→fail→retry) instead of planning
a strategy that fits within their available turns.

Fix: build_turn_budget_notice() generates a structured markdown notice
that call_subordinate.py injects into the delegation message. This tells
the subordinate how many turns it has, encouraging strategic planning.

Usage:
    from python.helpers.turn_budget import build_turn_budget_notice
    notice = build_turn_budget_notice(current_turn=10, max_turns=50)
    if notice:
        message = notice + "\\n\\n" + message
"""


def build_turn_budget_notice(
    current_turn: int,
    max_turns: int,
) -> str:
    """Generate a turn budget notice for subordinate agents.

    Args:
        current_turn: Parent agent's current absolute turn count.
        max_turns: Parent agent's maximum allowed turns.

    Returns:
        Markdown-formatted budget notice, or empty string if budget is
        unlimited (max_turns <= 0).
    """
    if max_turns <= 0:
        return ""

    remaining = max(0, max_turns - current_turn)
    pct_remaining = remaining / max_turns if max_turns > 0 else 1.0

    # Determine urgency level
    if pct_remaining < 0.2:
        urgency = "⚠️ CRITICAL"
        planning = (
            "You have very few turns left. Do NOT iterate or retry. "
            "Execute your best strategy in a single pass and deliver immediately."
        )
    elif pct_remaining < 0.5:
        urgency = "⏳ LIMITED"
        planning = (
            "Plan your strategy carefully before executing. Avoid trial-and-error — "
            "choose the most likely approach and commit to it."
        )
    else:
        urgency = "📋"
        planning = (
            "Plan your approach before starting. Prioritize the highest-impact "
            "changes first in case you run low on turns."
        )

    notice = (
        f"## {urgency} Turn Budget: {remaining} turns remaining\n"
        f"**Budget:** {remaining}/{max_turns} turns | "
        f"**Used:** {current_turn} turns\n\n"
        f"**Strategy guidance:** {planning}\n"
    )

    return notice
