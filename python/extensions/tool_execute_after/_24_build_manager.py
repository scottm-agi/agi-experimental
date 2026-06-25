"""
Unified Build Manager — tool_execute_after extension.

Merges 3 previously-separate build extensions:
- _24_build_loop_hook: build loop detection + L2 escalation + hard stop
- _30_build_retry_gate: retry counting + STOP after 5 + cold build advisory
- _40_build_pass_gate: delivery-time build verification gate

All share build command detection patterns, failure patterns, and agent.data
state. Consolidating eliminates 18 duplicate regex patterns and 3 separate
passes over the same tool output.

Architecture:
- After code_execution_tool: tracks build attempts, detects loops, injects STOP
- After response tool: blocks delivery if build hasn't passed
- Shared patterns: BUILD_CMD_PATTERNS, BUILD_FAILURE_PATTERNS, SUCCESS_PATTERNS
- Uses BuildLoopDetector helper for escalation tiers

Hooks into: tool_execute_after (order 24)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from python.helpers.extension import Extension
from python.helpers.build_loop_detector import BuildLoopDetector

logger = logging.getLogger("agix.build_manager")

# Context-aware: code agents + orchestrator, code_execution + response
# (orchestrator needs response gate for delivery blocking)

# ═══════════════════════════════════════════════════════════════════════
# Shared Patterns (were duplicated across _24, _30, _35)
# ═══════════════════════════════════════════════════════════════════════

BUILD_CMD_PATTERNS = [
    re.compile(r"\bnpm\s+run\s+build\b", re.IGNORECASE),
    re.compile(r"\bnpx\s+(?:next|vite|nuxt|remix)\s+build\b", re.IGNORECASE),
    re.compile(r"\bnext\s+build\b", re.IGNORECASE),
    re.compile(r"\byarn\s+build\b", re.IGNORECASE),
    re.compile(r"\bpnpm\s+build\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+run\s+lint\b", re.IGNORECASE),
    re.compile(r"\btsc\b.*--noEmit", re.IGNORECASE),
    re.compile(r"\bnpm\s+run\s+export\b", re.IGNORECASE),
    # Test commands — detect test-fix death spirals too
    re.compile(r"\bnpm\s+(?:run\s+)?test\b", re.IGNORECASE),
    re.compile(r"\bnpx\s+(?:jest|vitest)\b", re.IGNORECASE),
    re.compile(r"\bvitest\s+run\b", re.IGNORECASE),
    re.compile(r"\byarn\s+test\b", re.IGNORECASE),
    re.compile(r"\bpnpm\s+test\b", re.IGNORECASE),
]

BUILD_FAILURE_PATTERNS = [
    re.compile(r"Build failed", re.IGNORECASE),
    re.compile(r"Build error", re.IGNORECASE),
    re.compile(r"Failed to compile", re.IGNORECASE),
    re.compile(r"exit code [1-9]", re.IGNORECASE),
    re.compile(r"ELIFECYCLE", re.IGNORECASE),
    re.compile(r"Type error:", re.IGNORECASE),
    re.compile(r"Module not found", re.IGNORECASE),
    re.compile(r"TS\d{4,}:", re.IGNORECASE),
    re.compile(r"Unhandled Runtime Error", re.IGNORECASE),
    re.compile(r"Hydration failed", re.IGNORECASE),
    re.compile(r"Error:\s+Cannot read properties", re.IGNORECASE),
]

SUCCESS_PATTERNS = [
    re.compile(r"Compiled successfully", re.IGNORECASE),
    re.compile(r"✓\s*Compiled", re.IGNORECASE),
    re.compile(r"Build completed", re.IGNORECASE),
    re.compile(r"Successfully compiled", re.IGNORECASE),
    re.compile(r"Route \(app\)", re.IGNORECASE),
    re.compile(r"built in \d+", re.IGNORECASE),
    re.compile(r"webpack compiled successfully", re.IGNORECASE),
]

# SSG patterns that indicate a cold build may be needed
COLD_BUILD_PATTERNS = [
    re.compile(r"getStaticProps", re.IGNORECASE),
    re.compile(r"generateStaticParams", re.IGNORECASE),
    re.compile(r"Export encountered errors", re.IGNORECASE),
    re.compile(r"revalidate.*(?:option|ISR|not\s+enabled)", re.IGNORECASE),
    re.compile(r"Static\s+generation\s+failed", re.IGNORECASE),
    re.compile(r"Error occurred prerendering page", re.IGNORECASE),
    re.compile(r"getStaticPaths", re.IGNORECASE),
]

# Source file extensions for write-after-build counter reduction
SOURCE_EXTENSIONS = {
    ".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs",
    ".css", ".scss", ".sass", ".less",
    ".json", ".html", ".vue", ".svelte", ".astro",
}

# Build output directories that indicate a successful build
BUILD_OUTPUT_DIRS = [".next", "dist", "build", ".output", ".nuxt", "out"]

CODE_EXEC_TOOLS = {"code_execution_tool", "code_execution", "services_mgt"}
MAX_BUILD_ATTEMPTS = 5
MAX_DELIVERY_BLOCKS = 2

# Singleton detector instance
_global_detector = BuildLoopDetector(threshold=2)


def is_build_command(command: str) -> bool:
    """Check if a command string contains a build command."""
    if not command or not command.strip():
        return False
    return any(p.search(command) for p in BUILD_CMD_PATTERNS)


def is_build_failure(output: str) -> bool:
    """Check if the output indicates a build failure."""
    return any(p.search(output) for p in BUILD_FAILURE_PATTERNS)


def is_success_output(output: str) -> bool:
    """Check if build output contains success patterns."""
    return any(p.search(output) for p in SUCCESS_PATTERNS)


def _extract_project_dir(code: str) -> str:
    """Extract project directory from command context."""
    match = re.match(r"^cd\s+(/\S+)\s*(?:&&|;)", code)
    return match.group(1) if match else ""


class BuildManager(Extension):
    """Unified build lifecycle manager.

    Handles:
    1. Build loop detection (from _24) — escalation tiers, L2 signals, hard stop
    2. Build retry tracking (from _30) — counter, STOP after 5, cold build advisory
    3. Delivery gate (from _40) — blocks response if build not verified
    """

    # Context-aware: code agents for build tracking, orchestrator for delivery gate
    PROFILES = {"code", "multiagentdev", "alex", "default"}
    TOOLS = frozenset({"code_execution_tool", "code_execution", "services_mgt", "response"})

    async def execute(self, tool_name: str = "", response: Any = None, **kwargs):
        if not tool_name or response is None:
            return

        tool_lower = tool_name.lower()

        # ── Route: code_execution → build tracking ──
        if tool_lower in CODE_EXEC_TOOLS:
            await self._handle_build_execution(tool_lower, response, **kwargs)

        # ── Route: response → delivery gate ──
        elif tool_lower == "response":
            self._handle_delivery_gate(response)

    # ═══════════════════════════════════════════════════════════════════
    # Build Execution Tracking (merged from _24 + _30)
    # ═══════════════════════════════════════════════════════════════════

    async def _handle_build_execution(self, tool_name: str, response: Any, **kwargs):
        """Track build attempts, detect loops, inject STOP if needed."""
        tool_args = kwargs.get("tool_args", {})
        if not tool_args or not isinstance(tool_args, dict):
            return

        code = tool_args.get("code", "")
        if not code or not is_build_command(code):
            return

        # §12 FIX-014: TDD mode suspension
        tdd_state = self.agent.data.get("_tdd_cycle_state")
        if tdd_state and isinstance(tdd_state, dict):
            if tdd_state.get("phase") not in ("IDLE", "COMPLETE", None):
                logger.info("[BUILD MANAGER] TDD cycle active — suspending build loop tracking.")
                return

        # Extract output
        msg = ""
        if hasattr(response, "message") and response.message:
            msg = str(response.message)
        elif isinstance(response, str):
            msg = response
        if not msg:
            return

        # Determine project directory
        project_dir = _extract_project_dir(code)
        if not project_dir:
            project_dir = getattr(self.agent, "project_dir", "") or "unknown"

        # ── System 1: Build Loop Detection (from _24) ──
        diagnostic = _global_detector.record_failure_from_output(
            project_dir=project_dir,
            output=msg,
            exit_code=1 if is_build_failure(msg) else 0,
        )

        # Shadow-write to RetryBudgetManager
        from python.helpers.retry_budget_bridge import shadow_build_failure_event
        if diagnostic:
            shadow_build_failure_event(
                self.agent.data, project_dir=project_dir,
                error_snippet=msg[:200],
                old_decision_would_stop=True,
            )

        if diagnostic:
            # L2 escalation signals
            tier = _global_detector.get_escalation_tier(project_dir)
            failure_count = _global_detector.get_failure_count(project_dir)

            if tier >= 2:
                severity = "critical" if tier >= 3 else "warning"
                l2_signal = {
                    "source": "build_manager",
                    "detector": "build_loop",
                    "severity": severity,
                    "detail": (
                        f"{failure_count} consecutive build failures in "
                        f"{project_dir} (tier {tier})"
                    ),
                }
                if "_l2_escalation_signals" not in self.agent.data:
                    self.agent.data["_l2_escalation_signals"] = []
                self.agent.data["_l2_escalation_signals"].append(l2_signal)

            # Tier 3 hard stop
            if tier >= 3:
                self.agent.loop_data.is_done = True
                logger.error(
                    f"[BUILD MANAGER] HARD STOP — {failure_count} consecutive "
                    f"build failures in {project_dir}."
                )
                # A6/RCA-475: Wire mark_failed for requirements linked to this loop.
                # At Tier 3, the build is structurally stuck — requirements
                # associated with these failures should be marked failed.
                try:
                    from python.helpers.requirements_delegation_tracker import mark_failed
                    looped_reqs = _global_detector.get_looped_requirement_ids(project_dir)
                    for req_id in looped_reqs:
                        mark_failed(
                            self.agent.data,
                            req_id,
                            reason=f"Build loop Tier 3 hard stop: {failure_count} "
                                   f"consecutive failures in {project_dir}",
                        )
                    if looped_reqs:
                        logger.warning(
                            f"[BUILD MANAGER] A6: Marked {len(looped_reqs)} "
                            f"requirements as FAILED (build loop Tier 3): "
                            f"{', '.join(sorted(looped_reqs)[:5])}"
                        )
                except Exception as e:
                    logger.debug(f"[BUILD MANAGER] A6 mark_failed wiring failed: {e}")

            await self.agent.hist_add_warning(diagnostic)

        elif not _global_detector.detect_failure_in_output(msg):
            # Build succeeded — reset
            from python.helpers.retry_budget_bridge import shadow_build_success_event
            shadow_build_success_event(self.agent.data, project_dir=project_dir)
            self.agent.data["_build_pass_verified"] = True

        # ── System 2: Retry Counter (from _30) ──
        self._track_retry(code, msg, response)

    def _track_retry(self, code: str, output: str, response: Any):
        """Track build retry count, inject STOP after limit, cold build advisory."""
        # Increment counter
        count = self.agent.data.get("_build_attempt_count", 0) + 1
        self.agent.data["_build_attempt_count"] = count
        self.agent.data["_last_build_ts"] = time.time()

        logger.info(f"[BUILD MANAGER] Build attempt #{count}: {code[:80]}")

        # Record in UEM if available
        if count >= 2:
            try:
                from python.helpers.universal_error_manager import UniversalErrorManager
                uem = UniversalErrorManager()
                context_id = (
                    self.agent.context.id
                    if hasattr(self.agent, "context") and self.agent.context
                    else "unknown"
                )
                uem.record_error(
                    context_id=context_id,
                    tool_name="code_execution_tool",
                    error_text=f"Build attempt #{count}: {code[:200]}",
                    attempted_fixes=self.agent.data.get("_attempted_fixes", []),
                )
            except Exception as e:
                logger.debug(f"UEM recording failed (non-critical): {e}")

        # Check for success → reset counter
        if is_success_output(output):
            old_count = self.agent.data.get("_build_attempt_count", 0)
            if old_count > 0:
                self.agent.data["_build_attempt_count"] = 0
                logger.info(f"[BUILD MANAGER] Build succeeded — counter reset from {old_count} to 0")

        # Cold build advisory (once per lifetime)
        if (
            output
            and not self.agent.data.get("_cold_build_suggested")
            and is_success_output(output)
            and any(p.search(output) for p in COLD_BUILD_PATTERNS)
        ):
            self.agent.data["_cold_build_suggested"] = True
            cold_msg = (
                "\n\n⚠️ **COLD BUILD ADVISORY** ⚠️\n\n"
                "The warm build succeeded, but SSG/ISR-related patterns were detected. "
                "These errors may only appear on a **cold build** (without cached .next artifacts).\n\n"
                "**Recommended**: `rm -rf .next && npm run build`\n"
            )
            if hasattr(response, "message") and response.message is not None:
                response.message = str(response.message) + cold_msg

        # Over limit → inject STOP
        if count >= MAX_BUILD_ATTEMPTS:
            stop_msg = (
                f"\n\n🛑 **STOP — BUILD RETRY LIMIT EXCEEDED** 🛑\n\n"
                f"You have attempted `npm run build` {count} times. "
                "This indicates a fundamental issue that retrying will NOT fix.\n\n"
                "**MANDATORY ACTIONS:**\n"
                "1. STOP running build commands immediately\n"
                "2. Read the FULL error output — identify the ROOT CAUSE\n"
                "3. Fix the root cause in the source files BEFORE retrying\n"
                "4. If the error persists after 2 different fix attempts, "
                "escalate to your orchestrator\n\n"
                "DO NOT run `npm run build` again until you have made a "
                "meaningful code change that addresses the root cause."
            )
            if hasattr(response, "message") and response.message is not None:
                response.message = str(response.message) + stop_msg

    # ═══════════════════════════════════════════════════════════════════
    # Delivery Gate (merged from _40)
    # ═══════════════════════════════════════════════════════════════════

    def _handle_delivery_gate(self, response):
        """Block delivery if build hasn't been verified."""
        from python.helpers.profile_registry import is_orchestrator
        if not is_orchestrator(self.agent.agent_name):
            return

        project_dir = self.agent.data.get("_active_project_dir", "")
        if not project_dir:
            return

        # Already verified
        if self.agent.data.get("_build_verified"):
            return

        # Check if web project with build script
        pkg_json_path = os.path.join(project_dir, "package.json")
        if not os.path.isfile(pkg_json_path):
            return
        try:
            with open(pkg_json_path, "r", encoding="utf-8") as f:
                pkg = json.load(f)
        except (json.JSONDecodeError, IOError):
            return
        scripts = pkg.get("scripts", {})
        if "build" not in scripts:
            return

        # Check build evidence
        evidence_path = os.path.join(
            project_dir, ".agix.proj", "verification", "build_evidence.json"
        )
        if os.path.isfile(evidence_path):
            try:
                with open(evidence_path, "r") as f:
                    evidence = json.load(f)
                if evidence.get("passed"):
                    return  # Build evidence confirms pass
            except (json.JSONDecodeError, IOError):
                pass

        # Circuit breaker
        from python.helpers.universal_gate_budget import gate_check, get_block_count
        if gate_check(self.agent.data, "build_pass_gate", threshold=MAX_DELIVERY_BLOCKS):
            logger.warning("[BUILD MANAGER] Delivery gate circuit breaker fired.")
            response.message += (
                "\n\n⚠️ **ADVISORY**: Build verification was not confirmed. "
                "The project may have build errors. Run `npm run build` to verify."
            )
            return

        # Block delivery
        build_cmd = scripts.get("build", "npm run build")
        gate_msg = (
            f"## 🚫 BUILD PASS GATE — DELIVERY BLOCKED\n\n"
            f"The project build has **not been verified**. Before delivery, "
            f"the build must pass.\n\n"
            f"**Required action**: Run `{build_cmd}` and fix any build errors.\n\n"
            f"Block {get_block_count(self.agent.data, 'build_pass_gate')}/{MAX_DELIVERY_BLOCKS} "
            f"— circuit breaker will allow delivery after {MAX_DELIVERY_BLOCKS} blocks."
        )
        original_msg = response.message or ""
        response.message = gate_msg + "\n\n---\n\n" + original_msg
        response.break_loop = False
