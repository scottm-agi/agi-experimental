"""
Tool Preference Nudger Extension

Extension that nudges (or blocks) agents from bypassing dedicated tools
in favor of raw shell commands.

Detects:
  - Direct context7.* MCP calls → advisory nudge (use `docs_lookup` tool)
  - Heredoc file creation in code_execution_tool → HARD BLOCK (use `write_to_file`)

NOTE: Git nudge was removed (RCA 218). `github_push` does not exist as a tool.
LLMs already know git and can use `code_execution_tool` with git commands
directly. The previous nudge caused agents to try a non-existent tool,
wasting iterations and triggering tool-blocked errors.

NOTE: node_project tool and npm lifecycle blocking were removed (P1-1 Systems
Audit). The node_project tool was deleted in favor of direct `npx create-*`
+ researcher + TDD approach. Agents now use npm/yarn/pnpm directly via
code_execution_tool.

U-13 (RCA-313): Severity-based nudge blocking.
  - "hard" severity → BLOCK the tool call (return error Response)
  - "advisory" severity → inject warning (agent self-corrects next turn)
  - Escape hatch: hard blocks lift after max attempts to prevent deadlocks.

RCA-316b: Heredoc → write_to_file enforcement.
  - Agent kept retrying code_execution_tool with heredoc for 40+ minutes
  - The heredoc ban (code_execution.py) returned guidance text but
    nothing ENFORCED tool switching — the agent just retried
  - Fix: Hard-block code_execution_tool when heredoc detected,
    force write_to_file. Same escape hatch pattern.

Priority: 22 (after mode tool filter at 20, before env validator at 23)
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from python.helpers.extension import Extension

logger = logging.getLogger("agix.tool_preference_nudger")

# Tool names that execute shell commands
CODE_EXEC_TOOLS = {"code_execution_tool", "code_execution"}

# ── U-13 (RCA-313): Severity constants ──
# "hard" → blocks the tool call (returns error), "advisory" → injects warning only
CONTEXT7_SEVERITY = "advisory"
HEREDOC_SEVERITY = "hard"  # RCA-316b: Must be hard to prevent thrash loops

# Escape hatch: after this many blocked attempts, lift the block.
HEREDOC_BLOCK_MAX_ATTEMPTS = 3  # RCA-316b: Same pattern for heredoc

# ── Context7 direct-call patterns: should use docs_lookup tool ──
# RCA-242: Agents calling context7.* directly causes retry loops because
# context7 MCP rejects invalid library ID formats. docs_lookup wraps
# context7 with a 3-layer fallback chain (Context7 → Tavily → Perplexity).
CONTEXT7_TOOL_PATTERNS = [
    re.compile(r"^context7\.", re.IGNORECASE),
]

# ── RCA-316b: Heredoc file creation patterns → should use write_to_file ──
# Agents repeatedly use `cat > file << 'EOF'` in code_execution_tool instead
# of write_to_file. The heredoc ban in code_execution.py returns guidance
# text but doesn't ENFORCE tool switching. This nudge is the enforcement.
# Threshold must match code_execution.py HEREDOC_LINE_THRESHOLD = 5.
HEREDOC_LINE_THRESHOLD = 5  # Lines below this are allowed (e.g., .env)
HEREDOC_PATTERN = re.compile(
    r"(cat|tee)\s+.*<<[-~]?\s*['\"]?(\w+)['\"]?",
    re.IGNORECASE,
)

HEREDOC_NUDGE_MESSAGE = (
    "🔴 **HEREDOC BLOCKED: Use `write_to_file` instead of heredoc (cat << EOF)**\n\n"
    "You attempted to create/overwrite a file using heredoc in code_execution_tool.\n"
    "This is BLOCKED because:\n"
    "- Heredoc causes shell escaping failures with JSX, backticks, and template literals\n"
    "- Token budget waste (file content appears in output tokens)\n"
    "- Files > 1500 lines get truncated\n\n"
    "**USE `write_to_file` INSTEAD:**\n"
    '{"tool_name": "write_to_file", "tool_args": {"path": "<file_path>", "content": "<content>"}}\n\n'
    "For files > 1500 lines, write first chunk with write_to_file, "
    "then append with replace_in_file."
)

CONTEXT7_NUDGE_MESSAGE = (
    "⚠️ **Tool Preference: Use `docs_lookup` instead of `context7.*` directly**\n\n"
    "You called a `context7.*` MCP tool directly. This causes failures because:\n"
    "- `context7.resolve-library-id` rejects non-standard library names\n"
    "- Direct calls have NO fallback when Context7 is unavailable\n"
    "- Retry loops on Context7 errors waste iteration budget and risk cancellation\n\n"
    "The `docs_lookup` tool wraps Context7 with a 3-layer fallback:\n"
    "1. Context7 (automatic library ID resolution)\n"
    "2. Tavily (scoped web search for docs)\n"
    "3. Perplexity (general research fallback)\n\n"
    "**Always use `docs_lookup` with `library` and `query` args.**\n"
    "Example: `{\"library\": \"prisma\", \"query\": \"datasource config\", \"version\": \"7.0.0\"}`"
)


class ToolPreferenceNudger(Extension):
    # Context-aware: only fire for code agents, on code execution
    PROFILES = {"code"}
    TOOLS = frozenset({"code_execution_tool", "code_execution"})

    """Severity-based tool nudger: blocks or advises based on severity level.

    RCA-316d: All state is stored in agent.data (persistent dict) instead of
    self._ instance variables. The framework creates a NEW extension instance
    on every call_extensions() invocation (extension.py:43), which destroys
    all self._ state. Using agent.data ensures cooldowns and escape hatches
    persist across the agent's full lifecycle.
    """

    # Keys used in agent.data for persistent state
    _KEY_NUDGED = "_nudger_nudged"
    _KEY_BLOCK_ATTEMPTS = "_nudger_block_attempts"

    def __init__(self, agent):
        super().__init__(agent)
        # RCA-316d: Initialize agent.data keys if not present.
        # These persist across extension instantiations.
        if self._KEY_NUDGED not in agent.data:
            agent.data[self._KEY_NUDGED] = set()
        if self._KEY_BLOCK_ATTEMPTS not in agent.data:
            agent.data[self._KEY_BLOCK_ATTEMPTS] = {}

    # ── Persistent state accessors (agent.data) ──

    @property
    def _nudged(self) -> set[str]:
        return self.agent.data[self._KEY_NUDGED]

    @property
    def _block_attempts(self) -> dict[str, int]:
        return self.agent.data[self._KEY_BLOCK_ATTEMPTS]

    async def execute(
        self,
        tool_name: str = "",
        tool_args: dict[str, Any] | None = None,
        **kwargs,
    ) -> Optional[Any]:
        """Check for raw commands that should use dedicated tools.

        Returns None to allow, or a string to block.
        """
        # Track node_project usage to suppress scaffold nudge
        if tool_name and tool_name.lower() == "node_project":
            self.agent.data["_nudger_node_project_used"] = True

        # Only check code execution tools for heredoc patterns
        if not tool_name or tool_name.lower() not in CODE_EXEC_TOOLS:
            # ── RCA-242: Check for direct context7.* MCP calls ──
            if tool_name and tool_name.lower().startswith("context7."):
                await self._check_context7_direct_call(tool_name)
            return None

        if not tool_args or not isinstance(tool_args, dict):
            return None

        code = tool_args.get("code", "")
        if not code:
            return None

        # ── RCA-227: Check for raw npx create-* scaffold calls ──
        await self._check_scaffold_direct_call(code)

        # ── RCA-316b: Check heredoc file creation ──
        heredoc_result = self._check_heredoc_file_creation(code)
        if heredoc_result is not None:
            return heredoc_result

        return None

    def _check_heredoc_file_creation(self, code: str) -> str | None:
        """RCA-316b: Detect heredoc file creation and hard-block.

        This is the Layer 2 enforcement for code_execution.py's heredoc ban
        (Layer 1 detection). When the agent uses code_execution_tool with
        heredoc to create files (≥ HEREDOC_LINE_THRESHOLD lines), this method:
        1. Hard-blocks the call and returns an error with write_to_file guidance
        2. Tracks attempts for escape hatch (HEREDOC_BLOCK_MAX_ATTEMPTS)
        3. Injects a warning into agent history for context

        Returns blocking message string if blocked, None if allowed.
        """
        if not HEREDOC_PATTERN.search(code):
            return None

        # Count lines — small heredocs (e.g., .env) are allowed
        lines = code.count("\n")
        if lines < HEREDOC_LINE_THRESHOLD:
            return None

        # Track attempts for escape hatch
        attempt = self._block_attempts.get("heredoc", 0) + 1
        self._block_attempts["heredoc"] = attempt

        if HEREDOC_SEVERITY == "hard" and attempt <= HEREDOC_BLOCK_MAX_ATTEMPTS:
            logger.warning(
                f"[TOOL BLOCK] {self.agent.agent_name}: "
                f"Heredoc file creation blocked (attempt {attempt}/"
                f"{HEREDOC_BLOCK_MAX_ATTEMPTS}). "
                f"Detected {lines} lines via heredoc. Use write_to_file."
            )
            return (
                f"🔴 HEREDOC BLOCKED (attempt {attempt}/"
                f"{HEREDOC_BLOCK_MAX_ATTEMPTS}): "
                f"Do NOT use heredoc (cat << EOF) for file creation. "
                f"Detected {lines} lines via heredoc.\n\n"
                f"USE the `write_to_file` tool:\n"
                f'{{"tool_name": "write_to_file", "tool_args": '
                f'{{"path": "<file_path>", "content": "<file_content>"}}}}\n\n'
                f"Heredoc causes shell escaping failures with JSX, backticks, "
                f"and template literals. This block lifts after "
                f"{HEREDOC_BLOCK_MAX_ATTEMPTS} attempts."
            )

        if attempt > HEREDOC_BLOCK_MAX_ATTEMPTS:
            logger.warning(
                f"[TOOL NUDGE ESCAPE] {self.agent.agent_name}: "
                f"Heredoc escape hatch triggered after {attempt} attempts — "
                f"allowing heredoc through."
            )
        return None  # Escape hatch or advisory — allow through

    async def _check_context7_direct_call(self, tool_name: str) -> bool:
        """RCA-242: Detect direct context7.* tool calls and nudge to docs_lookup.

        Returns True if a nudge was injected, False otherwise.
        """
        if "context7" in self._nudged:  # Uses persistent agent.data via property
            return False

        for pattern in CONTEXT7_TOOL_PATTERNS:
            if pattern.search(tool_name):
                self._nudged.add("context7")
                logger.info(
                    f"[TOOL NUDGE] {self.agent.agent_name}: "
                    f"Direct context7 call detected ({tool_name}), nudging to docs_lookup"
                )
                await self.agent.hist_add_warning(message=CONTEXT7_NUDGE_MESSAGE)
                return True
        return False

    async def _check_scaffold_direct_call(self, code: str) -> bool:
        """RCA-227: Check for raw npx create-* scaffold calls.

        If the agent attempts to run a scaffolding command without using
        the dedicated node_project tool, injects an advisory warning.
        """
        if self.agent.data.get("_nudger_node_project_used"):
            return False

        if "node_project" in self._nudged:
            return False

        import re
        scaffold_pattern = re.compile(
            r"\b(?:create-next-app|create-vite|create-react-app|create-t3-app)\b|npm\s+init\s+next-app",
            re.IGNORECASE,
        )
        if scaffold_pattern.search(code):
            self._nudged.add("node_project")
            logger.info(
                f"[TOOL NUDGE] {self.agent.agent_name}: "
                f"Raw scaffold command detected, nudging to node_project"
            )
            warning_msg = (
                "⚠️ **Tool Preference: Use node_project instead of npx create-* directly**\n\n"
                "You attempted to run a scaffolding command directly. Please use the dedicated `node_project` tool."
            )
            await self.agent.hist_add_warning(message=warning_msg)
            return True
        return False

# Backward-compat alias: tests expect SCAFFOLD_BLOCK_MAX_ATTEMPTS
# (scaffold/node_project blocking was removed per P1-1 Systems Audit;
#  the constant is aliased to HEREDOC_BLOCK_MAX_ATTEMPTS for test compatibility)
SCAFFOLD_BLOCK_MAX_ATTEMPTS = HEREDOC_BLOCK_MAX_ATTEMPTS
