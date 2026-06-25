"""Schema-driven MCP argument normalization (P4 fix).

Instead of maintaining hardcoded alias maps per tool, this module uses the
MCP tool's actual JSON schema (cached at init) to auto-remap unknown
parameters to the correct required parameter names.

Algorithm:
1. Identify required params from schema that are MISSING from agent args
2. Identify params sent by agent that are NOT in the schema
3. If exactly 1 missing required + 1+ unknowns, remap first unknown → required
4. If multiple missing required, attempt 1:1 matching by position
5. Log all remappings for debugging
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, Set

logger = logging.getLogger("agix.mcp_normalizer")


def schema_driven_normalize(
    args: Dict[str, Any],
    schema: Dict[str, Any],
    tool_name: str,
) -> Dict[str, Any]:
    """Normalize MCP tool arguments using the tool's JSON schema.

    This function auto-remaps parameters that the LLM hallucinated from
    training data to the actual parameter names the MCP tool expects.

    Args:
        args: The arguments provided by the LLM
        schema: The tool's inputSchema from MCP list_tools
        tool_name: Tool name for logging

    Returns:
        New dict with normalized arguments (original dict is NOT mutated)
    """
    if not args or not schema:
        return dict(args) if args else {}

    # Extract required and known params from schema
    required: Set[str] = set(schema.get("required", []))
    properties: Dict = schema.get("properties", {})
    known_params: Set[str] = set(properties.keys())

    if not required:
        # No required params → nothing to remap
        return dict(args)

    # Find missing required params and unknown agent params
    provided: Set[str] = set(args.keys())
    missing_required = required - provided
    unknown_params = provided - known_params

    if not missing_required or not unknown_params:
        # Either all required params present, or no unknowns to remap from
        return dict(args)

    # Build the normalized result (new dict, don't mutate input)
    result = dict(args)

    # RCA-ITR355 RC-D: When multiple unknowns need remapping, use semantic
    # matching instead of positional. Without this, set ordering is
    # non-deterministic and 'topic' could map to 'libraryId' instead of 'query'.
    # Algorithm: For each missing required param, find the unknown param that
    # shares the most common substrings (case-insensitive).
    def _semantic_score(unknown: str, required: str) -> int:
        """Score how well an unknown param name matches a required param name."""
        u_lower = unknown.lower()
        r_lower = required.lower()
        score = 0
        # Check if required name appears as substring in unknown
        if r_lower in u_lower:
            score += 100
        # Check individual words (split by uppercase transitions on ORIGINAL casing)
        import re
        u_words = set(w.lower() for w in re.split(r'(?=[A-Z])|_|-', unknown) if len(w) > 1)
        r_words = set(w.lower() for w in re.split(r'(?=[A-Z])|_|-', required) if len(w) > 1)
        score += len(u_words & r_words) * 20
        # Check shared character trigrams
        u_trigrams = set(u_lower[i:i+3] for i in range(len(u_lower)-2))
        r_trigrams = set(r_lower[i:i+3] for i in range(len(r_lower)-2))
        score += len(u_trigrams & r_trigrams) * 2
        return score

    remaining_unknowns = list(unknown_params)

    for req_param in sorted(missing_required):  # sorted for determinism
        if not remaining_unknowns:
            break

        # Find the best matching unknown param by semantic similarity
        best_donor = max(remaining_unknowns, key=lambda u: _semantic_score(u, req_param))
        remaining_unknowns.remove(best_donor)
        value = result.pop(best_donor)
        result[req_param] = value

        logger.info(
            f"[MCP_SCHEMA_NORMALIZE] {tool_name}: remapped "
            f"'{best_donor}' → '{req_param}' (value={str(value)[:80]})"
        )

    return result
