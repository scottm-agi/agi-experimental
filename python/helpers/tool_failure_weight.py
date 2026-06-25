"""Failure weight calculator for tool_failure_tracker.

RCA-356 §2c: Verification/test script failures count at half weight (0.5)
because they're exploratory — the agent is testing whether something works,
not making a mistake. Regular tool failures count at full weight (1.0).

RCA-451: Infrastructure tool failures count at elevated weight (1.5) because
they indicate systemic issues (port conflicts, service crashes) that cannot
be resolved by retrying. services_mgt failures in particular should reach
circuit breaker thresholds faster to prevent 14+ iteration spin loops.

This prevents verification scripts from prematurely triggering circuit
breakers and TIER escalation. Without this, an agent that dutifully runs
`node verify-routes.js` after every code change gets penalized twice as
fast as an agent that skips verification entirely — exactly the opposite
of the desired incentive.
"""

from __future__ import annotations

import re

# Keywords that indicate a verification/test/check command.
_VERIFY_KEYWORDS = frozenset({
    'verify', 'test', 'check', 'validate', 'assert',
    'jest', 'pytest', 'vitest', 'mocha', 'spec',
})

# RCA-451: Infrastructure tools whose failures indicate systemic issues.
# These get elevated weight (1.5) so the circuit breaker fires after ~5-6
# failures instead of requiring 8+ at normal weight.
INFRASTRUCTURE_TOOLS = frozenset({
    'services_mgt',
})

# Split on any non-alpha character to extract tokens.
_TOKEN_SPLITTER = re.compile(r'[^a-zA-Z]+')


def get_failure_weight(tool_name: str, tool_args: dict) -> float:
    """Calculate the failure weight for a tool execution.

    Args:
        tool_name: Name of the tool that failed (e.g., 'code_execution_tool').
        tool_args: Arguments passed to the tool (dict with 'code' or 'runtime' key).

    Returns:
        0.5 for verification/test script failures in code_execution_tool.
        1.5 for infrastructure tool failures (services_mgt).
        1.0 for all other failures.
    """
    # RCA-451: Infrastructure tools get elevated weight
    if tool_name in INFRASTRUCTURE_TOOLS:
        return 1.5

    if tool_name != 'code_execution_tool':
        return 1.0

    code = tool_args.get('code', '') or tool_args.get('runtime', '') or ''
    # Split into alpha-only tokens and check for verification keywords
    tokens = {t.lower() for t in _TOKEN_SPLITTER.split(code) if t}
    if tokens & _VERIFY_KEYWORDS:
        return 0.5

    return 1.0

