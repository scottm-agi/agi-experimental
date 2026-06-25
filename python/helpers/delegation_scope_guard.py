"""
Delegation Scope Guard (RCA-ITR36).

Detects fix-mode delegations and injects structured fix-mode framing
so the code agent clearly understands it's in fix/additive mode, not
build mode. Also detects overbroad rewrite signals and injects corrective
framing to prevent destructive scope expansion.

Per user directive: "orchestrator should be smart enough to know what it
needs agents to do — ensure it gives enough context but is specific."

This guard TRANSFORMS delegations by adding operational mode context,
not by blocking or stripping content.
"""
from __future__ import annotations

import re
from typing import Optional

# ── Phase detection patterns (case-insensitive) ─────────────────────
_PHASE_TRIGGERS = [
    r"phase\s*4",
    r"phase\s*5",
    r"phase\s*6",
    r"frontend[- ]?backend\s+integration",
    r"verification\s+failure",
    r"iteration",
]
_PHASE_RE = re.compile(
    "|".join(_PHASE_TRIGGERS),
    re.IGNORECASE,
)

# ── Fix-mode detection (broader than just "Phase N") ────────────────
_FIX_MODE_SIGNALS = [
    r"surgical\s+fix",
    r"fix\s+(?:the\s+)?(?:following|these|this|only|specific)",
    r"verification\s+failure",
    r"additive\s+only",
    r"do\s+not\s+rewrite",
    r"prohibited\s+changes",
    r"fix\s+mode",
    r"resolve\s+(?:this|the)",
    r"debug\s+(?:this|the)",
    r"regression\s+fix",
    r"targeted\s+fix",
    r"hotfix",
    r"patch\s+the",
]
_FIX_MODE_RE = re.compile(
    "|".join(_FIX_MODE_SIGNALS),
    re.IGNORECASE,
)

# ── BROAD signals: indicate overbroad scope ─────────────────────────
_BROAD_SIGNALS = [
    r"rewrite\s+all",
    r"replace\s+all",
    r"refactor",
    r"convert\s+client\s+to\s+server",
    r"rewrite\s+from\s+scratch",
    r"replace\s+ALL\s+hardcoded",
]
_BROAD_RE = re.compile(
    "|".join(_BROAD_SIGNALS),
    re.IGNORECASE,
)

# ── DIAGNOSIS SCOPE CREEP signals (RCA-ITR36 TRUE ROOT CAUSE) ────────
_DIAGNOSIS_CREEP_SIGNALS = [
    r"still\s+(?:contains?|uses?)\s+(?:hardcoded|mock)\s+(?:data|object)",
    r"(?:hardcoded|mock)\s+(?:data|object).*violation",
    r"replace\s+(?:the\s+)?(?:hardcoded|mock)\s+.*(?:with|using)\s+(?:real|prisma|api)",
    r"implement\s+real\s+data\s+(?:fetching|wiring)",
]
_DIAGNOSIS_CREEP_RE = re.compile(
    "|".join(_DIAGNOSIS_CREEP_SIGNALS),
    re.IGNORECASE,
)

# ── SURGICAL signals: indicate proper narrow scope ──────────────────
_SURGICAL_SIGNALS = [
    r"surgical",
    r"specific\s+failure",
    r"minimum\s+change",
    r"targeted\s+fix",
    r"do\s+not\s+rewrite",
]
_SURGICAL_RE = re.compile(
    "|".join(_SURGICAL_SIGNALS),
    re.IGNORECASE,
)

# ── File path pattern (matches common source paths) ─────────────────
_FILE_PATH_RE = re.compile(
    r"(?:src|app|pages|components|lib|prisma|public|styles|utils|hooks|api)/"
    r"[\w./-]+\.\w+",
)

# Max existing files allowed to be modified per delegation
_MAX_FILE_BUDGET = 5  # Raised from 3 to 5 — small fixes sometimes touch 4


def _build_fix_mode_frame(detected_signal: str = "") -> str:
    """Build the fix-mode operational frame injected into delegations.

    This is the key mechanism that tells the code agent it's in fix mode.
    Per user directive: code agent should have a clear understanding of
    fix/finish-gaps operation vs building new.
    """
    return (
        "\n---\n"
        "## 🔴 OPERATIONAL MODE: FIX / ADDITIVE ONLY\n\n"
        "You are operating in **Fix Mode**. All changes MUST be **additive** — "
        "never destructive. You are fixing a specific issue, NOT building from scratch.\n\n"
        "### Rules\n"
        "1. **Read first**: Read EVERY file you plan to modify BEFORE making changes\n"
        "2. **Scope narrowly**: Fix ONLY the specific error described in your brief\n"
        "3. **Preserve working code**: Do NOT rewrite files that are functioning correctly\n"
        "4. **No refactoring**: No component conversions, no import reorganization, "
        "no data-fetching pattern changes\n"
        "5. **Minimal footprint**: Modify at most 5 existing files per fix\n"
        "6. **Verify after**: Confirm the build still passes after your changes\n"
        "7. **Revert on regression**: If the build was passing before and breaks "
        "after your changes, REVERT and try a smaller fix\n\n"
        "### What the context sections mean\n"
        "- **Type Contract / Codebase State**: These are for REFERENCE ONLY — "
        "to help you understand existing code. Do NOT interpret them as instructions "
        "to rebuild or recreate.\n"
        "- **Error/Verification findings**: These ARE your task — fix these specific issues.\n"
        "- **Requirements/BDD/Mockups**: If present, they are background reference. "
        "Your task is the FIX, not implementing all requirements.\n"
        "\n---\n"
    )


def check_delegation_scope_guard(message: str) -> Optional[str]:
    """Check delegation message and inject fix-mode framing if needed.

    Detects fix-mode delegations and returns structured operational mode
    framing. Also detects overbroad rewrite signals and returns corrective
    framing to prevent destructive scope expansion.

    This is a TRANSFORMING guard — it adds context to help the code agent
    understand its operational mode, rather than stripping or blocking.

    Args:
        message: The delegation message to inspect.

    Returns:
        A fix-mode frame or corrective warning string to append,
        or None if the message doesn't need framing.
    """
    if not message:
        return None

    # ── Step 1: Is this a fix-mode delegation? ──
    is_fix_mode = bool(_FIX_MODE_RE.search(message))
    is_phase_delegation = bool(_PHASE_RE.search(message))

    if not is_fix_mode and not is_phase_delegation:
        return None

    # ── Step 2: Check for SURGICAL signals (already properly scoped) ──
    has_surgical = bool(_SURGICAL_RE.search(message))

    # ── Step 3: Check for BROAD signals ──
    broad_match = _BROAD_RE.search(message)
    has_broad = bool(broad_match)

    # If broad AND no surgical → inject corrective frame + warning
    if has_broad and not has_surgical:
        detected = broad_match.group(0) if broad_match else "overbroad signal"
        return (
            _build_fix_mode_frame(detected) +
            f"\n⚠️ SCOPE CORRECTION (RCA-ITR36): Detected overbroad "
            f"signal: \"{detected}\". Your brief may contain broad "
            f"instructions, but you MUST apply Fix Mode rules above — "
            f"make only surgical changes to the specific files/lines "
            f"that caused the failure. "
            f"New file creation (e.g., seed scripts) is allowed.\n"
        )

    # ── Step 4: Check for DIAGNOSIS SCOPE CREEP (RCA-ITR36) ──
    if _DIAGNOSIS_CREEP_RE.search(message):
        creep_match = _DIAGNOSIS_CREEP_RE.search(message)
        detected = creep_match.group(0) if creep_match else "diagnosis scope creep"
        if re.search(r"phase\s*[56]|verification\s+failure", message, re.IGNORECASE):
            return (
                _build_fix_mode_frame(detected) +
                f"\n⚠️ DIAGNOSIS SCOPE CREEP (RCA-ITR36): \"{detected}\" — "
                f"Mock data in WORKING pages is NOT a fix-mode issue. "
                f"Fix-mode MUST ONLY fix pages that CRASH or return errors. "
                f"Do NOT rewrite working pages to use real data during fixes.\n"
            )

    # ── Step 5: Check file budget ──
    file_paths = _FILE_PATH_RE.findall(message)
    if len(file_paths) > _MAX_FILE_BUDGET:
        return (
            _build_fix_mode_frame("file budget exceeded") +
            f"\n⚠️ FILE BUDGET (RCA-ITR36): This delegation targets "
            f"{len(file_paths)} files ({', '.join(file_paths[:5])}...). "
            f"Fix-mode changes should be surgical — modify max "
            f"{_MAX_FILE_BUDGET} existing files per delegation.\n"
        )

    # ── Step 6: Always inject fix-mode frame for fix-mode delegations ──
    if is_fix_mode:
        return _build_fix_mode_frame()

    return None


# Alias for use by build_delegation_package (ISS-A: scope guard as first section)
get_fix_mode_frame = check_delegation_scope_guard
