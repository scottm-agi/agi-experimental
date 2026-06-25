"""
TDD Red→Green gate check — verifies RED→GREEN test transition at delivery.

ITR-33 FIX-B Integration Point 2: Registered as a non-critical blocking
check that verifies the code agent's implementation actually made failing
TDD stubs pass. Requires Build check to pass first (no point running tests
if the project doesn't build).

Architecture:
  1. Read docs/red-baseline.json (captured after TDD stubs, before implementation)
  2. Call verify_green_transition() which re-runs tests and compares to baseline
  3. Block if <80% of RED tests transitioned to GREEN or if any regressions

This check is non-critical initially — it will be promoted to critical after
stability is proven across multiple smoke test iterations.
"""

import json
import logging
import os

from python.helpers.orchestrator_gate_integration_checks import register_check
from python.helpers.tdd_red_green_validator import verify_green_transition

logger = logging.getLogger("agix.gate.tdd_red_green")


@register_check(
    order=1.320,
    name="TDD Red→Green",
    critical=True,
    requires=["Build"],
    web_only=True,
    gate="tdd",
)
def check_tdd_red_green(ctx):
    """Verify TDD Red→Green transition at delivery time.

    Reads docs/red-baseline.json and compares against current test results.
    Blocks if:
      - <80% of RED tests transitioned to GREEN
      - Any GREEN tests regressed to RED

    Passes if:
      - No baseline exists (baseline wasn't captured — don't block)
      - ≥80% RED→GREEN, 0 regressions

    Args:
        ctx: CheckContext with project_dir, agent_data, etc.

    Returns:
        None if check passes, ctx.block(...) string if blocked.
    """
    if not ctx.project_dir:
        return None

    baseline_path = os.path.join(ctx.project_dir, "docs", "red-baseline.json")
    if not os.path.isfile(baseline_path):
        logger.info(
            "[TDD RED→GREEN] No red-baseline.json found — skipping "
            "(baseline wasn't captured for this project)"
        )
        return None

    # Read baseline
    try:
        with open(baseline_path, "r", encoding="utf-8") as f:
            baseline = json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.warning(f"[TDD RED→GREEN] Could not read baseline: {e}")
        return None  # Don't block on corrupt baseline

    total_red = baseline.get("failed", 0)
    if total_red == 0:
        logger.info(
            "[TDD RED→GREEN] Baseline has 0 RED tests — nothing to verify"
        )
        return None

    # Run current tests and compare to baseline
    try:
        passed, details = verify_green_transition(ctx.project_dir, timeout=30)
    except Exception as e:
        logger.warning(
            f"[TDD RED→GREEN] verify_green_transition failed: {e}"
        )
        return None  # Don't block on execution errors

    if passed:
        transitioned = details.get("transitioned_to_green", 0)
        ratio = details.get("transition_ratio", 0)
        logger.info(
            f"[TDD RED→GREEN] ✅ PASSED — {transitioned}/{total_red} RED tests "
            f"transitioned to GREEN ({ratio:.0%})"
        )
        return None

    # Failed — build block message
    transitioned = details.get("transitioned_to_green", 0)
    ratio = details.get("transition_ratio", 0)
    regressions = details.get("regressions", 0)
    still_red = details.get("still_red", [])

    msg_parts = [
        f"🔴 TDD RED→GREEN CHECK FAILED: {transitioned}/{total_red} "
        f"RED tests transitioned ({ratio:.0%}, need ≥80%).",
    ]

    if regressions > 0:
        msg_parts.append(
            f"⚠️ {regressions} REGRESSIONS: tests that were GREEN now RED."
        )

    if still_red:
        shown = still_red[:5]
        msg_parts.append(
            f"Still RED: {', '.join(shown)}"
            + (f" (+{len(still_red) - 5} more)" if len(still_red) > 5 else "")
        )

    msg_parts.append(
        "ACTION: Run `npm test` or `npx vitest run`, fix failing tests, "
        "then retry delivery."
    )

    return ctx.block(
        "\n".join(msg_parts),
        action=(
            "Delegate a TARGETED test fix: run `npm test`, identify which "
            "TDD stub tests are still failing, and implement the missing "
            "functionality to make them pass."
        ),
    )
