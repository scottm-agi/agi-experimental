"""route_to_agent — Purpose-built routing tool for the default profile.

Detects user intent and routes requests to the correct specialist agent.
This is the AGENT routing tool — it decides which agent handles the request.

Separation of concerns:
    - Agent routing (this tool): which agent handles the request?
    - Model selection (agent_models.py): which LLM model to use? (per-profile)

Design:
    1. If explicit profile= provided, use it directly
    2. Otherwise, auto-detect intent via PromptRouter.classify_structured()
       (Tier 1 phrase matching + Tier 2 LLM classification)
    3. Map the detected category to the correct agent profile
    4. Validate against swarm_registry boundaries
    5. Spawn the target agent and pass the request through
    6. For simple/conversational requests, signal the default agent to handle directly
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from python.agent import Agent, UserMessage
from python.helpers.tool import Tool, Response
from python.helpers.prompt_router import PromptRouter
from python.helpers.swarm_registry import is_profile_allowed, get_allowed_profiles
from python.helpers.delegation_guards import validate_profile_exists
from python.initialize import initialize_agent

logger = logging.getLogger("agix.routing")


# ── Category → Profile mapping ──────────────────────────────────────
# Maps PromptRouter classification categories to the agent profiles
# that should handle them. Categories that belong to a swarm route
# to the swarm's orchestrator (not directly to the specialist).
CATEGORY_TO_PROFILE = {
    # Development swarm → multiagentdev orchestrator
    "multiagentdev": "multiagentdev",
    "code": "multiagentdev",       # code is part of dev swarm
    "debug": "multiagentdev",      # debug is part of dev swarm
    "frontend": "multiagentdev",   # frontend is part of dev swarm
    "architecture": "multiagentdev",
    "architect": "multiagentdev",
    "hacker": "hacker",            # independent agent, not in dev swarm

    # Sales/marketing swarm → alex orchestrator
    "alex": "alex",
    "sales_marketing": "alex",
    "content": "alex",             # content-writer is part of alex swarm

    # Independent specialists (not in any swarm)
    "security": "security_auditor",
    "security_auditor": "security_auditor",
    "mcp": "mcp_builder",
    "mcp_builder": "mcp_builder",
    "researcher": "multiagentdev",  # researcher is in multiagentdev's swarm
    "browser": "multiagentdev",      # browser is in multiagentdev's swarm

    # Dashboard
    "dashboard": "dashboard",
    "admin": "dashboard",
}

# Categories that indicate the default agent should handle directly
# (no routing needed)
DIRECT_HANDLE_CATEGORIES = {"simple", "chat_model", "_clarify"}


class Routing(Tool):
    """Routes user requests to the appropriate specialist agent.

    This tool is designed for the default profile only. It auto-detects
    intent using PromptRouter's two-tier classification and spawns the
    correct agent profile.
    """

    async def execute(self, message="", profile="", **kwargs):
        """Execute the routing.

        Args:
            message: The user's request to route.
            profile: Optional target profile override. If provided, skips
                     auto-detection and routes directly to this profile.
        """
        message = (message or "").strip()
        if not message:
            return Response(
                message=(
                    "No message provided. Please pass the user's request "
                    "in the 'message' parameter so I can detect intent and "
                    "route to the correct agent."
                ),
                break_loop=False,
            )

        # ── Step 1: Determine target profile ────────────────────────
        if profile and profile.strip():
            # Explicit profile override — skip auto-detection
            target_profile = profile.strip()
            routing_reason = f"Explicit profile override: {target_profile}"
            logger.info(f"[ROUTING] Explicit profile={target_profile}")
        else:
            # Auto-detect intent via PromptRouter
            router = PromptRouter.get_instance()
            decision = await router.classify_structured(message, agent=self.agent)

            logger.info(
                f"[ROUTING] PromptRouter decision: "
                f"profile={decision.profile}, "
                f"confidence={decision.confidence:.2f}, "
                f"reason={decision.reason}"
            )

            # Check for direct-handle categories
            if decision.profile in DIRECT_HANDLE_CATEGORIES:
                if decision.profile == "_clarify":
                    return Response(
                        message=(
                            f"I'm not sure which specialist to route this to "
                            f"(confidence: {decision.confidence:.0%}). "
                            f"Could you clarify what you need? For example:\n"
                            f"- **Development tasks**: building, coding, debugging\n"
                            f"- **Sales/marketing**: campaigns, outreach, content\n"
                            f"- **Security**: audits, vulnerability scans\n"
                            f"- **Research**: web research, analysis\n\n"
                            f"Classification reason: {decision.reason}"
                        ),
                        break_loop=False,
                    )
                else:
                    # simple/chat_model — handle directly
                    return Response(
                        message=(
                            f"This is a simple/conversational request. "
                            f"Handle it yourself directly — no routing needed. "
                            f"Respond to the user's message: \"{message[:200]}\""
                        ),
                        break_loop=False,
                    )

            # Map category to agent profile
            target_profile = CATEGORY_TO_PROFILE.get(
                decision.profile, decision.profile
            )
            routing_reason = (
                f"Auto-detected: {decision.profile} → {target_profile} "
                f"(confidence: {decision.confidence:.0%}, "
                f"reason: {decision.reason})"
            )

        # ── Step 2: Validate target profile ─────────────────────────
        error = validate_profile_exists(target_profile)
        if error:
            return Response(message=error, break_loop=False)

        # ── Step 3: Check swarm boundaries ──────────────────────────
        current_profile = getattr(self.agent.config, "profile", "default")
        if not is_profile_allowed(current_profile, target_profile):
            allowed = get_allowed_profiles(current_profile) or set()
            logger.warning(
                f"[ROUTING] Swarm boundary blocked: "
                f"{current_profile} → {target_profile}"
            )
            return Response(
                message=(
                    f"⛔ ROUTING BLOCKED: Cannot route directly to "
                    f"'{target_profile}' from '{current_profile}'. "
                    f"Allowed targets: {', '.join(sorted(allowed))}. "
                    f"Try routing to an orchestrator instead "
                    f"(e.g., 'multiagentdev' for dev tasks, 'alex' for sales)."
                ),
                break_loop=False,
            )

        # ── Step 4: Spawn target agent ──────────────────────────────
        self.agent.log(
            type="info",
            heading=f"🔀 Routing to {target_profile}",
            content=routing_reason,
        )

        try:
            # Create subordinate with target profile
            config = initialize_agent()
            config.profile = target_profile

            subordinate = Agent(
                number=self.agent.number + 1,
                config=config,
                context=self.agent.context,
            )
            subordinate.set_data(Agent.DATA_NAME_SUPERIOR, self.agent)

            # Inject the user message
            await subordinate.hist_add_user_message(
                UserMessage(message=message, attachments=[]),
                sender_type="user",
                sender_id="route_to_agent",
            )

            # Run the subordinate
            result = await subordinate.monologue()

            if result is None:
                result = (
                    f"Agent '{target_profile}' completed but returned no response. "
                    f"The task may have been partially completed. "
                    f"Check the subordinate's output for details."
                )

            logger.info(
                f"[ROUTING] {target_profile} completed. "
                f"Result length: {len(str(result))} chars"
            )

            return Response(
                message=str(result),
                break_loop=True,  # Default stops here — orchestrator manages full lifecycle
            )

        except Exception as e:
            logger.error(f"[ROUTING] Error spawning {target_profile}: {e}")
            return Response(
                message=(
                    f"⚠️ Error routing to '{target_profile}': {str(e)[:500]}. "
                    f"You may need to try again or route to a different agent."
                ),
                break_loop=False,
            )

    def get_log_object(self):
        return self.agent.context.log.log(
            type="tool",
            heading=f"icon://alt_route {self.agent.agent_name}: Routing Request",
            content="",
            kvps=self.args,
        )
