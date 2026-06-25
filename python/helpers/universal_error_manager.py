"""
Universal Error Manager — Single import for all error operations.

F-ERR-2: Facade over ErrorLedger, retry_strategy, and error classification.
Every agent, every profile, same import:

    from python.helpers.universal_error_manager import UniversalErrorManager
    uem = UniversalErrorManager(self.agent)
    uem.record_tool_error("code_execution_tool", error_text, domain="nextjs")

Provides:
  - record_tool_error() with auto domain/category classification
  - record_fix_attempt() for what was tried
  - get_retry_decision() wiring retry_strategy.py (previously dead code)
  - build_delegation_error_context() for parent consumption
  - relay_subordinate_errors() for cross-delegation error relay (F-ERR-4)
"""
from __future__ import annotations

import logging
from dataclasses import field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from python.helpers.error_ledger import ErrorEntry, get_error_ledger

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger("agix.universal_error_manager")


class UniversalErrorManager:
    """Single import for all error operations across all agents.

    Usage:
        uem = UniversalErrorManager(self.agent)
        entry = uem.record_tool_error("code_execution_tool", "npm ERR!")
        decision = uem.get_retry_decision("Build failed")
        context = uem.build_delegation_error_context()
    """

    def __init__(self, agent: "Agent"):
        self.agent = agent
        self.ledger = get_error_ledger()
        self.context_id: str = (
            agent.context.id if hasattr(agent, "context") and agent.context else "unknown"
        )

    # ── Recording ─────────────────────────────────────────────────────

    def record_tool_error(
        self,
        tool_name: str,
        error_text: str,
        domain: str = "",
        severity: str = "",
    ) -> ErrorEntry:
        """Record a tool error with automatic classification.

        Returns the created ErrorEntry for chaining/inspection.
        """
        category = self._classify_category(error_text, tool_name)
        detected_domain = domain or self._detect_domain(error_text)
        suggested = self._get_alternative(tool_name, category, error_text)
        auto_severity = severity or self._severity_from_category(category)

        # Check if same error repeated
        recent = self.ledger.get_recent(self.context_id, limit=1)
        is_repeat = bool(
            recent and recent[-1].summary[:100] == error_text[:100]
        )
        occurrence = (
            getattr(recent[-1], "occurrence_count", 1) + 1
            if is_repeat and recent
            else 1
        )

        entry = ErrorEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            source="tool",
            severity=auto_severity,
            summary=error_text[:200],
            details=error_text[:500],
            tool_name=tool_name,
            five_why_hint=self._build_five_why(category, error_text),
            error_category=category,
            domain=detected_domain,
            suggested_alternative=suggested,
            occurrence_count=occurrence,
            is_same_as_previous=is_repeat,
        )
        self.ledger.record(self.context_id, entry)
        return entry

    def record_fix_attempt(self, error_summary: str, fix_description: str) -> None:
        """Record that a fix was attempted for an error.

        Persists in agent.data so it survives across tool calls and is
        available for delegation error context (F-ERR-4).
        """
        fixes: list = self.agent.data.setdefault("_attempted_fixes", [])
        fixes.append({
            "error": error_summary[:200],
            "fix": fix_description[:200],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # Keep last 20 fixes to prevent unbounded growth
        self.agent.data["_attempted_fixes"] = fixes[-20:]

    # ── Resolution ────────────────────────────────────────────────────

    # ITR-48: Maps success signal patterns to error categories they resolve.
    # When tool output matches a success pattern, all errors of the mapped
    # categories are marked resolved. The infrastructure detects both sides
    # of the error lifecycle — no LLM involvement needed.
    _SUCCESS_RESOLUTION_MAP = {
        # Build success signals
        "compiled successfully": ["build", "dependency"],
        "compiled client and server successfully": ["build", "dependency"],
        "build completed": ["build", "dependency"],
        "✓ compiled": ["build", "dependency"],
        "✓ ready": ["build"],
        "generating static pages": ["build"],
        "linting and checking validity of types": ["build"],
        "route (app)": ["build"],
        # Test success signals
        "tests passed": ["test"],
        "test suites: ": ["test"],  # "Test Suites: X passed"
        "tests:.*passed, 0 failed": ["test"],
        "✓ passed": ["test"],
        # Dependency success signals
        "added ":  ["dependency"],  # "added 42 packages"
        "up to date": ["dependency"],
        "npm warn": [],  # npm warnings are NOT success — skip
        # TypeScript success signals
        "no errors found": ["build"],  # tsc --noEmit
    }

    def resolve_errors_on_success(
        self,
        tool_name: str,
        tool_output: str,
    ) -> int:
        """Detect success signals in tool output and resolve matching errors.

        ITR-48: The same tool_execute_after hook that records errors also
        sees success output. This method closes the loop — when a build
        passes after errors, those errors are marked resolved so they
        don't leak into delegation results.

        Args:
            tool_name: The tool that produced the output.
            tool_output: The raw output from the tool.

        Returns:
            Total number of errors resolved.
        """
        if not tool_output or tool_name != "code_execution_tool":
            return 0

        output_lower = tool_output.lower()
        resolved_categories: set[str] = set()

        for signal, categories in self._SUCCESS_RESOLUTION_MAP.items():
            if signal.lower() in output_lower and categories:
                resolved_categories.update(categories)

        total_resolved = 0
        for category in resolved_categories:
            count = self.ledger.resolve_by_category(
                self.context_id, category, reason=f"{tool_name}_success"
            )
            total_resolved += count

        if total_resolved > 0:
            logger.info(
                f"[{self.agent.agent_name}] UEM: Resolved {total_resolved} errors "
                f"(categories: {sorted(resolved_categories)}) on success signal"
            )

        return total_resolved

    # ── Retry Decision ────────────────────────────────────────────────

    def get_retry_decision(self, error_text: str) -> dict:
        """Get structured retry decision with context.

        Wires the previously-dead retry_strategy.py module into the
        error management flow (F-ERR-3).
        """
        from python.helpers.retry_strategy import classify_error, get_retry_config

        category = classify_error(error_text)
        config = get_retry_config(error_text)
        attempt = self.agent.data.get("_retry_attempt_count", 0)
        can_retry = attempt < config.max_retries

        return {
            "can_retry": can_retry,
            "category": category,
            "attempt": attempt,
            "max_retries": config.max_retries,
            "should_change_approach": attempt >= 2,
            "error_text": error_text[:500],
            "attempted_fixes": self.agent.data.get("_attempted_fixes", []),
            "guidance": self._build_redirection_guidance(
                category, attempt, error_text
            ),
        }

    # ── Delegation Error Context ──────────────────────────────────────

    def build_delegation_error_context(self) -> dict:
        """Build error context to include in DelegationResult.

        Populates the previously-empty next_steps field and carries
        subordinate error details across the delegation boundary (F-ERR-4).

        ITR-48: Uses get_unresolved() instead of get_recent() so resolved
        errors don't leak into the delegation result and cause the
        orchestrator to hallucinate Recovery tasks.
        """
        # ITR-48: Only report UNRESOLVED errors to the parent.
        # Resolved errors were fixed during execution and are not actionable.
        unresolved = self.ledger.get_unresolved(self.context_id, limit=5)
        all_recent = self.ledger.get_recent(self.context_id, limit=5)
        fixes = self.agent.data.get("_attempted_fixes", [])

        recent_errors = [
            {
                "summary": e.summary,
                "category": getattr(e, "error_category", "unknown"),
                "domain": getattr(e, "domain", "unknown"),
                "count": getattr(e, "occurrence_count", 1),
                "suggested_alternative": getattr(e, "suggested_alternative", ""),
            }
            for e in unresolved
        ]

        dominant = self._get_dominant_category(unresolved)

        return {
            "recent_errors": recent_errors,
            "attempted_fixes": fixes[-10:],
            "total_errors": len(unresolved),
            "total_resolved": len(all_recent) - len(unresolved),
            "dominant_category": dominant,
            "next_steps": self._build_next_steps(dominant, unresolved, fixes),
        }

    # ── Classification Internals ──────────────────────────────────────

    def _classify_category(
        self, error_text: str, tool_name: str = ""
    ) -> str:
        """Classify error into domain-specific category."""
        t = error_text.lower()

        if any(kw in t for kw in [
            "npm run build", "next build", "webpack", "prerender",
            "build failed",
        ]):
            return "build"
        if any(kw in t for kw in [
            "test", "jest", "vitest", "expect(", "assertion", "FAIL",
        ]):
            return "test"
        if any(kw in t for kw in ["timeout", "timed out", "etimedout"]):
            return "timeout"
        if any(kw in t for kw in [
            "npm install", "eresolve", "peer dep", "dependency",
            "module not found", "cannot find module", "can't resolve",
        ]):
            return "dependency"
        if any(kw in t for kw in ["401", "403", "api key", "unauthorized"]):
            return "auth"
        if any(kw in t for kw in [
            "traceback", "import error", "syntax error", "syntaxerror",
            "typeerror", "referenceerror",
        ]):
            return "runtime"

        return "unknown"

    def _detect_domain(self, error_text: str) -> str:
        """Auto-detect the technology domain from error text."""
        t = error_text.lower()
        if any(kw in t for kw in [
            "next", "react", "jsx", "tsx", "prerender", "nextjs",
        ]):
            return "nextjs"
        if any(kw in t for kw in ["python", "traceback", "pip"]):
            return "python"
        if any(kw in t for kw in ["npm", "node_modules", "package.json"]):
            return "npm"
        if any(kw in t for kw in ["docker", "container"]):
            return "docker"
        if any(kw in t for kw in ["git", "merge conflict"]):
            return "git"
        return "unknown"

    def _severity_from_category(self, category: str) -> str:
        """Map error category to severity level."""
        severity_map = {
            "build": "high",
            "test": "medium",
            "runtime": "high",
            "auth": "critical",
            "timeout": "medium",
            "dependency": "medium",
        }
        return severity_map.get(category, "medium")

    def _get_alternative(
        self, tool_name: str, category: str, error_text: str
    ) -> str:
        """Get a suggested alternative approach for this error."""
        if tool_name == "code_execution_tool" and category == "build":
            return "Read the build error output carefully, fix the source file with replace_in_file, then re-run the build via code_execution_tool"
        if tool_name == "code_execution_tool" and category == "test":
            return "Fix the failing test file directly with write_to_file"
        if category == "timeout":
            return "Reduce scope or add a timeout flag to the command"
        if category == "auth":
            return "Check .env.local for correct API keys"
        if category == "dependency":
            return "Check package.json for version mismatches"
        return ""

    def _build_five_why(self, category: str, error_text: str) -> str:
        """Build domain-specific 5-Why guidance."""
        guidance = {
            "build": (
                "Build errors usually stem from anti-patterns (e.g., "
                "'use client' + force-dynamic) or missing dependencies. "
                "Analyze the specific error message before retrying."
            ),
            "test": (
                "Test failures indicate implementation gaps. Read the "
                "failing test to understand WHAT assertion failed, not "
                "just THAT it failed."
            ),
            "timeout": (
                "Timeouts indicate the command is too expensive. "
                "Reduce scope, add --filter, or run in chunks."
            ),
            "auth": (
                "Auth errors indicate missing or invalid credentials. "
                "Check .env.local first. Do NOT retry without fixing credentials."
            ),
            "dependency": (
                "Dependency errors are usually version conflicts. "
                "Check `npm ls` for peer dependency issues."
            ),
            "runtime": (
                "Runtime errors indicate code bugs. Read the traceback "
                "line-by-line to find the exact failure point."
            ),
        }
        return guidance.get(category, "Analyze the root cause before retrying.")

    def _build_redirection_guidance(
        self, category: str, attempt: int, error_text: str
    ) -> str:
        """Build context-specific guidance for the agent."""
        if attempt >= 2:
            return (
                f"⚠️ ERROR PERSISTED {attempt}x — you MUST change strategy. "
                f"Previous approaches failed. Try a fundamentally different method."
            )
        if category == "build":
            return "Build error detected. Read the error output, fix the source file, then re-run the build."
        if category == "timeout":
            return "Command timed out. Either reduce scope or add a timeout flag."
        if category == "auth":
            return "Authentication error. Check .env.local for correct API keys."
        if category == "dependency":
            return "Dependency conflict. Check package.json for version mismatches."
        return "Analyze the root cause before retrying. Do NOT use the same approach."

    def _build_next_steps(
        self,
        dominant_category: str,
        recent_errors: list,
        attempted_fixes: list,
    ) -> list:
        """Build suggested next steps for parent orchestrator."""
        steps: list[str] = []
        if not recent_errors:
            return steps

        if dominant_category == "build":
            steps.append(
                "Re-delegate with explicit instruction to read build error "
                "output and fix source files before re-running the build"
            )
            steps.append(
                "Consider scope-reducing: split frontend and backend "
                "into separate delegations"
            )
        elif dominant_category == "timeout":
            steps.append("Re-delegate with smaller task scope")
        elif dominant_category == "auth":
            steps.append(
                "Verify secrets are materialized before re-delegating"
            )
        elif dominant_category == "test":
            steps.append(
                "Re-delegate with instruction to fix failing tests, "
                "not add new ones"
            )

        if len(attempted_fixes) >= 3:
            steps.append(
                "Multiple fix attempts failed — consider a completely "
                "different approach"
            )

        return steps

    def _get_dominant_category(self, entries: list) -> str:
        """Find the most common error category in a list of entries."""
        if not entries:
            return "unknown"
        categories = [
            getattr(e, "error_category", "unknown") for e in entries
        ]
        return max(set(categories), key=categories.count)


# ── F-ERR-4: Cross-Delegation Error Relay ─────────────────────────────


def relay_subordinate_errors(
    parent_agent: "Agent", subordinate_agent: "Agent"
) -> None:
    """Relay subordinate's error context to parent's agent.data.

    Called in call_subordinate.py after delegation completes.
    Ensures error context survives the delegation boundary.
    """
    sub_context_id = (
        subordinate_agent.context.id
        if hasattr(subordinate_agent, "context") and subordinate_agent.context
        else None
    )
    if not sub_context_id:
        return

    ledger = get_error_ledger()
    # ITR-48: Only relay UNRESOLVED errors. Resolved errors were fixed
    # during execution and cause the orchestrator to hallucinate Recovery
    # tasks for already-resolved problems.
    sub_errors = ledger.get_unresolved(sub_context_id, limit=5)

    # Build relay payload
    relay = {
        "profile": getattr(
            subordinate_agent.config, "name", ""
        ) if hasattr(subordinate_agent, "config") else "",
        "errors": [
            {
                "summary": e.summary,
                "category": getattr(e, "error_category", "unknown"),
                "domain": getattr(e, "domain", "unknown"),
                "tool_name": e.tool_name,
                "suggested_alternative": getattr(e, "suggested_alternative", ""),
            }
            for e in sub_errors
        ],
        "attempted_fixes": subordinate_agent.data.get("_attempted_fixes", []),
    }

    # Only append if there's actual content
    if relay["errors"] or relay["attempted_fixes"]:
        history: list = parent_agent.data.setdefault(
            "_subordinate_error_history", []
        )
        history.append(relay)
        # Keep last 10 subordinate error histories
        parent_agent.data["_subordinate_error_history"] = history[-10:]

        logger.info(
            f"[ERROR RELAY] {relay['profile']}: "
            f"{len(relay['errors'])} errors + "
            f"{len(relay['attempted_fixes'])} fixes relayed to parent"
        )
