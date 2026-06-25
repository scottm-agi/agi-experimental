from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from python.helpers import settings

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """Structured routing output with confidence scoring.
    
    Every routing decision carries:
    - profile: which agent profile to route to
    - confidence: 0.0-1.0 float indicating how certain the classification is
    - reason: human-readable justification for the routing choice
    
    Inspired by agent-squad (AWS) ClassifierResult pattern.
    """
    profile: str
    confidence: float = 1.0
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "confidence": self.confidence,
            "reason": self.reason,
        }


class PromptRouter:
    """Intelligently routes requests to different model profiles based on classification.
    
    Two-tier approach:
    - Tier 1: Conservative multi-word phrase matching (only unambiguous phrases)
    - Tier 2: LLM-based full-context classification with structured JSON output
    
    Every routing decision produces a RoutingDecision with confidence score and reason.
    
    DESIGN PRINCIPLES:
    1. Single words like 'review', 'audit', 'design', 'build' are EXCLUDED from Tier 1
       because they change meaning based on context.
    2. Tier 2 forces the LLM to return structured JSON: {profile, confidence, reason}
    3. Confidence < 0.5 → _clarify signal (ask user for clarification)
    4. Default agent's job is to ROUTE, not handle. Only pure conversational stays local.
    """
    
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Tier 1: CONSERVATIVE multi-word phrase matching ──
    # Only unambiguous phrases that have ONE clear meaning regardless of context.
    # Single ambiguous words (review, audit, design, build, etc.) are EXCLUDED
    # and handled by Tier 2 LLM classification.
    TIER1_RULES: list[tuple[list[str], str]] = [
        # multiagentdev — only explicit dev orchestration phrases
        (["multi-agent dev", "multiagent dev", "multiagentdev", "development swarm",
          "system architect"], "multiagentdev"),
        
        # debug — only explicit debug/error phrases
        (["stack trace", "traceback", "debug this", "fix this bug", "segfault",
          "core dump", "runtime error"], "debug"),
        
        # frontend — only explicit UI/framework phrases
        (["landing page", "react component", "vue component", "tailwind css",
          "responsive design", "css styling", "webui"], "frontend"),
        
        # security — only explicit security phrases (NOT 'audit' alone)
        (["security audit", "vulnerability scan", "penetration test", "pentest",
          "owasp", "cve-", "security vulnerability"], "security"),
        
        # hacker — only explicit offensive security phrases
        (["red team", "blue team", "exploit development", "penetration test"], "hacker"),
        
        # mcp — very specific, low ambiguity
        (["model context protocol", "mcp server", "mcp client", "mcp builder"], "mcp"),
        
        # sales_marketing — multi-word phrases only
        (["sales enablement", "sales pipeline", "marketing strategy", "growth hack",
          "lead generation", "go-to-market", "gtm strategy", "battle card",
          "deal qualification", "pipeline review", "cold email", "email sequence",
          "marketing campaign"], "sales_marketing"),
        
        # content — only explicit writing phrases
        (["blog post", "case study", "whitepaper", "content writer", "copywriting"], "content"),
    ]

    # ── Confidence Threshold ──
    CONFIDENCE_CLARIFY_THRESHOLD = 0.5  # Below this → ask user for clarification

    async def classify_structured(self, request: str, agent: Any = None) -> RoutingDecision:
        """Classifies the user request into a RoutingDecision with confidence and reason.
        
        Uses a two-tier approach:
        1. Conservative multi-word phrase matching (confidence=1.0).
        2. LLM-based full-context classification with structured JSON output.
        
        Returns:
            RoutingDecision with profile, confidence (0-1), and reason.
        """
        request_lower = request.lower()
        
        # Tier 1: Conservative multi-word phrase matching
        for phrases, category in self.TIER1_RULES:
            for phrase in phrases:
                if phrase in request_lower:
                    decision = RoutingDecision(
                        profile=category,
                        confidence=1.0,
                        reason=f"Tier 1 phrase match: '{phrase}'"
                    )
                    logger.debug(f"PromptRouter: {decision.reason} → {category}")
                    return decision
        
        # Tier 2: LLM Classifier with structured JSON output
        if agent:
            try:
                return await self._tier2_classify(request, agent)
            except Exception as e:
                logger.warning(f"PromptRouter Tier 2 classification failed: {e}")

        return RoutingDecision(
            profile="simple",
            confidence=0.5,
            reason="No Tier 1 match and no LLM agent available for Tier 2"
        )

    async def _tier2_classify(self, request: str, agent: Any) -> RoutingDecision:
        """Tier 2: LLM-based classification with structured JSON output.
        
        Forces the LLM to return a JSON object with {profile, confidence, reason}.
        Validates the profile against the AgentCatalog.
        """
        valid_profiles = self._get_valid_profiles()
        profiles_list = ", ".join(sorted(valid_profiles))

        prompt = (
            "You are a request routing classifier. Analyze the FULL context of the user's request — "
            "not just individual words — to determine which agent profile should handle it.\n\n"
            "CRITICAL RULES:\n"
            "1. Read the ENTIRE request before deciding. Single words change meaning based on context.\n"
            "2. Your job is to ROUTE to the best agent. The default orchestrator handles ONLY "
            "pure conversational replies (greetings, 'what can you do?', clarifications).\n"
            "3. If unsure, prefer 'researcher' (broad capabilities) over 'simple' (do nothing).\n\n"
            "AMBIGUITY EXAMPLES:\n"
            "- 'review this PR' = code review → multiagentdev | 'review this contract' → researcher\n"
            "- 'audit the auth flow' = security → security_auditor | 'audit this vendor agreement' → researcher\n"
            "- 'design a microservice' = architecture → multiagentdev | 'design a campaign' → alex\n"
            "- 'build a REST API' = code → multiagentdev | 'build a go-to-market strategy' → alex\n\n"
            f"AVAILABLE PROFILES: {profiles_list}\n\n"
            "You MUST respond with ONLY a valid JSON object (no markdown, no code fences):\n"
            '{"profile": "<profile_name>", "confidence": <0.0-1.0>, "reason": "<one sentence why>"}\n\n'
            f"User Request: {request[:800]}"
        )

        raw = await agent.call_utility_model(
            system="You are a routing classifier. Return ONLY a JSON object: "
                   '{"profile": "...", "confidence": 0.0-1.0, "reason": "..."}. '
                   "No markdown, no code fences, no explanation.",
            message=prompt
        )

        return self._parse_tier2_response(raw, valid_profiles)

    def _parse_tier2_response(self, raw: str, valid_profiles: Set[str]) -> RoutingDecision:
        """Parse the LLM's JSON response into a RoutingDecision.
        
        Handles:
        - Valid JSON with valid profile → use as-is
        - Valid JSON with invalid profile → fallback to simple
        - Invalid JSON → try to extract category name, fallback to simple
        - Confidence below threshold → _clarify signal
        """
        raw = raw.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            raw = raw.strip()

        # Try JSON parse
        try:
            data = json.loads(raw)
            profile = str(data.get("profile", "simple")).strip().lower()
            confidence = float(data.get("confidence", 0.5))
            reason = str(data.get("reason", "LLM classification"))

            # Clamp confidence
            confidence = max(0.0, min(1.0, confidence))

            # Validate profile
            if profile not in valid_profiles and profile != "simple":
                logger.warning(f"PromptRouter: LLM returned invalid profile '{profile}', falling back to 'simple'")
                return RoutingDecision(
                    profile="simple",
                    confidence=0.5,
                    reason=f"LLM returned invalid profile '{profile}': {reason}"
                )

            # Check confidence threshold
            if confidence < self.CONFIDENCE_CLARIFY_THRESHOLD:
                logger.info(f"PromptRouter: Low confidence ({confidence}) for '{profile}', signaling _clarify")
                return RoutingDecision(
                    profile="_clarify",
                    confidence=confidence,
                    reason=reason
                )

            logger.info(f"PromptRouter: Tier 2 → '{profile}' (confidence={confidence:.2f}): {reason}")
            return RoutingDecision(profile=profile, confidence=confidence, reason=reason)

        except (json.JSONDecodeError, ValueError, TypeError) as e:
            # Fallback: try to extract a bare category name from the response
            logger.warning(f"PromptRouter: JSON parse failed ({e}), trying bare text fallback")
            category = raw.strip().lower().strip('"\'`')

            # Extended valid set includes 'simple'
            all_valid = valid_profiles | {"simple"}
            if category in all_valid:
                logger.info(f"PromptRouter: Tier 2 bare-text fallback matched '{category}'")
                return RoutingDecision(
                    profile=category,
                    confidence=0.6,
                    reason=f"Tier 2 bare-text fallback (JSON parse failed): {category}"
                )

            return RoutingDecision(
                profile="simple",
                confidence=0.5,
                reason=f"Tier 2 failed to parse LLM response: {raw[:100]}"
            )

    def _get_valid_profiles(self) -> Set[str]:
        """Get valid profile names from AgentCatalog, with fallback."""
        try:
            from python.helpers.agent_catalog import AgentCatalog
            catalog = AgentCatalog.get_instance()
            profiles = catalog.get_valid_profiles()
            if profiles:
                # Add 'simple' as always-valid
                return profiles | {"simple"}
        except Exception as e:
            logger.warning(f"PromptRouter: Failed to get profiles from AgentCatalog: {e}")

        # Fallback: hardcoded list
        return {
            "multiagentdev", "architecture", "debug", "code", "documentation",
            "researcher", "content", "security", "mcp", "hacker", "frontend",
            "sales_marketing", "alex", "security_auditor", "mcp_builder",
            "browser", "admin", "simple"
        }

    async def classify(self, request: str, agent: Any = None) -> str:
        """Classifies the user request into a category string.
        
        Backward-compatible wrapper around classify_structured().
        Returns just the profile name as a string.
        """
        decision = await self.classify_structured(request, agent)
        return decision.profile

    async def route_request(self, request: str, agent: Any = None) -> str:
        """Determines which model profile to use for the given request."""
        category = await self.classify(request, agent)
        
        s = settings.get_settings()
        routing_rules = s.get("routing_rules", {})
        
        # If the category is mapped to a profile, return it.
        # Otherwise, fall back to the default 'chat_model'.
        profile = routing_rules.get(category, "chat_model")
        
        logger.info(f"PromptRouter: routing to category '{category}' using profile '{profile}'")
        return profile


# Global helper
async def route_request(request: str, agent: Any = None) -> str:
    return await PromptRouter.get_instance().route_request(request, agent)
