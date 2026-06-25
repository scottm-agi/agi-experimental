"""
Goal State Manager
Singleton service to manage goal states across agents.
Part of Supervisor Reliability Enhancement - Gap 2.
"""

from typing import Optional, Dict, List, Any
import asyncio
import json

from python.helpers.goal_state import GoalState, GoalStatus, Subgoal, SubgoalStatus
from python.helpers.log import Log

import logging

_logger = logging.getLogger("agix.goal_state_manager")


class GoalStateManager:
    """
    Singleton service to manage goal states across agents.
    Provides extraction, persistence, and lookup capabilities.
    """
    _instance = None
    _goals: Dict[str, GoalState] = {}  # context_id -> GoalState

    # Exposed for test introspection (Layer 1 granularity validation)
    _EXTRACTION_PROMPT = (
        "Extract ONE independently-verifiable success criterion per distinct "
        "feature, integration, named route, named page, named API, CTA, or "
        "deliverable mentioned in the prompt. The count is driven entirely by "
        "the prompt content — 1 change = 1 criterion, 100 net-new features = "
        "100 criteria. Never lump multiple features into one criterion "
        "(e.g. 'review management AND response generation' must be two "
        "independent criteria). Each criterion must be testable by a single "
        "assert or BDD scenario. "
        "CRITICAL: Identify the CORE PRODUCT or SERVICE — the thing users pay "
        "for, the primary value proposition. Extract EVERY monetized feature, "
        "product workflow, routing logic, and business logic conditional as "
        "its own criterion. If the prompt describes pricing ($X/mo), ensure "
        "every feature included in that price tier has a dedicated criterion. "
        "Pay special attention to workflow patterns (X → Y routing, if/then "
        "conditional flows, step sequences, user journeys) — these are often "
        "the most critical features and must NOT be generalized away."
    )
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._goals = {}
        return cls._instance

    @classmethod
    def get_instance(cls) -> "GoalStateManager":
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def extract_goal_from_prompt(self, prompt: str, agent=None,
                                       regex_signals=None) -> Dict[str, Any]:
        """
        Use LLM to extract structured goal information from user prompt.

        Args:
            prompt: The raw user prompt text.
            agent: Agent instance with call_llm capability (optional).
            regex_signals: List[WeightedCandidate] from Layer 1 deterministic
                extraction. When provided, the prompt is annotated with regex
                findings to improve LLM classification coverage.

        Returns: {"objective": str, "success_criteria": List[str], "subgoals": List[str]}
        """
        # Phase 2: Annotate prompt with regex signals if available
        prompt_for_llm = prompt[:12000]
        signal_section = ""
        if regex_signals:
            from python.helpers.weighted_candidate import format_signal_annotations
            annotated = format_signal_annotations(prompt_for_llm, regex_signals)
            signal_section = (
                "\n\nIMPORTANT: The prompt has been pre-scanned by a deterministic regex\n"
                "extractor. Signal annotations below identify concrete requirements\n"
                "found in the text. Your success_criteria MUST include ALL high-confidence\n"
                "signals (≥0.85) and should classify medium-confidence signals (0.5–0.84)\n"
                "into appropriate criteria. Do NOT ignore these signals.\n"
            )
            prompt_for_llm = annotated

        extraction_prompt = f"""Analyze the following user request and extract:
1. A single-sentence objective (what the user ultimately wants)
2. SPECIFIC, TESTABLE success criteria — the count is driven entirely by the
   prompt content (1 change = 1 criterion, 100 net-new features = 100 criteria).
   Extract ONE independently-verifiable criterion per distinct feature,
   integration, named route/page, data model, CTA, or deliverable.
   NEVER lump multiple features into a single criterion — each must be
   testable by a single assert or BDD scenario.
   BAD: "Task completed successfully" or "App works as expected"
   BAD: "Review management and response generation" (compound — split these)
   GOOD: "Stripe payment checkout page accepts credit cards"
   GOOD: "Discovery engine returns Perplexity-sourced results"
   GOOD: "AI-generated response drafts appear in dashboard"

   CRITICAL — CORE PRODUCT IDENTIFICATION:
   - Identify the CORE PRODUCT or SERVICE — what users pay for, the primary
     value proposition. This is the MOST IMPORTANT thing to extract.
   - Extract EVERY monetized feature and product workflow as its own criterion.
   - Pay special attention to workflow patterns: routing logic (X → Y),
     conditional flows (if X then Y), step sequences, and user journeys.
   - If the prompt mentions pricing ($X/mo), ensure every feature included in
     that price tier has a dedicated success criterion.
   - Product workflows must NOT be generalized away into vague criteria.

3. 3-7 logical subgoals/steps to achieve this
{signal_section}
User Request:
\"\"\"
{prompt_for_llm}
\"\"\"

Respond ONLY with valid JSON:
{{
  "objective": "...",
  "success_criteria": ["specific criterion 1", "specific criterion 2", "..."],
  "subgoals": ["step 1", "step 2", "..."]
}}"""
        
        try:
            if agent and hasattr(agent, 'call_llm'):
                # Use agent's LLM capability
                from python.helpers.messages import Message
                msgs = [Message(role="user", content=extraction_prompt)]
                response = await agent.call_llm(msgs, max_tokens=2000)
                response_text = response.content if hasattr(response, 'content') else str(response)
            else:
                # Fallback - just use prompt as-is
                raise Exception("No LLM available")
            
            # Extract JSON from response
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                return json.loads(response_text[json_start:json_end])
                
        except Exception as e:
            import logging
            logging.getLogger("agix.goal_state_manager").info(f"Goal extraction fallback (no LLM): {e}")
        
        # Fallback: derive criteria from prompt keywords
        objective = prompt[:200] + "..." if len(prompt) > 200 else prompt
        return {
            "objective": objective,
            "success_criteria": [f"Deliver all features described in: {prompt[:100]}"],
            "subgoals": [f"Implement requirements from user prompt: {prompt[:100]}"]
        }

    async def create_goal(
        self, 
        context_id: str, 
        agent_id: str, 
        prompt: str, 
        agent=None,
        parent_goal_id: str = None,
        regex_signals=None,
        project_dir: str = None
    ) -> GoalState:
        """Create a new goal state for a context.
        
        Args:
            context_id: The context ID for this goal.
            agent_id: The agent ID creating the goal.
            prompt: The user's original prompt text.
            agent: Agent instance with call_llm capability (optional).
            parent_goal_id: Parent goal ID for subordinate agents.
            regex_signals: Weighted candidates from Layer 1 extraction.
            project_dir: Project directory path. When provided, saves to
                         {project_dir}/.agix.proj/goal_states/.
        """
        extracted = await self.extract_goal_from_prompt(prompt, agent, regex_signals=regex_signals)
        
        goal = GoalState(
            context_id=context_id,
            agent_id=agent_id,
            original_prompt=prompt,
            extracted_objective=extracted.get("objective", ""),
            success_criteria=extracted.get("success_criteria", []),
            parent_goal_id=parent_goal_id,
            status=GoalStatus.IN_PROGRESS
        )
        
        # Create subgoals
        for i, sg_desc in enumerate(extracted.get("subgoals", [])):
            goal.subgoals.append(Subgoal(
                id=f"sg_{i+1}",
                description=sg_desc
            ))
        
        # Persist and cache
        try:
            goal.save(project_dir=project_dir)
        except Exception as e:
            Log.warning(f"[GoalStateManager] Could not persist goal: {e}")
            
        self._goals[context_id] = goal
        
        Log.info(f"[GoalStateManager] Created goal for context {context_id}: {goal.extracted_objective[:100]}")
        return goal

    def get_goal(self, context_id: str, project_dir: str = None) -> Optional[GoalState]:
        """Retrieve goal state for a context (from cache or disk).
        
        Args:
            context_id: The context ID to look up.
            project_dir: Project directory path. When provided, tries loading from
                         {project_dir}/.agix.proj/goal_states/ first, then falls
                         back to the legacy path.
        """
        if context_id in self._goals:
            return self._goals[context_id]
        
        try:
            goal = GoalState.load(context_id, project_dir=project_dir)
            if goal:
                self._goals[context_id] = goal
                return goal
        except Exception as e:
            Log.debug(f"[GoalStateManager] Could not load goal for {context_id}: {e}")
            
        return None

    # ── RCA-475 D6: GoalSM wrapper ──────────────────────────────────

    def _get_or_create_goal_sm(self, goal: GoalState):
        """Get or create a GoalSM for a GoalState instance.

        RCA-475 D6: SM lives on goal._goal_sm, seeded with the goal's
        current status on first access.

        RCA-479 Fix: Handles corrupted SM from JSON round-trip.
        """
        from python.helpers.state_machines.goal_sm import GoalSM
        existing = getattr(goal, "_goal_sm", None)
        if not isinstance(existing, GoalSM):
            current = goal.status.value if isinstance(goal.status, GoalStatus) else str(goal.status)
            goal._goal_sm = GoalSM(status=current, entity_id=goal.context_id)
        return goal._goal_sm

    def _sync_goal_sm(self, goal: GoalState, target: str, reason: str = ""):
        """Sync GoalSM with the actual goal status.

        Invalid transitions are force-synced with a warning (migration mode).
        """
        sm = self._get_or_create_goal_sm(goal)
        if sm.status == target:
            return  # idempotent
        ok, msg = sm.transition(
            target,
            reason=reason,
            source="goal_state_manager",
        )
        if not ok:
            _logger.warning(f"[GOAL SM] {msg} — force-syncing (migration mode)")
            sm.transition(
                target,
                reason=f"force-sync: {msg}",
                source="goal_state_manager",
                force=True,
            )

    def update_goal_status(self, context_id: str, status: GoalStatus) -> bool:
        """Update the status of a goal."""
        goal = self.get_goal(context_id)
        if goal:
            # RCA-475 D6: Create SM BEFORE assignment so it seeds with OLD status
            self._get_or_create_goal_sm(goal)
            goal.status = status
            # RCA-475 D6: SM wrap — sync GoalSM alongside status assignment
            self._sync_goal_sm(goal, status.value, reason="update_goal_status")
            try:
                goal.save()
            except Exception as e:
                Log.warning(f"[GoalStateManager] Could not save goal: {e}")
            return True
        return False

    def record_completion_claim(self, context_id: str) -> bool:
        """Record that the agent has claimed task completion."""
        from datetime import datetime, timezone
        goal = self.get_goal(context_id)
        if goal:
            self._get_or_create_goal_sm(goal)  # seed before assignment
            goal.completion_claimed_at = datetime.now(timezone.utc).isoformat()
            goal.status = GoalStatus.COMPLETED
            # RCA-475 D6: SM wrap
            self._sync_goal_sm(goal, "completed", reason="record_completion_claim")
            try:
                goal.save()
            except Exception as e:
                Log.warning(f"[GoalStateManager] Could not save goal: {e}")
            return True
        return False

    def verify_completion(self, context_id: str) -> bool:
        """Mark goal as verified complete."""
        from datetime import datetime, timezone
        goal = self.get_goal(context_id)
        if goal:
            self._get_or_create_goal_sm(goal)  # seed before assignment
            goal.status = GoalStatus.VERIFIED
            goal.verified_complete_at = datetime.now(timezone.utc).isoformat()
            # RCA-475 D6: SM wrap
            self._sync_goal_sm(goal, "verified", reason="verify_completion")
            try:
                goal.save()
            except Exception as e:
                Log.warning(f"[GoalStateManager] Could not save goal: {e}")
            return True
        return False

    def reject_completion(self, context_id: str, reason: str = None) -> bool:
        """Reject a completion claim and set goal back to in progress."""
        goal = self.get_goal(context_id)
        if goal:
            self._get_or_create_goal_sm(goal)  # seed before assignment
            goal.status = GoalStatus.IN_PROGRESS
            goal.completion_claimed_at = None
            if reason:
                goal.last_intervention_reason = f"Completion rejected: {reason}"
            # RCA-475 D6: SM wrap
            self._sync_goal_sm(goal, "in_progress", reason="reject_completion")
            try:
                goal.save()
            except Exception as e:
                Log.warning(f"[GoalStateManager] Could not save goal: {e}")
            return True
        return False

    def record_intervention(self, context_id: str, reason: str) -> bool:
        """Record an intervention for a goal."""
        goal = self.get_goal(context_id)
        if goal:
            goal.increment_intervention(reason)
            try:
                goal.save()
            except Exception as e:
                Log.warning(f"[GoalStateManager] Could not save goal: {e}")
            return True
        return False

    def mark_subgoal_complete(self, context_id: str, subgoal_id: str, evidence: str = None) -> bool:
        """Mark a subgoal as completed."""
        goal = self.get_goal(context_id)
        if goal:
            goal.mark_subgoal_complete(subgoal_id, evidence)
            try:
                goal.save()
            except Exception as e:
                Log.warning(f"[GoalStateManager] Could not save goal: {e}")
            return True
        return False

    def get_goal_summary(self, context_id: str) -> Optional[str]:
        """Get a formatted summary of a goal's progress."""
        goal = self.get_goal(context_id)
        if goal:
            return goal.get_progress_summary()
        return None

    def clear_goal(self, context_id: str) -> bool:
        """Remove a goal from cache (and optionally disk)."""
        if context_id in self._goals:
            del self._goals[context_id]
            return True
        return False

    def get_all_active_goals(self) -> List[GoalState]:
        """Get all goals that are not yet verified or failed."""
        return [
            g for g in self._goals.values() 
            if g.status not in [GoalStatus.VERIFIED, GoalStatus.FAILED]
        ]
