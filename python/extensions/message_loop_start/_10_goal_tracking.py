"""
Goal Tracking Extension
Creates and maintains goal state on first user message.
Part of Supervisor Reliability Enhancement - Gap 2.
Priority: 10 (runs early, before other extensions)
"""

import logging

from python.helpers.extension import Extension

logger = logging.getLogger("agix.goal_tracking")


class GoalTrackingExtension(Extension):
    """Extension to track goals from user prompts."""
    
    async def execute(self, loop_data=None, **kwargs):
        """Execute goal tracking on message loop start."""
        agent = self.agent
        context = agent.context
        
        # Only process on first iteration with a user message
        iteration = getattr(loop_data, 'iteration', 0) if loop_data else 0
        if iteration > 0:
            return loop_data
        
        # Import here to avoid circular imports
        try:
            from python.helpers.goal_state_manager import GoalStateManager
        except ImportError as e:
            logger.debug(f"[GoalTracking] Could not import GoalStateManager: {e}")
            return loop_data
        
        # Check if this is a new goal (no existing goal for context)
        gsm = GoalStateManager.get_instance()
        project_dir = agent.data.get("_active_project_dir", None) or None
        existing_goal = gsm.get_goal(context.id, project_dir=project_dir)
        
        if existing_goal:
            # Goal already exists - attach to agent for visibility
            agent._current_goal = existing_goal
            logger.debug(f"[GoalTracking] Existing goal found for context {context.id}")
            return loop_data
        
        # Extract the user's prompt from history
        history = context.history if hasattr(context, 'history') else []
        user_messages = [m for m in history if m.get("role") == "user"]
        
        if not user_messages:
            return loop_data
        
        # Get the first user message (the original prompt)
        first_prompt = user_messages[0].get("content", "")
        if not first_prompt:
            return loop_data
        
        # Skip goal creation for very short prompts (likely not substantial tasks)
        if len(first_prompt.strip()) < 20:
            logger.debug(f"[GoalTracking] Prompt too short for goal extraction")
            return loop_data
        
        # Create goal state
        try:
            # ── PHASE 1: DETERMINISTIC EXTRACTION (Regex with Confidence) ──
            # Run weighted candidate extraction BEFORE LLM goal creation.
            # This gives us regex signals to inject into the LLM prompt,
            # marrying deterministic and intelligence-based extraction.
            candidates = None
            try:
                from python.helpers.prompt_line_item_extractor import extract_weighted_candidates
                candidates = extract_weighted_candidates(first_prompt)
                if candidates:
                    logger.info(
                        f"[GoalTracking] Hybrid pipeline: {len(candidates)} weighted "
                        f"candidates extracted (regex signals ready for LLM annotation)"
                    )
            except Exception as wc_err:
                logger.warning(f"[GoalTracking] Weighted extraction failed (non-fatal): {wc_err}")

            # ── PHASE 2: LLM GOAL EXTRACTION (Signal-Annotated) ──
            # Pass regex signals to create_goal so the LLM receives an
            # annotated prompt with [REGEX_SIGNAL] markers. This is the
            # "marriage" — the LLM is signal-aware, not independent.
            goal = await gsm.create_goal(
                context_id=context.id,
                agent_id=str(agent.number),
                prompt=first_prompt,
                agent=agent,
                regex_signals=candidates if candidates else None,
                project_dir=project_dir,
            )
            agent._current_goal = goal
            logger.info(f"[GoalTracking] Goal created: {goal.extracted_objective[:100]}")

            # ── UNIFICATION: Seed requirements ledger from GoalState ──
            if goal.success_criteria:
                try:
                    from python.helpers.requirements_ledger import seed_from_goal_state
                    seed_from_goal_state(agent.data, goal.success_criteria)
                except Exception as seed_err:
                    logger.warning(f"[GoalTracking] Failed to seed requirements ledger: {seed_err}")

            # ── PHASE 3: VALIDATION (Hallucination Guard + Dropped Recovery) ──
            # Cross-reference regex candidates against LLM criteria.
            # The validator tags each requirement with source attribution
            # (regex/llm/both/llm_unverified) and recovers dropped signals.
            validated = None
            source_map = {}
            if candidates and goal.success_criteria:
                try:
                    from python.helpers.extraction_validator import validate_extraction
                    validated = validate_extraction(first_prompt, candidates, goal.success_criteria)
                    # Build source map for ledger attribution
                    if validated:
                        for item in validated:
                            source = getattr(item, 'source', 'regex') if hasattr(item, 'source') else 'regex'
                            source_map[item.text] = source
                        logger.info(
                            f"[GoalTracking] Hybrid validation: {len(validated)} validated items "
                            f"({len(source_map)} with source attribution)"
                        )
                except Exception as val_err:
                    logger.warning(f"[GoalTracking] Extraction validation failed (non-fatal): {val_err}")

            # ── PHASE 4: MERGE INTO LEDGER (with source attribution) ──
            # Use validated items if available, otherwise fall back to raw extraction
            try:
                from python.helpers.requirements_ledger import merge_line_items_into_ledger
                if validated:
                    # Validated items from hybrid pipeline
                    from python.helpers.prompt_line_item_extractor import LineItem
                    line_items = []
                    for i, item in enumerate(validated):
                        li = LineItem(
                            id=f"LI-V{i+1:03d}",
                            text=item.text,
                            category=getattr(item, 'category', 'feature'),
                            source_line=getattr(item, 'source_line', 0),
                        )
                        line_items.append(li)
                    added = merge_line_items_into_ledger(
                        agent.data, line_items, source_metadata=source_map
                    )
                    logger.info(
                        f"[GoalTracking] Hybrid merge: {len(line_items)} validated items, "
                        f"{added} new requirements merged into ledger (with source attribution)"
                    )
                elif candidates:
                    # Fallback: use raw weighted candidates (no LLM validation)
                    from python.helpers.prompt_line_item_extractor import LineItem
                    line_items = []
                    for i, c in enumerate(candidates):
                        li = LineItem(
                            id=f"LI-C{i+1:03d}",
                            text=c.text,
                            category=c.category,
                            source_line=getattr(c, 'source_line', 0),
                        )
                        line_items.append(li)
                    added = merge_line_items_into_ledger(agent.data, line_items)
                    logger.info(
                        f"[GoalTracking] Fallback merge: {len(line_items)} candidates, "
                        f"{added} new requirements merged"
                    )
                else:
                    # Legacy fallback: use old extract_line_items
                    from python.helpers.prompt_line_item_extractor import extract_line_items
                    line_items = extract_line_items(first_prompt)
                    if line_items:
                        added = merge_line_items_into_ledger(agent.data, line_items)
                        logger.info(
                            f"[GoalTracking] Legacy line-item extractor: {len(line_items)} extracted, "
                            f"{added} new requirements merged into ledger"
                        )
            except Exception as li_err:
                logger.warning(f"[GoalTracking] Line-item extraction/merge failed (non-fatal): {li_err}")

            # ── EXTRACTION METADATA (Observability) ──
            # Persist pipeline stats for downstream consumers and debugging
            agent.data["_extraction_metadata"] = {
                "regex_candidates": len(candidates) if candidates else 0,
                "llm_criteria": len(goal.success_criteria) if goal.success_criteria else 0,
                "validated_count": len(validated) if validated else 0,
                "source_distribution": _count_sources(source_map) if source_map else {},
                "pipeline": "hybrid" if candidates else "fallback",
            }

            # ── FEATURE SEEDING + REGISTRY (Phase 3 + ADR-007) ──────
            # extract_features is expensive — call ONCE and share the result
            # between feature seeding and feature registry classification.
            try:
                from python.helpers.prompt_contract_parser import extract_features
                from python.helpers.requirements_ledger import seed_features_into_ledger
                pdv_features = extract_features(first_prompt)

                # Feature seeding: merge structural requirements into ledger
                if pdv_features:
                    feat_added = seed_features_into_ledger(agent.data, pdv_features)
                    logger.info(
                        f"[GoalTracking] Feature seeding: {len(pdv_features)} extracted, "
                        f"{feat_added} new requirements merged into ledger"
                    )

                # Feature registry: classify into ADR-007 waves (CORE → INFRA → SALES)
                if pdv_features:
                    try:
                        from python.helpers.feature_registry import classify_features
                        feature_names = [f["name"] for f in pdv_features]
                        classified = classify_features(feature_names)
                        agent.data["_feature_registry"] = [
                            {"id": f.id, "name": f.name, "category": f.category,
                             "priority": f.priority, "status": f.status}
                            for f in classified
                        ]
                        logger.info(
                            f"[GoalTracking] Feature registry activated: {len(classified)} features "
                            f"classified (ADR-007 wave ordering enabled)"
                        )
                    except Exception as fr_err:
                        logger.warning(f"[GoalTracking] Feature registry activation failed (non-fatal): {fr_err}")

            except Exception as feat_err:
                logger.warning(f"[GoalTracking] PDV feature seeding failed (non-fatal): {feat_err}")

        except Exception as e:
            logger.warning(f"[GoalTracking] Failed to create goal: {e}")
        
        return loop_data


def _count_sources(source_map: dict) -> dict:
    """Count source attribution distribution."""
    counts = {}
    for source in source_map.values():
        counts[source] = counts.get(source, 0) + 1
    return counts


