"""
Pre-Build Advisor — inject type-check hint before first build.

RCA 211 Fix 3: Agents waste ~60% of build cycles on type errors that could
be caught in seconds with `npx tsc --noEmit`. This module provides a one-shot
hint on the first build attempt.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("agix.pre_build_advisor")


def get_pre_build_hint(agent_data: dict) -> str | None:
    """Get a pre-build type-check hint (one-shot).

    On the first build attempt, returns a hint to run `npx tsc --noEmit`
    first. After the first injection, returns None to avoid spamming.

    Args:
        agent_data: The agent's data dict.

    Returns:
        Hint message on first call, None on subsequent calls.
    """
    if agent_data.get("_pre_build_hint_injected"):
        return None

    agent_data["_pre_build_hint_injected"] = True

    logger.info("[PRE-BUILD] Injecting type-check hint (first build attempt)")

    return (
        "💡 **PRE-BUILD TYPE CHECK**: Before running `npm run build`, first run:\n\n"
        "```\nnpx tsc --noEmit\n```\n\n"
        "This catches TypeScript errors in seconds (vs minutes for a full build).\n"
        "Fix all type errors BEFORE attempting the full build.\n\n"
        "Common issues:\n"
        "- Missing imports → add import statements\n"
        "- Type mismatches → align types with Prisma schema\n"
        "- Async params → use `await` for Next.js 15 dynamic route params\n"
    )
