"""
Universal Gate Budget — single module for ALL escape hatches and budget tracking.

Every gate, guard, and blocking mechanism in the system MUST use this module
instead of inline block counting. This ensures:
1. Consistent escape hatch behavior (default: 3 blocks then allow through)
2. Centralized budget tracking for agent decision-making
3. MagicMock/None safety for test contexts
4. Single place to tune thresholds

Usage in extensions (self.agent.data):
    from python.helpers.universal_gate_budget import gate_check, gate_reset

    if gate_check(self.agent.data, "build_pass_gate"):
        return  # Escape hatch — allow through
    # ... blocking logic ...
    # No need to manually increment — gate_check does it

Usage in helpers (agent.data as parameter):
    from python.helpers.universal_gate_budget import gate_check

    if gate_check(agent.data, "integration_checks"):
        return None  # Escape — allow through

Per-tool/per-path gates (dynamic suffix):
    from python.helpers.universal_gate_budget import gate_check

    if gate_check(self.agent.data, "tool_block_enforcer", suffix=tool_name):
        return  # Escape for this specific tool
"""

import logging

logger = logging.getLogger(__name__)

# ── Default threshold ──
DEFAULT_THRESHOLD = 3


def _make_key(gate_name: str, suffix: str = "") -> str:
    """Build the agent.data key for a gate's block counter.

    Format: _<gate_name>[_<suffix>]_blocks

    Args:
        gate_name: Unique gate identifier (e.g., "build_pass_gate")
        suffix: Optional dynamic suffix (e.g., tool name, file path)

    Returns:
        Key string like "_build_pass_gate_blocks" or "_tool_block_enforcer_npm_blocks"
    """
    if suffix:
        return f"_{gate_name}_{suffix}_blocks"
    return f"_{gate_name}_blocks"


def _is_real_dict(agent_data) -> bool:
    """Check if agent_data is a real dict (not MagicMock or None)."""
    return isinstance(agent_data, dict)


def gate_check(
    agent_data,
    gate_name: str,
    threshold: int = DEFAULT_THRESHOLD,
    suffix: str = "",
) -> bool:
    """Check if a gate should escape (allow through) and increment the block counter.

    Call this at the TOP of every blocking code path. If it returns True,
    the gate should allow the action through (escape hatch fired).
    If it returns False, proceed with the block.

    The counter is ALWAYS incremented (even after escape) for tracking.

    Args:
        agent_data: The agent.data dict (or MagicMock in tests)
        gate_name: Unique gate identifier
        threshold: Number of blocks before escape (default: 3)
        suffix: Optional dynamic suffix for per-tool/per-path keys

    Returns:
        True if escape hatch should fire (allow through)
        False if gate should block
    """
    if not _is_real_dict(agent_data):
        return False

    key = _make_key(gate_name, suffix)
    block_count = agent_data.get(key, 0)

    # Always increment
    agent_data[key] = block_count + 1

    if block_count >= threshold:
        logger.warning(
            f"[GATE_BUDGET] {gate_name}"
            + (f"/{suffix}" if suffix else "")
            + f" escape hatch after {block_count} blocks "
            + f"(threshold={threshold}) — allowing through (ADVISORY)"
        )
        return True

    return False


def gate_reset(agent_data, gate_name: str, suffix: str = "") -> None:
    """Reset a gate's block counter (call after successful action).

    Args:
        agent_data: The agent.data dict
        gate_name: Unique gate identifier
        suffix: Optional dynamic suffix
    """
    if not _is_real_dict(agent_data):
        return
    key = _make_key(gate_name, suffix)
    agent_data[key] = 0


def get_block_count(agent_data, gate_name: str, suffix: str = "") -> int:
    """Read current block count without incrementing.

    Args:
        agent_data: The agent.data dict
        gate_name: Unique gate identifier
        suffix: Optional dynamic suffix

    Returns:
        Current block count, or 0 if not found / not a dict
    """
    if not _is_real_dict(agent_data):
        return 0
    key = _make_key(gate_name, suffix)
    return agent_data.get(key, 0)


def budget_summary(agent_data, default_threshold: int = DEFAULT_THRESHOLD) -> dict:
    """Generate a budget summary of all gate states for agent decision-making.

    Scans agent_data for all keys matching the _*_blocks pattern and returns
    a summary dict with count and near_escape status.

    Args:
        agent_data: The agent.data dict
        default_threshold: Threshold to use for near_escape calculation

    Returns:
        Dict of {gate_name: {"count": N, "near_escape": bool}} or empty dict
    """
    if not _is_real_dict(agent_data):
        return {}

    summary = {}
    for key, value in agent_data.items():
        if key.startswith("_") and key.endswith("_blocks") and isinstance(value, (int, float)):
            # Strip leading _ and trailing _blocks to get gate name
            gate_name = key[1:].rsplit("_blocks", 1)[0]
            count = int(value)
            summary[gate_name] = {
                "count": count,
                "near_escape": count >= (default_threshold - 1),
            }

    return summary
