from __future__ import annotations
"""
Fact Verification / Grounding Tool
====================================
Provides temporal fact-checking and grounding for agent-generated content.
Uses search tools (Perplexity, web search) to verify claims against
current real-world data, adding date context to prevent stale information.

Issue: #686
"""
import os
import logging
from datetime import datetime, timezone
from python.helpers.tool import Tool, Response

logger = logging.getLogger("agix.fact_check")


class FactCheck(Tool):
    """
    Grounding tool that verifies claims and facts against current data.
    Adds temporal awareness so agents use up-to-date information.
    """

    async def execute(self, **kwargs) -> Response:
        claim = self.args.get("claim", "")
        context_info = self.args.get("context", "")
        verify_type = self.args.get("type", "general")  # general, temporal, technical, data
        
        if not claim:
            return Response(
                message="Error: 'claim' argument is required. Provide the fact or claim to verify.",
                break_loop=False
            )
        
        # Get current temporal context
        now = datetime.now(timezone.utc)
        current_date = now.strftime("%B %d, %Y")
        current_year = now.year
        
        # Build grounding prompt with temporal awareness
        grounding_prompt = self._build_verification_prompt(
            claim=claim,
            context_info=context_info,
            verify_type=verify_type,
            current_date=current_date,
            current_year=current_year
        )
        
        # Use the agent's LLM to perform grounded verification
        # This leverages the agent's existing tools (Perplexity, search, etc.)
        instruction = (
            f"**Fact Verification Request** (as of {current_date})\n\n"
            f"**Claim to verify**: {claim}\n\n"
            f"{grounding_prompt}\n\n"
            f"**Instructions**: Use your search tools (perplexity_ask, search_engine) "
            f"to verify this claim against current sources. Report:\n"
            f"1. **Verdict**: Confirmed / Partially True / Unverified / False\n"
            f"2. **Evidence**: Key sources and data points\n"
            f"3. **Temporal note**: Whether this information is time-sensitive\n"
            f"4. **Confidence**: High / Medium / Low"
        )
        
        logger.info(f"Fact check requested: {claim[:100]}... (type: {verify_type})")
        
        return Response(
            message=instruction,
            break_loop=False
        )
    
    def _build_verification_prompt(
        self, claim: str, context_info: str, verify_type: str,
        current_date: str, current_year: int
    ) -> str:
        """Build a grounding-aware verification prompt."""
        
        temporal_note = (
            f"⚠️ **Temporal grounding**: Today is {current_date}. "
            f"Ensure all facts are verified against {current_year} data. "
            f"Flag any information that may be outdated."
        )
        
        type_guidance = {
            "general": "Verify this general claim against reliable sources.",
            "temporal": f"This is a time-sensitive claim. Use search with date filter for {current_year}.",
            "technical": "Verify this technical claim against official documentation and changelogs.",
            "data": "Verify these data points/statistics against authoritative sources.",
        }
        
        guidance = type_guidance.get(verify_type, type_guidance["general"])
        
        parts = [temporal_note, guidance]
        if context_info:
            parts.append(f"**Additional context**: {context_info}")
        
        return "\n\n".join(parts)
