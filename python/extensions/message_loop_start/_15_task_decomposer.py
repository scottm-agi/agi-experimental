"""
Task Decomposer Extension (PFR #827)
Detects complex tasks and injects pre-planning guidance into the system prompt.
Fires only on iteration 0 for top-level agents (number == 0).

Priority: 15 (after guardrails at 05)
"""
import json
import os
import re
import sys

from python.helpers.task_list import (
    format_verification_guidance,
    format_lit_guidance,
    format_prerequisites_guidance,
    format_testability_audit,
    format_structured_plan_guidance,
)

from python.helpers.extension import Extension

COMPLEXITY_THRESHOLD = 3

print("[TaskDecomposer] Extension loaded", file=sys.stderr, flush=True)


# Agent profile catalog — describes all available profiles for routing decisions
AGENT_PROFILE_CATALOG = {
    # --- Orchestrators (can delegate and coordinate) ---
    "multiagentdev": {
        "type": "orchestrator",
        "description": "Development orchestrator with modes: code, architect, ask, debug, review. Coordinates software engineering tasks across multiple subordinates.",
        "best_for": ["code implementation", "architecture design", "debugging", "code review", "multi-step dev tasks"],
    },
    "alex": {
        "type": "orchestrator",
        "description": "Sales & marketing orchestrator. Coordinates account-leader, marketing-lead, sales-enabler, content-writer, and researcher for business deliverables.",
        "best_for": ["sales strategy", "marketing campaigns", "content creation", "business analysis", "market research"],
    },
    # --- Specialists (do focused work) ---
    "researcher": {
        "type": "specialist",
        "description": "Data research & analysis specialist. Handles bulk non-code data tasks: API crawling, web research, document analysis. Produces structured content deliverables (reports, strategy docs, competitive analyses) via save_deliverable — this is the researcher's ONLY write mechanism. Needs pre-broken tasks — give it one clear research objective at a time.",
        "best_for": ["API crawling", "web research", "data gathering", "Google Chat", "Forgejo/GitHub scanning", "Perplexity/Tavily search", "content deliverables", "research reports", "structured analysis"],
    },
    "browser": {
        "type": "specialist",
        "description": "Web browser automation specialist. Navigates websites, fills forms, extracts dynamic content.",
        "best_for": ["web scraping", "form submission", "UI testing", "screenshot capture"],
    },
    "frontend": {
        "type": "specialist",
        "description": "Frontend development specialist. HTML, CSS, JavaScript, React, Next.js.",
        "best_for": ["UI implementation", "frontend bugs", "styling", "responsive design"],
    },
    "architect": {
        "type": "specialist",
        "description": "Design and planning focus. Read-only code access with docs editing. Plans before coding.",
        "best_for": ["system design", "architecture planning", "technical docs", "RFC drafting"],
    },
    "code": {
        "type": "specialist",
        "description": "Full development capabilities. Write, modify, test, and run code.",
        "best_for": ["code implementation", "bug fixes", "refactoring", "testing"],
    },
    "debug": {
        "type": "specialist",
        "description": "Troubleshooting specialist. Diagnosis-first approach before fixes.",
        "best_for": ["bug diagnosis", "log analysis", "performance profiling", "error investigation"],
    },
    "review": {
        "type": "specialist",
        "description": "Code review specialist. Provides feedback on code quality, security, performance.",
        "best_for": ["code review", "security audit", "best practices", "PR review"],
    },
    "security_auditor": {
        "type": "specialist",
        "description": "Security-focused auditor. Vulnerability scanning and security analysis.",
        "best_for": ["security audits", "vulnerability scanning", "penetration testing", "compliance"],
    },
    "mcp_builder": {
        "type": "specialist",
        "description": "MCP server development specialist. Builds and maintains Model Context Protocol servers.",
        "best_for": ["MCP server creation", "tool integration", "protocol implementation"],
    },
    "hacker": {
        "type": "specialist",
        "description": "Creative problem solver. Unconventional approaches to technical challenges.",
        "best_for": ["creative solutions", "workarounds", "rapid prototyping"],
    },
    "content-writer": {
        "type": "specialist",
        "description": "Content creation specialist. Blog posts, documentation, marketing copy.",
        "best_for": ["blog posts", "documentation", "marketing copy", "technical writing"],
    },
    "account-leader": {
        "type": "specialist",
        "description": "Account management specialist. Client strategy and relationship management.",
        "best_for": ["account strategy", "client management", "business development"],
    },
    "marketing-lead": {
        "type": "specialist",
        "description": "Marketing strategy specialist. Campaigns, analytics, growth.",
        "best_for": ["marketing strategy", "campaigns", "SEO", "growth"],
    },
    "sales-enabler": {
        "type": "specialist",
        "description": "Sales enablement specialist. Collateral, demos, competitive analysis.",
        "best_for": ["sales collateral", "demo prep", "competitive analysis", "pricing"],
    },
    "dashboard": {
        "type": "specialist",
        "description": "Dashboard and reporting specialist. Data visualization and metrics.",
        "best_for": ["dashboard creation", "data visualization", "reporting"],
    },
    "ask": {
        "type": "specialist",
        "description": "Q&A mode. Read-only, minimal tools. Quick answers and information lookup.",
        "best_for": ["quick questions", "information lookup", "explanations"],
    },
}


class TaskDecomposer(Extension):
    """Detect complex tasks and inject pre-planning guidance."""

    async def execute(self, loop_data=None, **kwargs):
        if loop_data is None:
            loop_data = kwargs.get("loop_data")
        if loop_data is None:
            return

        # Only fire on first iteration
        iter_no = self.agent.get_data("iteration_no") or 0
        if iter_no > 1:
            return loop_data

        # Only for top-level agents (number == 0)
        if self.agent.number != 0:
            return loop_data

        # Only fire once per context — don't re-inject on subsequent user messages
        if self.agent.get_data("_task_decomposer_fired"):
            return loop_data

        # Get user's message
        user_msg = self._get_user_message(loop_data)
        if not user_msg:
            return loop_data

        # Assess complexity
        score = self._assess_complexity(user_msg)
        if score < COMPLEXITY_THRESHOLD:
            print(f"[TaskDecomposer] Score {score} < threshold {COMPLEXITY_THRESHOLD}, skipping", file=sys.stderr, flush=True)
            return loop_data  # Below threshold, skip

        # Load MCP agent hints and match
        hints = self._load_mcp_hints()
        tool_hints = self._match_tools_to_hints(user_msg, hints)

        # Build and inject guidance
        guidance = self._build_guidance(score, tool_hints)

        if hasattr(loop_data, 'system'):
            loop_data.system.append(guidance)

        # ── Verification task injection for dev workflows ──
        # When multiagentdev handles a dev/fullstack task, inject mandatory
        # verification tasks that the agent MUST add to its task list.
        # This makes L1 (open todos) the primary completion mechanism.
        agent_name = getattr(self.agent, "agent_name", "").lower()
        if agent_name == "multiagentdev" and self._is_dev_task(user_msg):
            # Phase 0: Proactive planning — prerequisites + structured plan
            prerequisites = format_prerequisites_guidance()
            testability = format_testability_audit()
            structured_plan = format_structured_plan_guidance()
            if hasattr(loop_data, 'system'):
                loop_data.system.append(prerequisites)
                loop_data.system.append(testability)
                loop_data.system.append(structured_plan)
            # Phase A-D: Verification tasks
            verification_guidance = format_verification_guidance()
            if hasattr(loop_data, 'system'):
                loop_data.system.append(verification_guidance)
            # Phase B.5: LIT guidance
            lit_guidance = format_lit_guidance()
            if hasattr(loop_data, 'system'):
                loop_data.system.append(lit_guidance)
            print(
                "[TaskDecomposer] Dev workflow detected for multiagentdev "
                "— injected MEP prerequisites + testability audit + "
                "structured plan + verification + LIT tasks",
                file=sys.stderr, flush=True
            )

            # GAP-4 FIX: REMOVED (RCA-357).
            # This block incorrectly called init_requirements(user_msg, project_dir)
            # with wrong arg types (str, str) instead of (dict, list).
            # Requirements ledger is properly seeded by _10_goal_tracking extension
            # via seed_from_goal_state() — this was redundant and broken.

        # Mark as fired
        self.agent.set_data("_task_decomposer_fired", True)

        print(
            f"[TaskDecomposer] Complex task detected (score={score}). "
            f"Injected pre-planning guidance. "
            f"Tool hints: {[h['server'] for h in tool_hints]}",
            file=sys.stderr, flush=True
        )

        return loop_data

    def _get_user_message(self, loop_data) -> str:
        """Extract the user message text from loop_data.
        
        The framework's LoopData.user_message is a history.Message object.
        Message.content can be:
          - str: plain text
          - dict: e.g. {'user_message': 'text...'} or {'text': '...'} or {'raw_content': '...'}
          - list: multimodal parts, each can be str or dict
        """
        if hasattr(loop_data, 'user_message') and loop_data.user_message:
            msg = loop_data.user_message
            # history.Message stores text in .content attribute
            if hasattr(msg, 'content'):
                content = msg.content
                if isinstance(content, str):
                    return content
                # Content can be a dict (the framework wraps messages)
                if isinstance(content, dict):
                    # Primary key used by the framework
                    if "user_message" in content:
                        val = content["user_message"]
                        if isinstance(val, str):
                            return val
                    # Alternative keys
                    for key in ("text", "raw_content", "content"):
                        if key in content and isinstance(content[key], str):
                            return content[key]
                    # Fallback: stringify
                    return str(content)
                # Content can also be a list of parts (multimodal)
                if isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, str):
                            text_parts.append(part)
                        elif isinstance(part, dict):
                            for key in ("user_message", "text", "raw_content"):
                                if key in part and isinstance(part[key], str):
                                    text_parts.append(part[key])
                                    break
                    return " ".join(text_parts)
            # Fallback: try str()
            return str(msg)
        return ""

    def _assess_complexity(self, message: str) -> int:
        """Score task complexity using heuristics.
        
        Returns an integer score — higher means more complex.
        Threshold for triggering pre-planning is COMPLEXITY_THRESHOLD (3).
        """
        score = 0

        # Length-based
        if len(message) > 500:
            score += 1
        if len(message) > 1500:
            score += 1

        # Structure-based (numbered categories/sections like "### 1", "## 2", "3.", "Category 3")
        category_patterns = re.findall(r'###?\s*(?:Category\s*)?\d+', message, re.IGNORECASE)
        numbered_list = re.findall(r'^\s*\d+[\.\)]\s+', message, re.MULTILINE)
        category_count = len(category_patterns) + len(numbered_list)
        if category_count >= 3:
            score += 2
        if category_count >= 7:
            score += 2

        # Keyword-based
        big_data_keywords = [
            "audit", "crawl", "scan", "comprehensive",
            "all spaces", "all repos", "exhaustive", "full system",
            "health check", "system health", "smoke test",
        ]
        matches = sum(1 for kw in big_data_keywords if kw in message.lower())
        if matches >= 2:
            score += 2

        return score

    def _is_dev_task(self, message: str) -> bool:
        """Check if the message describes a software development task.

        Used to decide whether to inject verification task guidance.
        Returns True if the message contains development-related keywords.
        """
        dev_keywords = [
            "build", "frontend", "backend", "api", "react", "next.js",
            "nextjs", "vue", "angular", "express", "fastapi", "django",
            "database", "fullstack", "full-stack", "full stack",
            "web app", "webapp", "website", "deploy", "deployment",
            "application", "server", "client", "component", "page",
            "route", "endpoint", "authentication", "crud", "rest",
            "graphql", "typescript", "javascript", "python", "node",
            "npm", "package.json", "html", "css", "tailwind",
            "implement", "create", "develop", "scaffold",
        ]
        msg_lower = message.lower()
        match_count = sum(1 for kw in dev_keywords if kw in msg_lower)
        # Require at least 2 dev keyword matches to be confident
        return match_count >= 2

    def _get_hints_path(self) -> str:
        """Get the path to the MCP agent hints JSON file."""
        # Try relative to the project root
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        ))))
        return os.path.join(base, "mcps", "mcp_agent_hints.json")

    def _load_mcp_hints(self) -> dict:
        """Load MCP agent hints from JSON config."""
        hints_path = self._get_hints_path()
        try:
            with open(hints_path, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            pass  # Hints file not found, proceed without hints
            return {}

    def _match_tools_to_hints(self, message: str, hints: dict) -> list:
        """Match user prompt keywords to MCP agent hints.
        
        Returns list of dicts with server, agent, reason, parallel.
        """
        matched = []
        msg_lower = message.lower()
        for server, hint in hints.items():
            if server.startswith("_"):
                continue
            if not isinstance(hint, dict):
                continue
            keywords = hint.get("hint_keywords", [])
            if any(kw in msg_lower for kw in keywords):
                matched.append({
                    "server": server,
                    "agent": hint.get("hint_agent", "default"),
                    "reason": hint.get("hint_reason", ""),
                    "parallel": hint.get("hint_parallel", False),
                })
        return matched

    def _build_guidance(self, score: int, tool_hints: list) -> str:
        """Build the pre-planning system prompt injection."""
        lines = [
            "",
            "⚡ **COMPLEX TASK DETECTED** — You MUST pre-plan before executing:",
            "",
            "1. **Decompose** this task into independent subtasks before starting ANY work",
            "2. **Use `call_subordinate_batch`** for parallel independent work:",
            "   - `execution_mode: \"parallel\"` for independent subtasks (fastest)",
            "   - `execution_mode: \"wave\"` for dependent subtasks (respects order)",
            "3. **Route to the RIGHT agent profile** — don't try to do everything yourself:",
        ]

        # Add orchestrator awareness
        lines.extend([
            "",
            "**🎯 Orchestrator Profiles** (these coordinate teams — delegate entire domains to them):",
            "   - `multiagentdev`: Dev orchestrator (code, architect, debug, review modes). For ALL software engineering work.",
            "   - `alex`: Sales & marketing orchestrator. Coordinates account-leader, marketing-lead, sales-enabler, content-writer. For ALL business/marketing tasks.",
            "",
            "**🔬 Specialist Profiles** (these do focused work — give them specific, pre-broken tasks):",
            "   - `researcher`: Bulk non-code data tasks (API crawling, Google Chat, Forgejo scanning, web research). **Break work into specific units** — e.g., one task per space/repo.",
            "   - `browser`: Web automation, UI testing, dynamic content extraction",
            "   - `code`: Direct code implementation and bug fixes",
            "   - `architect`: System design and planning (read-only code)",
            "   - `debug`: Troubleshooting and diagnosis",
            "   - `security_auditor`: Security audits and vulnerability scanning",
            "   - `frontend`: UI/UX implementation (HTML, CSS, JS, React)",
            "   - `content-writer`: Blog posts, docs, marketing copy",
            "   - `mcp_builder`: MCP server development",
        ])

        # Add tool-specific routing hints
        if tool_hints:
            lines.extend([
                "",
                "**🔀 Detected tool routing hints:**",
            ])
            for hint in tool_hints:
                parallel_str = " (parallel OK)" if hint["parallel"] else ""
                lines.append(f"   - {hint['server']} → delegate to `{hint['agent']}` profile{parallel_str}: {hint['reason']}")

        # Add decomposition rules
        lines.extend([
            "",
            "4. **Decomposition rules:**",
            "   - For multidisciplinary tasks: assign work packages to the RIGHT orchestrator/specialist",
            "   - For data-heavy tasks (crawling, scanning): break into units FIRST, then delegate each unit to `researcher`",
            "   - For code + research combo: use `wave` mode — research first, then code",
            "   - Each subordinate has its OWN iteration budget — don't try to do everything in one agent",
            "5. **Synthesize** results from all subtasks into a unified final response",
            "6. **NEVER** re-do work that a subordinate already completed — use their results directly",
            "",
            "**🎯 Category → Agent Routing (multiagentdev decides this, NOT the architect):**",
            "```",
            "| Category       | Agent Profile | Scope                                                    |",
            "|----------------|---------------|----------------------------------------------------------|",
            "| frontend       | frontend      | ALL frontend: scaffolding, framework config, UI, styling |",
            "| backend        | code          | API routes, database, server logic, CLI tools            |",
            "| infrastructure | code          | Docker, CI/CD, deployment, env config                    |",
            "| integration    | code          | Wiring frontend + backend, E2E testing                   |",
            "| testing        | code          | Unit tests, integration tests, TDD setup                 |",
            "| research       | researcher    | Data gathering, API exploration, web research            |",
            "| design         | architect     | System design, planning, technical docs                  |",
            "```",
            "",
            "**🔴 CRITICAL: Frontend agent owns ALL frontend work — including scaffolding and framework config.**",
            "NEVER delegate `create-next-app`, `create-vite`, Tailwind config, PostCSS setup, or CSS framework",
            "configuration to the `code` agent. The `frontend` agent has specialized knowledge of build",
            "pipelines, CSS frameworks, and version-specific quirks (e.g., Tailwind v3 vs v4).",
            "",
            "**📋 Planning format — create a clear assignment table BEFORE executing:**",
            "",
            "**🔑 GUID Assignment (MANDATORY):** Use the `generate_guid` tool to assign a stable GUID",
            "to EVERY task in your decomposition. Call it in batch mode with all task titles:",
            '```json',
            '{"texts": ["Research XMR price", "Crawl Google Chat spaces", "Scaffold frontend", ...]}',
            '```',
            "Then include the returned GUIDs in your planning table and in every `call_subordinate` delegation.",
            "",
            "```",
            "| # | GUID         | Task Description              | Agent Profile    | Mode     | Dependencies |",
            "|---|--------------|-------------------------------|------------------|----------|--------------|",
            "| 1 | REQ-a1b2c3d4 | Research XMR price            | researcher       | -        | -            |",
            "| 2 | REQ-e5f6a7b8 | Crawl Google Chat spaces      | researcher       | -        | -            |",
            "| 3 | REQ-c9d0e1f2 | Scaffold + style frontend     | frontend         | -        | -            |",
            "| 4 | REQ-d3e4f5a6 | Implement API routes          | multiagentdev    | code     | -            |",
            "| 5 | REQ-b7c8d9e0 | Write marketing copy          | alex             | -        | 3            |",
            "```",
            "",
        ])

        return "\n".join(lines)

