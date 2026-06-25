"""
Delegation Profile Router v2 — Designer/Developer Separation.

Redesigned from v1 (RCA-234): The original router auto-corrected `code` → `frontend`
for ANY UI-related task (design AND coding). This caused cross-agent file collisions
when both agents wrote JSX/CSS to the same files.

v2 separation:
  - `frontend` profile = **pure UI/UX Designer** (mockups, tokens, specs — NO code)
  - `code` profile = **Full-Stack Developer** (ALL source code including frontend pages)

The router now only routes **design** tasks (mockups, design system, color palette,
component specs) to `frontend`. ALL coding tasks — including frontend page
implementation — stay with `code`.

Usage in call_subordinate.py:
    from python.helpers.delegation_profile_router import should_use_designer_profile
    should_switch, reason = should_use_designer_profile(message, requested_profile)
    if should_switch:
        logger.warning(f"PROFILE ROUTER: Auto-correcting to frontend (designer): {reason}")
        config.profile = "frontend"
"""

import re
from typing import Tuple

# ─── DESIGN Signal Keywords (weighted) ─────────────────────────────────
# These detect tasks that are about DESIGNING — not coding.
# Score >= THRESHOLD triggers auto-correction to frontend (designer).

DESIGN_SIGNALS = [
    # Mockup & visual generation (weight 3-4)
    (r"\bmockups?\b", 4),
    (r"\bgenerate_image\b", 3),
    (r"\bdesign[\-\s]system\b", 4),
    (r"\bdesign[\-\s]tokens?\b", 4),
    (r"\bdesign[\-\s]tokens?\.json\b", 5),
    (r"\bvisual\s+design\b", 3),
    (r"\bui[\s/]ux\s+(design|spec)", 4),
    (r"\bphotorealistic\b", 3),

    # Color & typography (weight 3-4)
    (r"\bcolor\s+palette\b", 4),
    (r"\btypography\s+(system|scale|spec)", 3),
    (r"\bcolor\s+scale\b", 3),
    (r"\bdesign\s+language\b", 3),
    (r"\bdesign\b.*\bcolor\b", 3),
    (r"\bcolor\b.*\bdesign\b", 3),

    # Component specs (NOT code) (weight 3-4)
    (r"\bcomponent[\-\s]spec(ification)?\b", 4),
    (r"\bcomponent\s+hierarchy\b", 3),
    (r"\blayout\s+hierarchy\b", 3),
    (r"\bvisual\s+spec\b", 3),
    (r"\binteraction\s+pattern\b", 3),
    (r"\bcomponent.*\bprops\b.*\blayout\b", 3),

    # Design review (weight 3-4)
    (r"\bdesign\s+review\b", 4),
    (r"\bvisual\s+(fidelity|deviation|qa|audit)\b", 4),
    (r"\bscreenshots?\s+(against|vs|versus)\s+mockups?\b", 4),
    (r"\bmockup\s+alignment\b", 3),
    (r"\bvisual\s+comparison\b", 3),
    (r"\bvisual\s+deviation\b", 3),
    (r"\breview.*mockup\b", 3),
    (r"\bflag.*visual\b", 3),

    # Design phase explicit (weight 3)
    (r"\bphase\s+2\.3\b", 4),
    (r"\bphase\s+5\.0\.5\b", 3),
    (r"\bui[\s/]ux\s+design\s+phase\b", 4),
]

# ─── CODING Counter-signals (negative weight) ─────────────────────────
# These detect tasks that involve writing/modifying source code.
# Strong negative signals prevent design tasks from being misrouted
# when they mention frameworks or components by name.

CODING_COUNTER_SIGNALS = [
    # File creation/modification (weight -3 to -5)
    (r"\bcreate\s+\w+\.(tsx|jsx|ts|js|css|html)\b", -4),
    (r"\bwrite\s+(the|a)\s+(page|component|file)\b", -3),
    (r"\bbuild\s+(the|a)\s+(page|component|landing)\b", -4),
    (r"\bimplement\s+(the|a)\s+(page|feature|component)\b", -4),
    (r"\b(create|add|write)\s+.*\.(tsx|jsx)\b", -4),

    # Framework-specific coding (weight -3 to -5)
    (r"\bnpx\s+create-\b", -5),
    (r"\bnpm\s+(install|run|test)\b", -4),
    (r"\bcode_execution_tool\b", -5),
    (r"\bnpm\s+run\s+dev\b", -4),
    (r"\bnpm\s+run\s+build\b", -4),

    # Backend signals (weight -3)
    (r"\bapi\s+(endpoint|route)\b", -3),
    (r"\bdatabase\b", -3),
    (r"\bpostgresql?\b", -3),
    (r"\bprisma\b", -2),
    (r"\bjwt\b", -2),
    (r"\bauth(entication|orization)?\s+(system|middleware|logic)\b", -3),

    # Coding-specific language (weight -2 to -3)
    (r"\badd\s+(responsive\s+)?css\s+styling\b", -3),
    (r"\bhover\s+animations?\b", -2),
    (r"\bwire\s+(the\s+)?react\b", -3),
    (r"\bfetch\(\)\b", -3),
    (r"\bloading\s+states?\b", -2),
    (r"\berror\s+handling\b", -2),
    (r"\buse\s+client\b", -2),

    # Scaffolding (weight -5 to -8)
    (r"\bscaffold(ing)?\b", -5),
    (r"\bcreate-next-app\b", -8),
    (r"\bcreate-vite\b", -8),

    # Infrastructure (weight -3)
    (r"\bdocker\b", -3),
    (r"\bci/cd\b", -3),
    (r"\bdeployment\b", -3),
    (r"\brailway\b", -3),

    # Repository automation (weight -5 to -8) — RCA-webhook-20260612
    (r"\brepository_automation\b", -8),
    (r"\banalyze_issue\b", -5),
]

# Score threshold: must meet or exceed this to trigger auto-correction
DESIGN_SWITCH_THRESHOLD = 6

# Only these profiles are candidates for auto-correction TO frontend (designer)
CORRECTABLE_PROFILES = {"code"}


def should_use_designer_profile(
    message: str,
    requested_profile: str,
) -> Tuple[bool, str]:
    """Analyze task content and determine if it should use `frontend` (designer) profile.

    The frontend profile is now a pure UI/UX Designer. It should ONLY receive
    tasks about visual design (mockups, tokens, specs, design review) — NOT
    tasks about writing/modifying source code.

    Args:
        message: The delegation task message
        requested_profile: The profile chosen by the LLM

    Returns:
        (should_switch, reason): Whether to auto-correct, and why
    """
    # Short-circuit: only correct `code` → `frontend`
    if requested_profile not in CORRECTABLE_PROFILES:
        return (False, f"Profile '{requested_profile}' is not correctable")

    if not message or not message.strip():
        return (False, "Empty message")

    # ── RCA-webhook-20260612: Repo automation bypass ──
    # Repository automation tasks (webhook-triggered issue analysis, branch
    # building, deployments) are NEVER design work. The delegation message
    # may contain design-system/design-tokens from the standard phase table,
    # but these are informational — not task instructions.
    _REPO_AUTOMATION_BYPASS = re.compile(
        r"\brepository_automation\b|\banalyze_issue\b|\bbuild_branch\b|"
        r"\bmerge_all\b|\bdeploy_to_cloud\b",
        re.I
    )
    if _REPO_AUTOMATION_BYPASS.search(message):
        return (False, "Repo automation bypass: task references repository automation tools")

    msg_lower = message.lower()

    # Score design signals
    score = 0
    matched_signals = []

    for pattern, weight in DESIGN_SIGNALS:
        if re.search(pattern, msg_lower):
            score += weight
            matched_signals.append((pattern, weight))

    # Score coding counter-signals
    coding_matches = []
    for pattern, weight in CODING_COUNTER_SIGNALS:
        if re.search(pattern, msg_lower):
            score += weight  # weight is negative
            coding_matches.append((pattern, weight))

    # Decision
    if score >= DESIGN_SWITCH_THRESHOLD:
        top_signals = sorted(matched_signals, key=lambda x: -x[1])[:5]
        signal_summary = ", ".join(f"{p}(+{w})" for p, w in top_signals)
        reason = (
            f"Design score {score} >= {DESIGN_SWITCH_THRESHOLD}. "
            f"Top signals: {signal_summary}. "
            f"Task appears to be UI/UX DESIGN work (mockups, tokens, specs) "
            f"that should use the 'frontend' (designer) profile."
        )
        return (True, reason)

    reason = (
        f"Design score {score} < {DESIGN_SWITCH_THRESHOLD}. "
        f"Task does not have enough design signals — stays with code agent."
    )
    return (False, reason)


# ─── DEBUG → CODE Misrouting Guard (RCA-346 F-4) ──────────────────────
# The `debug` profile is for DIAGNOSIS ONLY — it has NO `write_to_file`.
# When the orchestrator mistakenly routes file-editing tasks to debug,
# this guard detects the misrouting and corrects to `code`.

# File-editing signal keywords (positive weight → indicates file-editing)
FILE_EDIT_SIGNALS = [
    # File creation/modification (weight 3-5)
    (r"\bfix\s+(the\s+)?file\b", 4),
    (r"\breplace\s+placeholder", 4),
    (r"\bcreate\s+(new\s+)?(\w+\s+)?(component|page|file|module)\b", 4),
    (r"\bcreate\s+new\b", 3),
    (r"\bupdate\s+(all\s+)?(routes?|pages?|files?|components?)\b", 3),
    (r"\bmodify\s+(the\s+)?(code|file|source)\b", 4),
    (r"\bwrite_to_file\b", 5),
    (r"\breplace_in_file\b", 5),
    (r"\bsed\s+-i\b", 5),
    (r"\b(create|add|write)\s+.*\.(tsx|jsx|ts|js|css|html|py)\b", 4),
    (r"\bimplement\s+(the\s+)?(fix|feature|component|page)\b", 3),
    (r"\bbuild\s+(the\s+)?(component|page|feature)\b", 3),
    (r"\brefactor\s+(the\s+)?(code|file|function)\b", 3),
    (r"\bfix\s+(the\s+)?(bug|issue|error)\s+in\b", 3),
    (r"\bpatch\s+(the\s+)?(file|code)\b", 3),
    (r"\brewrite\s+(the\s+)?(file|code|function)\b", 4),
    (r"\b(edit|change)\s+(the\s+)?(file|code|source)\b", 3),
    (r"\breal\s+(content|data|text)\b", 3),

    # File path references with modification intent (weight 3)
    (r"\bpage\.tsx\b", 2),
    (r"\blayout\.tsx\b", 2),
    (r"\b\w+\.(tsx|jsx)\b", 1),
]

# Debug-legitimate counter-signals (negative weight → indicates real debug work)
DEBUG_LEGITIMATE_SIGNALS = [
    # Diagnosis keywords (weight -3 to -5)
    (r"\bdiagnos(e|is|tic)\b", -5),
    (r"\binvestigat(e|ion)\b", -5),
    (r"\bcheck\s+(the\s+)?(logs?|error|output)\b", -4),
    (r"\bfind\s+(the\s+)?root\s+cause\b", -5),
    (r"\btraceback\b", -4),
    (r"\bdebug\s+(the\s+)?(memory|leak|crash|issue|problem)\b", -4),
    (r"\blog\s+(analysis|examination)\b", -4),
    (r"\berror\s+logs?\b", -3),
    (r"\b(stack|call)\s*trace\b", -4),
    (r"\bprofil(e|ing)\s+(cpu|memory|performance)\b", -4),
    (r"\bwhy\s+(does|is|did)\b", -3),
    (r"\bwhat\s+caus(es?|ed|ing)\b", -3),
    (r"\bexamine\b", -3),
    (r"\binspect\b", -3),
    (r"\btroubleshoot\b", -4),
    (r"\banalyze\s+(the\s+)?(error|crash|failure|bug)\b", -4),
]

# Score threshold for debug→code correction
DEBUG_CORRECTION_THRESHOLD = 5

# Only the debug profile is a candidate for this correction
DEBUG_CORRECTABLE_PROFILES = {"debug"}


def should_correct_debug_to_code(
    message: str,
    requested_profile: str,
) -> Tuple[bool, str]:
    """Analyze task content and determine if a debug delegation should be corrected to code.

    The debug profile is for DIAGNOSIS ONLY — it has NO write_to_file tool.
    Tasks involving file creation, code modification, or content replacement
    must go to the code profile instead.

    Args:
        message: The delegation task message
        requested_profile: The profile chosen by the LLM

    Returns:
        (should_correct, reason): Whether to auto-correct debug→code, and why
    """
    # Short-circuit: only correct `debug` profile
    if requested_profile not in DEBUG_CORRECTABLE_PROFILES:
        return (False, f"Profile '{requested_profile}' is not debug — no correction needed")

    if not message or not message.strip():
        return (False, "Empty message")

    msg_lower = message.lower()

    # Score file-edit signals
    score = 0
    matched_signals = []

    for pattern, weight in FILE_EDIT_SIGNALS:
        if re.search(pattern, msg_lower):
            score += weight
            matched_signals.append((pattern, weight))

    # Score debug counter-signals
    debug_matches = []
    for pattern, weight in DEBUG_LEGITIMATE_SIGNALS:
        if re.search(pattern, msg_lower):
            score += weight  # weight is negative
            debug_matches.append((pattern, weight))

    # Decision
    if score >= DEBUG_CORRECTION_THRESHOLD:
        top_signals = sorted(matched_signals, key=lambda x: -x[1])[:5]
        signal_summary = ", ".join(f"{p}(+{w})" for p, w in top_signals)
        reason = (
            f"File-edit score {score} >= {DEBUG_CORRECTION_THRESHOLD}. "
            f"Top signals: {signal_summary}. "
            f"Task requires file-editing capabilities (write_to_file) "
            f"that the debug profile does not have. Correcting to 'code'."
        )
        return (True, reason)

    reason = (
        f"File-edit score {score} < {DEBUG_CORRECTION_THRESHOLD}. "
        f"Task appears to be legitimate debug/diagnosis work — stays with debug."
    )
    return (False, reason)


# ─── Backward Compatibility Alias ──────────────────────────────────────
# The old function name is still used in tests and may be referenced elsewhere.
# Keep it as an alias to prevent import errors during rollout.

should_use_frontend_profile = should_use_designer_profile


# ─── FRONTEND → CODE Misrouting Guard (ISS-4 P1) ──────────────────────
# The `frontend` profile is a pure UI/UX Designer — it has NO `write_to_file`,
# `code_execution_tool`, or `replace_in_file`. When the designer router
# (should_use_designer_profile) routes a task to frontend, but the task ALSO
# requires file-writing operations, the frontend agent loops on
# PROFILE_ENFORCEMENT blocks → HARD_STOP, wasting ~600s per cycle.
#
# This guard detects file-write requirements in tasks already routed to
# frontend and corrects them to `code`. It is the symmetric counterpart
# of should_correct_debug_to_code (RCA-346 F-4).

# Frontend-legitimate counter-signals (negative weight → indicates pure design)
FRONTEND_DESIGN_SIGNALS = [
    # save_deliverable is allowed in frontend (weight -4 to -5)
    (r"\bsave_deliverable\b", -5),
    (r"\bgenerate_image\b", -4),
    (r"\ba2ui_generate\b", -3),
    (r"\bread_deliverables?\b", -3),
    (r"\bmermaid_renderer\b", -3),
    (r"\bvision_load\b", -3),
    # Pure design language (weight -3 to -4)
    (r"\bvisual\s+(qa|audit|review|comparison|fidelity)\b", -4),
    (r"\bdesign\s+review\b", -4),
    (r"\bflag.*visual\s+deviation\b", -3),
    (r"\bvia\s+`?save_deliverable`?\b", -5),
    (r"\bvia\s+`?generate_image`?\b", -4),
]

# Extra filesystem-operation signals specific to frontend→code correction.
# These catch file operations not covered by the generic FILE_EDIT_SIGNALS
# (which were designed for debug→code). The frontend profile can't do ANY
# filesystem operations — not just file writes.
FRONTEND_FILESYSTEM_SIGNALS = [
    # File copy/move operations (weight 4-5)
    (r"\bcopy\s+\S+\s+(to|into)\b", 5),
    (r"\bmove\s+\S+\s+(to|into)\b", 5),
    (r"\bcopy\s+(deliverables?|artifacts?|files?)\b", 4),
    (r"\b(align|sync)\s+(planning\s+)?artifacts?\b", 4),
    # Install/setup operations (weight 4-5)
    (r"\bnpm\s+install\b", 5),
    (r"\bnpx\s+", 5),
    (r"\byarn\s+(add|install)\b", 5),
    (r"\bpip\s+install\b", 5),
    (r"\bset\s+up\s+(the\s+)?project\b", 3),
    # Config file creation (weight 4)
    (r"\b(create|write|generate)\s+\w*\s*config\s+file\b", 4),
    (r"\btailwind\s+config\b", 4),
    (r"\b(tsconfig|next\.config|vite\.config|webpack\.config)\b", 4),
    # File verification requiring shell access (weight 3)
    (r"\bverify\s+\S+\s+(exists?|is\s+in\s+place)\b", 3),
    (r"\bcheck\s+if\s+\S+\s+exists?\b", 3),
    # Explicit directory operations (weight 4)
    (r"\b(to|into)\s+(the\s+)?(project\s+)?root(\s+directory)?\b", 4),
    (r"\b(mkdir|rmdir|rm\s+-rf)\b", 5),
]

# Score threshold: must meet or exceed this to trigger auto-correction
FRONTEND_CORRECTION_THRESHOLD = 5


# Only the frontend profile is a candidate for this correction
FRONTEND_CORRECTABLE_PROFILES = {"frontend"}


def should_correct_frontend_to_code(
    message: str,
    requested_profile: str,
) -> Tuple[bool, str]:
    """Analyze task content and determine if a frontend delegation should be corrected to code.

    The frontend profile is a pure UI/UX Designer — it has NO write_to_file,
    code_execution_tool, or replace_in_file. Tasks involving file creation,
    code modification, npm operations, or scaffolding must go to the code
    profile instead.

    This prevents the ISS-4 PROFILE_ENFORCEMENT loop where the frontend agent
    wastes turns trying unauthorized tools -> getting blocked -> retrying.

    Args:
        message: The delegation task message
        requested_profile: The profile chosen (should be 'frontend')

    Returns:
        (should_correct, reason): Whether to auto-correct frontend->code, and why
    """
    # Short-circuit: only correct `frontend` profile
    if requested_profile not in FRONTEND_CORRECTABLE_PROFILES:
        return (False, f"Profile '{requested_profile}' is not frontend -- no correction needed")

    if not message or not message.strip():
        return (False, "Empty message")

    msg_lower = message.lower()

    # Score file-edit signals (reuse the debug->code FILE_EDIT_SIGNALS)
    score = 0
    matched_signals = []

    for pattern, weight in FILE_EDIT_SIGNALS:
        if re.search(pattern, msg_lower):
            score += weight
            matched_signals.append((pattern, weight))

    # Also check CODING_COUNTER_SIGNALS (these are negative for designer routing
    # but POSITIVE for frontend->code correction -- they indicate coding work)
    for pattern, neg_weight in CODING_COUNTER_SIGNALS:
        if re.search(pattern, msg_lower):
            # Flip the sign: coding signals are positive indicators for correction
            pos_weight = abs(neg_weight)
            score += pos_weight
            matched_signals.append((pattern, pos_weight))

    # Check frontend-specific filesystem signals (copy, npm install, config, etc.)
    for pattern, weight in FRONTEND_FILESYSTEM_SIGNALS:
        if re.search(pattern, msg_lower):
            score += weight
            matched_signals.append((pattern, weight))

    # Score frontend-design counter-signals (reduces score for pure design)

    design_matches = []
    for pattern, weight in FRONTEND_DESIGN_SIGNALS:
        if re.search(pattern, msg_lower):
            score += weight  # weight is negative
            design_matches.append((pattern, weight))

    # Decision
    if score >= FRONTEND_CORRECTION_THRESHOLD:
        top_signals = sorted(matched_signals, key=lambda x: -x[1])[:5]
        signal_summary = ", ".join(f"{p}(+{w})" for p, w in top_signals)
        reason = (
            f"File-write score {score} >= {FRONTEND_CORRECTION_THRESHOLD}. "
            f"Top signals: {signal_summary}. "
            f"Task requires file-writing capabilities (write_to_file, code_execution_tool) "
            f"that the frontend profile does not have. Correcting to 'code'."
        )
        return (True, reason)

    reason = (
        f"File-write score {score} < {FRONTEND_CORRECTION_THRESHOLD}. "
        f"Task appears to be pure design work -- stays with frontend (designer)."
    )
    return (False, reason)

