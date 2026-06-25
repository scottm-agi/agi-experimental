from __future__ import annotations
"""
Research Quality Gate — tool_execute_after extension

When a RESEARCHER agent fires the 'response' tool, this gate checks for:
1. Hallucinated/generic placeholder names (John Smith, Jane Doe, etc.)
2. Fabricated LinkedIn URLs with generic slugs
3. Insufficient scrape_url usage (< MIN_SCRAPE_CALLS for deep research)
4. Missing code_execution_tool usage for database queries

If violations are found:
1. First attempt: clears break_loop, injects warning with correction instructions
2. After MAX_RESEARCH_BLOCKS: lets response through (prevents infinite loops)

Only fires for the 'researcher' profile to avoid affecting other agents.

Iteration 104: Root cause fix for Amazon layoff test producing 4 hallucinated
names (John Smith, Sarah Lee, Mike Chen, Emily Rodriguez) instead of real data.

Iteration 109: Depth-aware research gate. Auto-detects 'shallow' vs 'deep'
research from the delegation message to avoid blocking framework lookups
with the same aggressive thresholds used for people search.
"""

import logging
import re
from typing import Any, List, Optional

from python.helpers.extension import Extension
from python.helpers.tool import Response

logger = logging.getLogger("agix.research_quality_gate")

# Maximum number of times the gate will block a response.
# Prevents infinite retry loops while enforcing quality standards.
MAX_RESEARCH_BLOCKS = 2

# ── Depth-aware extraction thresholds (Iteration 109) ──
# Deep research: people search, WARN filings, market analysis → requires thorough extraction
MIN_SCRAPE_CALLS = 3
# Shallow research: framework lookups, API docs, version checks → search-only is fine
MIN_SHALLOW_CALLS = 0

# ── Depth classification keywords ──
# If the delegation message contains ANY of these, it's deep research.
# If NONE match, it defaults to shallow.
_DEEP_RESEARCH_INDICATORS = [
    # People/employee search
    r"\bemployee\w*\b", r"\blaid off\b", r"\blayoff\w*\b", r"\bfired\b",
    r"\bterminated\b", r"\bheadcount\b", r"\bstaff\b",
    # WARN / regulatory
    r"\bwarn\s+act\b", r"\bwarn\s+fil", r"\bsec\s+filing",
    # People-specific
    r"\bdecision\s+maker", r"\bkey\s+player", r"\bexecutive\w*\b",
    r"\bprospect\w*\b", r"\bleader\w*\b", r"\bmanagement\s+team\b",
    r"\bindividual\w*\b",
    # Market / competitive analysis
    r"\bmarket\s+landscape\b", r"\bcompetit\w+\b", r"\bindustry\s+analys",
    r"\bmarket\s+research\b", r"\bmarket\s+analys",
    # Legal / contract review
    r"\bnda\b", r"\bcontract\b", r"\blegal\b", r"\bliabilit\w*\b",
    r"\bclause\w*\b", r"\baudit\b",
    # Deep data extraction
    r"\bbulk\s+data\b", r"\bdatabase\b", r"\bscrape\b",
    r"\bh1b\b", r"\blca\b", r"\bglass\s*door\b",
]

_SHALLOW_RESEARCH_INDICATORS = [
    # Framework / library lookups
    r"\bframework\b", r"\blibrary\b", r"\bpackage\b", r"\bnpm\b",
    r"\bversion\b", r"\blatest\s+(?:stable|version|release)\b",
    r"\bconfigur\w+\b", r"\bsetup\b", r"\binstall\w*\b",
    # Specific tech names (strong signal for shallow)
    r"\bnext\.?js\b", r"\breact\b", r"\bvue\b", r"\bsvelte\b",
    r"\btailwind\b", r"\bprisma\b", r"\bvite\b", r"\bwebpack\b",
    r"\bstripe\b", r"\bfirebase\b", r"\bsupabase\b", r"\bauth0?\b",
    r"\bpostgres\w*\b", r"\bmongo\w*\b", r"\bredis\b",
    r"\btypescript\b", r"\bpython\b", r"\brust\b", r"\bgo\s+lang",
    # Documentation / API reference
    r"\bapi\s+(?:reference|docs?|endpoint)\b", r"\bdocumentati\w+\b",
    r"\bbest\s+practice\w*\b", r"\bmigrati\w+\b", r"\btutorial\b",
    r"\bapp\s+router\b", r"\bpages?\s+router\b",
]


class ResearchQualityGate(Extension):
    # Context-aware: researcher profile only, response tool
    PROFILES = {"researcher"}
    TOOLS = frozenset({"response"})

    """Block researcher responses containing hallucinated data."""

    async def execute(self, tool_name: str = "", response: Any = None, **kwargs):
        """After tool execution, validate research quality if it's the response tool."""
        if not tool_name or response is None:
            return

        tool_lower = tool_name.lower()

        # Track tool usage for the researcher profile
        if tool_lower not in ("response",):
            profile = getattr(getattr(self.agent, "config", None), "profile", "")
            if profile == "researcher":
                history = self.agent.data.setdefault("_research_tool_history", [])
                history.append(tool_lower)
            return

        # Only intercept the 'response' tool for the researcher profile
        profile = getattr(getattr(self.agent, "config", None), "profile", "")
        if profile != "researcher":
            return



        logger.info("[RESEARCH QUALITY GATE] Response tool intercepted, checking quality...")

        # Check max-block counter
        block_count = self.agent.data.get("_research_quality_block_count", 0)
        if block_count >= MAX_RESEARCH_BLOCKS:
            logger.warning(
                f"[RESEARCH QUALITY GATE] Max blocks ({MAX_RESEARCH_BLOCKS}) reached. "
                f"Letting through."
            )
            self.agent.data["_research_quality_block_count"] = 0
            return

        # Get response text
        response_text = ""
        if isinstance(response, Response) and response.message:
            response_text = response.message
        elif hasattr(response, "message") and response.message:
            response_text = response.message

        if not response_text or len(response_text) < 20:
            return

        # ── Determine research depth ──
        depth = self._get_effective_depth()
        logger.info(f"[RESEARCH QUALITY GATE] Effective research depth: {depth}")

        # ── Run all checks ──
        all_issues: List[str] = []

        # 3. Check tool usage (scrape quota) — depth-aware
        tool_history = self.agent.data.get("_research_tool_history", [])
        tool_issues = self._check_tool_usage(tool_history)
        all_issues.extend(tool_issues)

        # 4. Code execution suggestions (soft warning)
        code_suggestions = self._check_code_execution_usage(tool_history)

        # 5. Check for hallucinated names (G-2 fix — was defined but never called)
        hallucination_issues = self._check_hallucinated_names(response_text)
        all_issues.extend(hallucination_issues)

        # 6. Check for fabricated URLs (G-3 fix — was defined but never called)
        url_issues = self._check_fabricated_urls(response_text)
        all_issues.extend(url_issues)

        if all_issues:
            # BLOCK the response
            logger.warning(
                f"[RESEARCH QUALITY GATE] BLOCKED — {len(all_issues)} issues found: "
                f"{all_issues[:3]}"
            )

            if isinstance(response, Response):
                response.break_loop = False

            # Update response log item in-place for seamless UX
            try:
                loop_data = self.agent.loop_data
                if loop_data and "log_item_response" in loop_data.params_temporary:
                    log_item = loop_data.params_temporary["log_item_response"]
                    log_item.update(
                        content="",
                        heading=f"icon://refresh {self.agent.agent_name}: Retrying (quality check)...",
                    )
            except Exception as e:
                logger.warning(f"[RESEARCH QUALITY GATE] Could not update log item: {e}")

            # Build warning message
            warning_parts = [
                "🔴 RESEARCH QUALITY GATE FAILED",
                "Your response has been BLOCKED because it contains data quality issues:",
                "",
            ]
            for issue in all_issues:
                warning_parts.append(f"  ❌ {issue}")

            if code_suggestions:
                warning_parts.append("")
                warning_parts.append("💡 SUGGESTIONS:")
                for suggestion in code_suggestions:
                    warning_parts.append(f"  💡 {suggestion}")

            warning_parts.extend([
                "",
                "=== MANDATORY CORRECTIONS ===",
                "",
                "1. REMOVE all hallucinated/placeholder names from your response.",
                "   NEVER INVENT names. Only include names you found in scraped sources.",
                "",
                "2. USE `code_execution_tool` to download BULK DATA directly:",
                "   - Government databases (DOL, SEC, WARN Act filings, etc.)",
                "   - Industry data portals (h1bdata.info, layoffs.fyi, etc.)",
                "   - APIs (WARNFirehose, Wayback Machine CDX, DATA.GOV, etc.)",
                "   - B2B directories (RocketReach, LeadIQ, Apollo.io)",
                "",
                "3. EXTRACT data from at least 3 sources (scrape_url + code_execution_tool both count).",
                "   Save bulk results as CSV to project deliverables.",
                "",
                "4. If you CANNOT find specific individual names, say so HONESTLY.",
                "   Report aggregate data (headcount, departments, locations) instead.",
                "   DO NOT fabricate names to fill your report.",
                "",
                "🔴 NEVER INVENT NAMES. NEVER FABRICATE LINKEDIN URLS.",
                "If real names are unavailable, provide verified aggregate data instead.",
            ])

            await self.agent.hist_add_warning(message="\n".join(warning_parts))

            # Increment block counter
            self.agent.data["_research_quality_block_count"] = block_count + 1
            logger.info(
                f"[RESEARCH QUALITY GATE] Block count: "
                f"{block_count + 1}/{MAX_RESEARCH_BLOCKS}"
            )
        else:
            logger.info("[RESEARCH QUALITY GATE] Response quality OK — passing through")
            self.agent.data["_research_quality_block_count"] = 0

    # ── Depth Detection (Iteration 109 → 111) ──

    def _get_effective_depth(self) -> str:
        """Determine the effective research depth.
        
        Priority:
        1. Explicit `_research_depth` on agent.data (set by calling agent via
           call_subordinate's research_depth parameter)
        2. Fallback keyword inference from the full delegation message
        3. Default to 'shallow' — most delegated research is lightweight.
           Deep research (people search, WARN filings) must be explicitly
           declared by the orchestrator.
        
        Iteration 111: Removed 200/500-char heuristics. The orchestrator now
        explicitly sets research_depth on each delegation. The gate trusts
        the caller's declaration. Inference is a lightweight fallback only.
        """
        # 1. Check explicit override (set by call_subordinate/batch)
        explicit = self.agent.data.get("_research_depth", "")
        if explicit in ("shallow", "deep"):
            logger.info(f"[RESEARCH QUALITY GATE] Using explicit depth: {explicit}")
            return explicit

        # 2. Fallback: infer from the full delegation message (no truncation)
        try:
            history = getattr(self.agent, "history", None)
            if history:
                topic = getattr(history, "current", None)
                if topic:
                    messages = getattr(topic, "messages", [])
                    for msg in messages:
                        if not getattr(msg, "ai", True):
                            content = getattr(msg, "content", "")
                            if isinstance(content, dict):
                                content = content.get("user_message", "") or content.get("message", "")
                            if isinstance(content, str) and content.strip():
                                inferred = self._infer_research_depth(content)
                                logger.info(
                                    f"[RESEARCH QUALITY GATE] Inferred depth '{inferred}' "
                                    f"from delegation message (fallback): {content[:120]}..."
                                )
                                return inferred
        except Exception as e:
            logger.debug(f"[RESEARCH QUALITY GATE] Could not infer depth: {e}")

        # 3. Default to shallow — orchestrators must explicitly set 'deep'
        logger.info("[RESEARCH QUALITY GATE] No explicit depth set, defaulting to 'shallow'")
        return "shallow"

    def _infer_research_depth(self, message: str) -> str:
        """Infer research depth from delegation message as a HELPER signal.

        RCA-358 V-3: Keywords provide weighted signals and reasoning context
        to the LLM/agent. When the orchestrator explicitly sets _research_depth,
        that takes priority (see _get_effective_depth). This fallback inference
        provides specifics, context, and weights — the LLM decides in the end.

        Returns 'deep' or 'shallow' based on keyword signal strength.
        Shallow is the default when signals are ambiguous.
        """
        if not message:
            return "shallow"

        msg_lower = message.lower()

        has_deep = any(re.search(pattern, msg_lower) for pattern in _DEEP_RESEARCH_INDICATORS)
        has_shallow = any(re.search(pattern, msg_lower) for pattern in _SHALLOW_RESEARCH_INDICATORS)

        if has_deep and not has_shallow:
            return "deep"
        if has_shallow:
            return "shallow"

        # Neither matched → default to shallow (callers set 'deep' explicitly)
        return "shallow"

    def _check_tool_usage(self, tool_history: List[str]) -> List[str]:
        """Check that the researcher used sufficient data extraction calls.
        
        scrape_url and code_execution_tool both count toward the minimum.
        A pipeline approach with heavy code_execution_tool usage is valid.
        
        Iteration 109: Depth-aware thresholds.
        - 'deep' research: MIN_SCRAPE_CALLS (3) extraction calls required
        - 'shallow' research: MIN_SHALLOW_CALLS (0) — search-only is fine
        """
        depth = self._get_effective_depth()
        min_required = MIN_SCRAPE_CALLS if depth == "deep" else MIN_SHALLOW_CALLS

        if min_required == 0:
            logger.info(
                f"[RESEARCH QUALITY GATE] Shallow research detected — "
                f"skipping extraction check (min_required=0)"
            )
            return []

        issues = []
        scrape_count = sum(1 for t in tool_history if t == "scrape_url")
        code_count = sum(1 for t in tool_history if t == "code_execution_tool")
        total_extraction = scrape_count + code_count
        if total_extraction < min_required:
            issues.append(
                f"Insufficient data extraction: {scrape_count} scrape_url + "
                f"{code_count} code_execution = {total_extraction} calls "
                f"(minimum: {min_required}). You MUST extract data from more "
                f"sources. Use code_execution_tool to download bulk datasets from "
                f"government databases, industry portals, or data APIs. "
                f"Use word-boundary regex to prevent false positive entity matches."
            )
        return issues

    def _check_code_execution_usage(self, tool_history: List[str]) -> List[str]:
        """Check if code_execution_tool was used for database queries (soft suggestion)."""
        suggestions = []
        code_count = sum(1 for t in tool_history if t == "code_execution_tool")
        if code_count == 0:
            suggestions.append(
                "No code_execution_tool usage detected. For bulk data extraction, "
                "use code_execution_tool to: "
                "(1) Download bulk datasets from government portals (DOL, SEC, state agencies), "
                "(2) Query data APIs (WARNFirehose, Wayback Machine CDX, DATA.GOV), "
                "(3) Parse structured data from industry databases and B2B directories."
            )
        return suggestions

    def _check_hallucinated_names(self, response_text: str) -> List[str]:
        """Check for hallucinated/generic placeholder names."""
        issues = []
        hallucinated_names = [
            "john smith", "jane doe", "john doe", "mike chen",
            "sarah lee", "emily rodriguez", "david kim",
            "alex johnson", "jessica wang", "robert taylor",
            "lisa chen", "james wilson", "maria garcia",
        ]
        text_lower = response_text.lower()
        for name in hallucinated_names:
            if name in text_lower:
                issues.append(
                    f"Hallucinated name detected: '{name}'. "
                    f"NEVER invent names. Only include names verified from scraped sources."
                )
        return issues

    def _check_fabricated_urls(self, response_text: str) -> List[str]:
        """Check for fabricated LinkedIn URLs with generic slugs."""
        issues = []
        # Match LinkedIn profile URLs
        linkedin_pattern = r'linkedin\.com/in/([a-zA-Z0-9_-]+)'
        matches = re.findall(linkedin_pattern, response_text.lower())

        # Generic slug patterns that indicate fabrication
        generic_patterns = [
            r'johnsmith', r'janedoe', r'johndoe', r'mikechen',
            r'sarahlee', r'emilyrodriguez', r'davidkim',
            r'alexjohnson', r'jessicawang', r'roberttaylor',
            r'lisachen', r'jameswilson', r'mariagarcia',
            # Role-based slugs
            r'\w+[-_](?:pm|cto|ceo|cfo|exec|vp|svp|evp|dir|mgr|lead|sr|eng)',
            r'(?:pm|cto|ceo|cfo|exec|vp|svp|evp|dir|mgr|lead|sr|eng)[-_]\w+',
        ]

        for slug in matches:
            for pattern in generic_patterns:
                if re.search(pattern, slug):
                    issues.append(
                        f"Fabricated LinkedIn URL detected: slug '{slug}' matches generic pattern. "
                        f"NEVER fabricate LinkedIn URLs."
                    )
                    break

        return issues
