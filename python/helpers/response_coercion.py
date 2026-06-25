"""
Response Coercion — Subordinate Response → str at Delegation Boundary

RCA-354 I-2: When monologue() returns a Response with break_loop=False,
the subordinate's response was REJECTED internally (near-dup detector,
fidelity gate, etc.). The Response.message is the rejection feedback,
NOT the agent's actual work output.

Previous behavior: coerce Response→str by extracting .message, discarding
break_loop. This caused the quality gate to scan rejection boilerplate
("NEAR-DUPLICATE RESPONSE REJECTED...") instead of real code output,
producing false 0/15 keyword scores.

Fix: When break_loop=False, prepend [RESPONSE_REJECTED] sentinel so
build_delegation_result classifies the status as "partial" instead of
"success". The quality gate only runs for "success" status, so it's
automatically skipped for rejected responses.
"""
import logging

logger = logging.getLogger("agix.response_coercion")

# Sentinel tag for internally-rejected subordinate responses.
# build_delegation_result checks for this tag to classify as "partial".
RESPONSE_REJECTED_TAG = "[RESPONSE_REJECTED]"


def coerce_subordinate_response(result) -> str:
    """Coerce a monologue() return value to str at the delegation boundary.

    Handles three cases:
    1. Response with break_loop=True → extract .message (normal delivery)
    2. Response with break_loop=False → tag with [RESPONSE_REJECTED] sentinel
       (subordinate's response was internally rejected by a gate)
    3. Non-Response (str, None) → pass through

    Args:
        result: The return value from subordinate.monologue()

    Returns:
        str — the coerced result, possibly with sentinel prefix
    """
    from python.helpers.tool import Response

    if not isinstance(result, Response):
        # Non-Response passthrough (str, None, etc.)
        return result if result is not None else ""

    message = result.message or ""

    if result.break_loop:
        # Normal accepted response — extract message as-is
        logger.info(
            f"[RCA-354] Coercing accepted Response→str "
            f"({len(message)} chars, break_loop=True)"
        )
        return message
    else:
        # Internally rejected response — tag for downstream classification
        logger.warning(
            f"[RCA-354] Coercing REJECTED Response→str "
            f"({len(message)} chars, break_loop=False). "
            f"Tagging with {RESPONSE_REJECTED_TAG} for partial classification."
        )
        return f"{RESPONSE_REJECTED_TAG} {message}"
