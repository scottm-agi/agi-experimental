"""
TDD Progress Hook — tool_execute_after extension (RC-3).

Fires AFTER code_execution_tool to detect test run commands (pytest, jest,
npm test, etc.) and feed the output to TDDProgressTracker. When the tracker
detects a spiral (3 identical test results in a row), the hook:
  1. Logs a warning about spiral detection
  2. Consumes OperationType.TDD_PROGRESS budget via RetryBudgetManager
  3. Sets agent.data['_tdd_spiral_detected'] = True when budget exhausted

This WIRES the previously dead-code TDDProgressTracker (D-1) and
OperationType.TDD_PROGRESS (retry_budget.py) into the live extension
pipeline.

Hooks into: tool_execute_after (order 25 — before build retry gate)
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from python.helpers.extension import Extension
from python.helpers.tdd_progress_tracker import TDDProgressTracker

logger = logging.getLogger("agix.tdd_progress_hook")

# Tool names that count as code execution
_CODE_EXEC_TOOLS = {"code_execution_tool", "code_execution"}

# Patterns in the command string that indicate a test run
_TEST_CMD_PATTERNS = [
    re.compile(r"\bnpm\s+test\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+run\s+test\b", re.IGNORECASE),
    re.compile(r"\byarn\s+test\b", re.IGNORECASE),
    re.compile(r"\bpnpm\s+test\b", re.IGNORECASE),
    re.compile(r"\bbun\s+test\b", re.IGNORECASE),
    re.compile(r"\bjest\b", re.IGNORECASE),
    re.compile(r"\bvitest\b", re.IGNORECASE),
    re.compile(r"\bpytest\b", re.IGNORECASE),
    re.compile(r"\bpython\s+-m\s+pytest\b", re.IGNORECASE),
    re.compile(r"\bpython3\s+-m\s+pytest\b", re.IGNORECASE),
    re.compile(r"\bgo\s+test\b", re.IGNORECASE),
    re.compile(r"\bcargo\s+test\b", re.IGNORECASE),
    re.compile(r"\brspec\b", re.IGNORECASE),
    re.compile(r"\bphpunit\b", re.IGNORECASE),
]

# Patterns in the OUTPUT that indicate test results (fallback check)
_TEST_OUTPUT_PATTERNS = [
    re.compile(r"\bTests?:\s*\d+", re.IGNORECASE),
    re.compile(r"\bPASS(?:ED)?\b"),
    re.compile(r"\bFAIL(?:ED)?\b"),
    re.compile(r"\d+\s+passed", re.IGNORECASE),
    re.compile(r"\d+\s+failed", re.IGNORECASE),
]


def _is_test_command(code: str) -> bool:
    """Check if the executed code contains a test-running command."""
    return any(p.search(code) for p in _TEST_CMD_PATTERNS)


def _output_looks_like_tests(output: str) -> bool:
    """Check if the output contains test result patterns."""
    return any(p.search(output) for p in _TEST_OUTPUT_PATTERNS)


class TDDProgressHook(Extension):
    # Context-aware: code agents only, code execution
    PROFILES = {"code"}
    TOOLS = frozenset({"code_execution_tool", "code_execution"})

    """Track TDD test run progress and detect spirals.

    RC-3: Wires TDDProgressTracker into the extension pipeline.

    On each code_execution_tool invocation that runs tests:
      1. Gets or creates TDDProgressTracker on agent.data['_tdd_tracker']
      2. Calls tracker.record_test_run(output)
      3. If tracker.is_making_progress() returns False:
         a. Logs a warning
         b. Consumes TDD_PROGRESS budget
         c. If budget exhausted → sets _tdd_spiral_detected = True
    """

    async def execute(self, tool_name: str = "", response: Any = None, **kwargs):
        """Execute the TDD progress hook after tool execution.

        Args:
            tool_name: Name of the tool that was executed.
            response: The tool response object (has .message attribute).
            **kwargs: Additional arguments, including tool_args.
        """
        if not tool_name or response is None:
            return

        # Only act on code execution tools
        if tool_name.lower() not in _CODE_EXEC_TOOLS:
            return

        # Extract the command from tool_args
        tool_args = kwargs.get("tool_args", {})
        if not tool_args or not isinstance(tool_args, dict):
            return

        code = tool_args.get("code", "")
        if not code:
            return

        # Check if this is a test command
        if not _is_test_command(code):
            return

        # Extract the output
        output = ""
        if hasattr(response, "message") and response.message:
            output = str(response.message)

        if not output:
            return

        # Verify the output looks like test results (defense in depth)
        if not _output_looks_like_tests(output):
            return

        # Get or create the tracker
        tracker = self.agent.data.get("_tdd_tracker")
        if tracker is None:
            tracker = TDDProgressTracker()
            self.agent.data["_tdd_tracker"] = tracker

        # Record the test run
        tracker.record_test_run(output)

        agent_name = getattr(self.agent, "agent_name", "agent")

        # Check if stuck (only meaningful after 3+ runs)
        if tracker.run_count >= 3 and not tracker.is_making_progress():
            logger.warning(
                "[TDD PROGRESS HOOK] %s: Spiral detected — last 3 test runs "
                "show identical results (run #%d)",
                agent_name,
                tracker.run_count,
            )

            # Consume TDD_PROGRESS budget
            self._consume_tdd_budget()

        # Fix E: After test run, validate test content against BDD scenarios
        self._validate_test_files_after_run(code)

    def _consume_tdd_budget(self) -> None:
        """Consume one TDD_PROGRESS retry from the budget manager.

        If the budget manager doesn't exist yet, creates one.
        If budget exhausted, sets agent.data['_tdd_spiral_detected'] = True.
        """
        try:
            from python.helpers.retry_budget import (
                OperationType,
                RetryBudgetManager,
            )

            budget_mgr = self.agent.data.get("_retry_budget")
            if budget_mgr is None:
                budget_mgr = RetryBudgetManager()
                self.agent.data["_retry_budget"] = budget_mgr

            decision = budget_mgr.record_failure(
                OperationType.TDD_PROGRESS,
                context="TDD spiral detected — test results not improving",
            )

            agent_name = getattr(self.agent, "agent_name", "agent")

            if decision.action in ("escalate", "force_complete", "terminal"):
                self.agent.data["_tdd_spiral_detected"] = True
                logger.warning(
                    "[TDD PROGRESS HOOK] %s: TDD_PROGRESS budget exhausted "
                    "(action=%s) — _tdd_spiral_detected set to True",
                    agent_name,
                    decision.action,
                )
            else:
                logger.info(
                    "[TDD PROGRESS HOOK] %s: TDD_PROGRESS budget consumed "
                    "(%s)",
                    agent_name,
                    decision.message,
                )

        except Exception as e:
            logger.error(
                "[TDD PROGRESS HOOK] Failed to consume TDD_PROGRESS budget: %s",
                e,
            )

    def _validate_test_files_after_run(self, code: str) -> None:
        """After a test run, check if test files reference REQ-IDs and
        validate them against BDD THEN clauses using embedding similarity.

        Fix E: Scans for test file paths in the executed code, reads each
        test file, extracts REQ-IDs, and compares content against BDD THEN
        clauses. Sets agent.data['_tdd_bdd_mismatch_warning'] if similarity
        is below threshold (0.55).
        """
        try:
            project_dir = self.agent.data.get("project_dir", "")
            if not project_dir:
                return

            # Find test file paths referenced in the command
            # Common patterns: pytest path/test_x.py, jest __tests__/x.test.ts
            test_file_patterns = re.findall(
                r'[\w./\-]+(?:test_[\w]+\.py|[\w]+\.test\.[tj]sx?|[\w]+\.spec\.[tj]sx?)',
                code
            )

            # Also look for test files in the project test dirs
            test_dirs = [
                os.path.join(project_dir, "src", "__tests__"),
                os.path.join(project_dir, "tests"),
                os.path.join(project_dir, "test"),
                os.path.join(project_dir, "__tests__"),
            ]

            test_files = []
            for td in test_dirs:
                if os.path.isdir(td):
                    for root, _dirs, files in os.walk(td):
                        for fname in files:
                            if re.search(r'\.(test|spec)\.[tj]sx?$', fname) or fname.startswith('test_'):
                                test_files.append(os.path.join(root, fname))

            # Also resolve any paths from the command
            for pat in test_file_patterns:
                full_path = os.path.join(project_dir, pat)
                if os.path.isfile(full_path) and full_path not in test_files:
                    test_files.append(full_path)

            for tf in test_files:
                self._validate_test_content_against_bdd(tf)

        except Exception as e:
            logger.debug("[TDD PROGRESS HOOK] BDD validation scan failed: %s", e)

    def _validate_test_content_against_bdd(self, test_file_path: str) -> None:
        """Validate a single test file's content against BDD THEN clauses.

        Fix E: Extracts REQ-IDs from the test file, loads BDD scenarios for
        those REQ-IDs, computes embedding similarity between the test content
        and the BDD THEN clauses. If similarity < 0.55, sets
        agent.data['_tdd_bdd_mismatch_warning'] with the specific THEN clause.

        Args:
            test_file_path: Absolute path to the test file.
        """
        try:
            if not os.path.isfile(test_file_path):
                return

            with open(test_file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # Extract REQ-IDs from the test file
            req_ids = re.findall(r'REQ-\d+', content)
            if not req_ids:
                return

            # Load BDD scenarios
            project_dir = self.agent.data.get("project_dir", "")
            if not project_dir:
                return

            from python.helpers.tdd_generator_helpers import _load_bdd_scenarios
            bdd_map = _load_bdd_scenarios(project_dir)
            if not bdd_map:
                return

            # Import embedding function
            from python.helpers.semantic_embeddings import compute_embedding_sync

            similarity_threshold = 0.55

            for req_id in set(req_ids):
                scenarios = bdd_map.get(req_id, [])
                if not scenarios:
                    continue

                # Compute embedding for the test file content
                test_embedding = compute_embedding_sync(content)
                if test_embedding is None:
                    continue

                for scenario in scenarios:
                    then_clauses = scenario.get("then", [])
                    for then_clause in then_clauses:
                        then_embedding = compute_embedding_sync(then_clause)
                        if then_embedding is None:
                            continue

                        # Compute cosine similarity (embeddings are normalized)
                        similarity = float(test_embedding @ then_embedding)

                        if similarity < similarity_threshold:
                            agent_name = getattr(self.agent, "agent_name", "agent")
                            logger.warning(
                                "[TDD PROGRESS HOOK] %s: Low BDD similarity "
                                "(%.3f < %.2f) for %s against THEN: '%s'",
                                agent_name,
                                similarity,
                                similarity_threshold,
                                os.path.basename(test_file_path),
                                then_clause[:80],
                            )
                            self.agent.data["_tdd_bdd_mismatch_warning"] = {
                                "test_file": test_file_path,
                                "req_id": req_id,
                                "then_clause": then_clause,
                                "similarity": similarity,
                            }
                            return  # Report first mismatch only

        except Exception as e:
            logger.debug(
                "[TDD PROGRESS HOOK] BDD content validation failed: %s", e
            )
