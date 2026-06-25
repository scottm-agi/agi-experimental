"""
RCA-327: Null Response Ceiling — Module-level helpers.

Extracted from python/agent.py during modularization. These are standalone
functions (not Agent methods) that implement the null iteration tracking
and L2 escalation logic.
"""
import json
import logging

logger = logging.getLogger(__name__)

# Import threshold from the canonical source
from python.helpers.thresholds_registry import Thresholds as _Thresholds

MAX_TOTAL_NULL_ITERATIONS = 30


def _extract_middle_out_thoughts(agent_response, max_chars: int = 400) -> str:
    """Extract head + tail of agent thoughts for context injection.

    If the input is JSON with a 'thoughts' list, join them into a single string.
    If the result exceeds max_chars, keep head + tail with a truncation marker.
    Safe for None, empty, or non-JSON inputs.
    """
    try:
        if agent_response is None:
            return ""
        text = str(agent_response)
        # Try to extract structured thoughts from JSON
        try:
            parsed = json.loads(text) if isinstance(agent_response, str) else {}
            thoughts = parsed.get("thoughts", [])
            if isinstance(thoughts, list) and thoughts:
                text = " ".join(str(t) for t in thoughts)
            elif isinstance(thoughts, str) and thoughts:
                text = thoughts
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass  # Use raw text

        if len(text) <= max_chars:
            return text
        half = max_chars // 2
        return f"{text[:half]} ... [truncated] ... {text[-half:]}"
    except Exception:
        return str(agent_response or "")[:max_chars]


def update_null_iteration_counter(agent, tools_result, agent_response: str) -> int:
    """Update the _total_null_iterations counter based on tool result quality.

    Increments when:
      - tools_result is None or falsy
      - tools_result is truthy but tiny (< 10 chars) — empty MCP results

    Resets to 0 when:
      - tools_result has substantive content (>= 10 chars)

    Returns the updated counter value.
    """
    if tools_result:
        result_str = str(tools_result).strip()
        if len(result_str) >= 10:
            # Substantive result — reset counter
            agent.data["_total_null_iterations"] = 0
            return 0

    # Null or empty or tiny result — increment
    null_total = agent.data.get("_total_null_iterations", 0) + 1
    agent.data["_total_null_iterations"] = null_total
    return null_total


def check_null_ceiling_escalation(agent, agent_response: str) -> bool:
    """Check if null iteration ceiling is reached and emit L2 escalation signal.

    At MAX_TOTAL_NULL_ITERATIONS, emits a critical-severity L2 escalation signal
    with middle-out thought context. The L2 supervisor decides whether to
    REDIRECT (with guidance) or STOP_AND_DELIVER.

    Returns True if escalation was emitted, False otherwise.
    """
    null_total = agent.data.get("_total_null_iterations", 0)
    if null_total < MAX_TOTAL_NULL_ITERATIONS:
        return False

    # Extract thought context for the supervisor
    thoughts = _extract_middle_out_thoughts(agent_response, max_chars=400)

    # Emit L2 escalation signal (append, don't overwrite)
    signal = {
        "severity": "critical",
        "detector": "null_iteration_ceiling",
        "detail": (
            f"Agent has had {null_total} consecutive iterations with no "
            f"productive tool output. Recent thoughts: {thoughts}"
        ),
    }
    agent.data.setdefault("_l2_escalation_signals", []).append(signal)

    # Reset counter after escalation (allows L2 redirect to attempt recovery)
    agent.data["_total_null_iterations"] = 0

    logger.error(
        f"[NULL_CEILING] {agent.agent_name}: Null iteration ceiling reached "
        f"({null_total}/{MAX_TOTAL_NULL_ITERATIONS}). L2 escalation emitted."
    )
    return True
