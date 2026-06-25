"""
Delegation Tool Capability Guard (SS-12)

L3 gate that detects when a delegation targets a profile that lacks
the required tools for the task. Catches misrouted delegations BEFORE
they fail with PROFILE_ENFORCEMENT errors.

Root cause: The frontend (designer) profile was delegated a task that
required code_execution_tool. PROFILE_ENFORCEMENT correctly blocked it,
but the root cause is the orchestrator delegating a code-execution task
to a restricted profile.

This guard inspects the task message for tool-capability signals and
cross-checks against the target profile's known restrictions.

Universal design: works for ANY profile configuration, not project-specific.
"""

import re
import logging
from typing import Dict, Optional

logger = logging.getLogger("agix.delegation_tool_guard")


# ── Tool-requirement signal patterns ──────────────────────────────────────

CODE_EXECUTION_SIGNALS = re.compile(
    r'\b(?:run|execute|install|npm|pip|test|build|compile|deploy|'
    r'start|serve|launch|command|script|terminal|shell|bash)\b',
    re.IGNORECASE,
)

FILE_WRITE_SIGNALS = re.compile(
    r'\b(?:create\s+file|write\s+to|modify|edit|implement|'
    r'add\s+(?:to|in)\s+(?:the\s+)?(?:file|code)|'
    r'update\s+(?:the|this)\s+(?:file|code|component))\b',
    re.IGNORECASE,
)

# ── Design-work counter-signals (reduce false positives) ─────────────────
# These indicate the task is DESIGN work, not code execution or file writing.
DESIGN_COUNTER_SIGNALS = re.compile(
    r'\b(?:design|mockup|wireframe|prototype|color\s+palette|'
    r'typography|spacing|layout\s+spec|style\s+guide|'
    r'ui\s+spec|ux\s+flow|visual|brand|logo|icon\s+set|'
    r'figma|sketch|adobe)\b',
    re.IGNORECASE,
)

# ── Research-work counter-signals (reduce false positives — RCA-ITR16) ───
# These indicate the task is RESEARCH work. Words like "build" in research
# context mean "compose a document", not "run a build command".
RESEARCH_COUNTER_SIGNALS = re.compile(
    r'\b(?:research|investigate|study|look\s+up|find\s+docs|'
    r'framework\s+(?:research|documentation|versions|compatibility)|'
    r'pre[- ]?fetch|compatibility|documentation|literature|'
    r'survey|analyze\s+(?:versions|patterns|docs)|'
    r'version\s+(?:check|comparison|confirmation)|'
    r'stable\s+version|latest\s+version)\b',
    re.IGNORECASE,
)

# ── Profiles that CANNOT run code or write files ─────────────────────────
RESTRICTED_PROFILES: Dict[str, Dict[str, list]] = {
    "frontend": {"cannot": ["code_execution", "file_write"]},
    "researcher": {"cannot": ["code_execution", "file_write"]},
}


def check_tool_capability_mismatch(
    message: str,
    profile: str,
) -> Optional[Dict]:
    """Detect when a delegation targets a profile that lacks required tools.

    L3 gate: catches misrouted delegations before they fail with
    PROFILE_ENFORCEMENT errors.

    Args:
        message: The delegation task message
        profile: The target profile name

    Returns:
        None if no mismatch detected.
        Dict with 'reason' and 'suggested_profile' if mismatch found.
    """
    if not message or not profile:
        return None

    restrictions = RESTRICTED_PROFILES.get(profile)
    if not restrictions:
        return None

    cannot = restrictions["cannot"]

    # Check for design counter-signals first — if the task looks like
    # pure design work, don't flag it even if words like "create" appear
    design_matches = len(DESIGN_COUNTER_SIGNALS.findall(message))

    # Check for research counter-signals — if the task looks like
    # research work, don't flag it even if words like "build" appear
    # (RCA-ITR16: "Build the framework research doc" is research, not code)
    research_matches = len(RESEARCH_COUNTER_SIGNALS.findall(message))

    # Combined counter-signal count
    counter_matches = design_matches + research_matches

    # Check code_execution signals
    if "code_execution" in cannot:
        code_matches = CODE_EXECUTION_SIGNALS.findall(message)
        if code_matches and counter_matches == 0:
            return {
                "reason": (
                    f"Task requires code execution but '{profile}' profile "
                    f"cannot run code. Signals: {code_matches[:3]}"
                ),
                "suggested_profile": "code",
            }

    # Check file_write signals
    if "file_write" in cannot:
        file_matches = FILE_WRITE_SIGNALS.findall(message)
        if file_matches and counter_matches == 0:
            return {
                "reason": (
                    f"Task requires file writes but '{profile}' profile "
                    f"cannot write files. Signals: {file_matches[:3]}"
                ),
                "suggested_profile": "code",
            }

    return None
