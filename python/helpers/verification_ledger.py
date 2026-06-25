"""Verification Ledger — project-level gate state persistence.

Replaces ephemeral per-agent ``_self_check_blocks`` counter with a
disk-persisted ledger at ``docs/.verification_state.json``.

RCA: ITR-43 had 39 self-check loops because the counter reset per delegation.
This module survives delegation boundaries, agent restarts, and crashes.

Key features:
- Result hashing: same failure twice = unfixable (no more wasted attempts)
- Known unfixable patterns: ``_not-found prerender`` auto-classified
- Per-check attempt budget: MAX_ATTEMPTS_PER_CHECK = 3
- Global block budget: GLOBAL_BLOCK_BUDGET = 10
- Verdicts: 'passed', 'fixable', 'unfixable', 'exhausted'

Usage::

    from python.helpers.verification_ledger import VerificationLedger

    ledger = VerificationLedger(project_dir)
    verdict = ledger.record("build_pass", passed=False, failures=["error..."])
    if ledger.should_block("build_pass"):
        # block the response
        ...
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from typing import Dict, List, Optional

logger = logging.getLogger("agix.verification_ledger")

# ── Constants ────────────────────────────────────────────────────────────

MAX_ATTEMPTS_PER_CHECK = 3
GLOBAL_BLOCK_BUDGET = 10
STATE_FILENAME = ".verification_state.json"

# Known unfixable patterns — if ANY failure message matches, immediately
# classify as 'unfixable' (no point retrying).
KNOWN_UNFIXABLE_PATTERNS = [
    re.compile(r"_not-found\s+prerender", re.IGNORECASE),
]

# Module-level lock for thread-safe file access
_ledger_lock = threading.Lock()


# ── VerificationLedger ───────────────────────────────────────────────────

class VerificationLedger:
    """Project-level verification state that persists across delegations.

    State is stored at ``<project_dir>/docs/.verification_state.json``.
    Thread-safe via a module-level lock.

    Attributes:
        project_dir: Absolute path to the project root.
        state: The in-memory state dict, mirroring the JSON on disk.
    """

    def __init__(self, project_dir: str) -> None:
        self.project_dir = project_dir
        self._state_path = os.path.join(project_dir, "docs", STATE_FILENAME)
        self.state = self._load()

    # ── Public API ───────────────────────────────────────────────────────

    def record(
        self,
        check_name: str,
        passed: bool,
        failures: List[str],
    ) -> str:
        """Record a self-check result and return a verdict string.

        Verdicts:
          - ``'passed'``: check succeeded
          - ``'fixable'``: first-time or new failure, worth retrying
          - ``'unfixable'``: same failure hash seen before, or known pattern
          - ``'exhausted'``: per-check or global attempt budget spent

        Args:
            check_name: Short name of the check (e.g. ``"build_pass"``).
            passed: Whether the check passed.
            failures: List of failure message strings (empty if passed).

        Returns:
            Verdict string: one of ``'passed'``, ``'fixable'``,
            ``'unfixable'``, ``'exhausted'``.
        """
        checks = self.state.setdefault("checks", {})
        check_state = checks.setdefault(check_name, {
            "attempts": 0,
            "verdict": "unknown",
            "failure_hashes": [],
        })

        if passed:
            check_state["verdict"] = "passed"
            check_state["attempts"] = 0
            check_state["failure_hashes"] = []
            self._save()
            return "passed"

        # ── Check known unfixable patterns ───────────────────────────────
        failure_text = " ".join(failures)
        for pattern in KNOWN_UNFIXABLE_PATTERNS:
            if pattern.search(failure_text):
                check_state["verdict"] = "unfixable"
                self._save()
                logger.info(
                    f"[VERIFICATION LEDGER] {check_name}: "
                    f"known unfixable pattern matched → unfixable"
                )
                return "unfixable"

        # ── Check global budget ──────────────────────────────────────────
        total_blocks = self.state.get("global_blocks", 0)
        if total_blocks >= GLOBAL_BLOCK_BUDGET:
            check_state["verdict"] = "exhausted"
            self._save()
            logger.info(
                f"[VERIFICATION LEDGER] {check_name}: "
                f"global budget exhausted ({total_blocks}/{GLOBAL_BLOCK_BUDGET})"
            )
            return "exhausted"

        # ── Compute failure hash ─────────────────────────────────────────
        failure_hash = self._hash_failures(failures)

        # ── Same-failure detection ───────────────────────────────────────
        seen_hashes = check_state.get("failure_hashes", [])
        if failure_hash in seen_hashes:
            check_state["verdict"] = "unfixable"
            self._save()
            logger.info(
                f"[VERIFICATION LEDGER] {check_name}: "
                f"same failure hash {failure_hash[:8]} repeated → unfixable"
            )
            return "unfixable"

        # ── Per-check attempt budget ─────────────────────────────────────
        check_state["attempts"] = check_state.get("attempts", 0) + 1
        seen_hashes.append(failure_hash)
        check_state["failure_hashes"] = seen_hashes

        if check_state["attempts"] >= MAX_ATTEMPTS_PER_CHECK:
            check_state["verdict"] = "exhausted"
            self.state["global_blocks"] = total_blocks + 1
            self._save()
            logger.info(
                f"[VERIFICATION LEDGER] {check_name}: "
                f"per-check attempts exhausted ({check_state['attempts']}/"
                f"{MAX_ATTEMPTS_PER_CHECK})"
            )
            return "exhausted"

        # ── Fixable — increment global blocks ────────────────────────────
        check_state["verdict"] = "fixable"
        self.state["global_blocks"] = total_blocks + 1
        self._save()
        logger.info(
            f"[VERIFICATION LEDGER] {check_name}: "
            f"fixable (attempt {check_state['attempts']}/{MAX_ATTEMPTS_PER_CHECK}, "
            f"global {total_blocks + 1}/{GLOBAL_BLOCK_BUDGET})"
        )
        return "fixable"

    def should_block(self, check_name: str) -> bool:
        """Return True if the check is in a 'fixable' state (worth blocking).

        Returns False for: passed, unfixable, exhausted, or unknown checks.
        """
        checks = self.state.get("checks", {})
        check_state = checks.get(check_name, {})
        return check_state.get("verdict") == "fixable"

    def all_resolved(self) -> bool:
        """Return True when all recorded checks are non-fixable.

        A check is "resolved" if its verdict is anything OTHER than 'fixable'.
        An empty ledger (no checks recorded) is considered resolved.
        """
        checks = self.state.get("checks", {})
        for check_state in checks.values():
            if check_state.get("verdict") == "fixable":
                return False
        return True

    def summary(self) -> str:
        """Return a human-readable summary of the ledger state."""
        checks = self.state.get("checks", {})
        global_blocks = self.state.get("global_blocks", 0)

        if not checks:
            return f"VerificationLedger: 0 checks recorded (budget: {global_blocks}/{GLOBAL_BLOCK_BUDGET})"

        lines = [
            f"VerificationLedger: {len(checks)} check(s), "
            f"global budget {global_blocks}/{GLOBAL_BLOCK_BUDGET}:"
        ]
        for name, state in sorted(checks.items()):
            verdict = state.get("verdict", "unknown")
            attempts = state.get("attempts", 0)
            icon = {
                "passed": "✅",
                "fixable": "🔄",
                "unfixable": "❌",
                "exhausted": "⏱️",
            }.get(verdict, "❓")
            lines.append(
                f"  {icon} {name}: {verdict} "
                f"(attempts: {attempts}/{MAX_ATTEMPTS_PER_CHECK})"
            )

        return "\n".join(lines)

    # ── Private Helpers ──────────────────────────────────────────────────

    def _hash_failures(self, failures: List[str]) -> str:
        """Compute a stable MD5 hash of the failure list."""
        content = "\n".join(sorted(failures))
        return hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()

    def _load(self) -> dict:
        """Load state from disk, returning defaults on any error."""
        with _ledger_lock:
            if not os.path.isfile(self._state_path):
                return self._defaults()
            try:
                with open(self._state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    logger.warning(
                        f"[VERIFICATION LEDGER] State file is not a dict, resetting"
                    )
                    return self._defaults()
                return data
            except (json.JSONDecodeError, OSError, ValueError) as e:
                logger.warning(
                    f"[VERIFICATION LEDGER] Failed to load state: {e}, resetting"
                )
                return self._defaults()

    def _save(self) -> None:
        """Persist state to disk atomically."""
        with _ledger_lock:
            docs_dir = os.path.dirname(self._state_path)
            os.makedirs(docs_dir, exist_ok=True)
            tmp_path = self._state_path + ".tmp"
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, indent=2, sort_keys=True)
                os.replace(tmp_path, self._state_path)
            except OSError as e:
                logger.error(
                    f"[VERIFICATION LEDGER] Failed to save state: {e}"
                )
                # Clean up temp file if it exists
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

    @staticmethod
    def _defaults() -> dict:
        """Return a fresh default state dict."""
        return {
            "checks": {},
            "global_blocks": 0,
        }
