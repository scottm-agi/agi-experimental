"""Lesson Extraction — monologue_end extension.

Extracts lessons from agent task completion for ALL agents (not just supervisor).
Uses the LessonsLearnedEngine for structured, deduplicated lesson storage.
After extraction, triggers the auto-promotion pipeline to promote eligible
lessons to global rules.

Hooks into: monologue_end (order 52)
"""
from __future__ import annotations

import logging
from python.helpers.extension import Extension
from python.helpers.lessons_learned import LessonsLearnedEngine
from python.agent import LoopData

logger = logging.getLogger("agix.extract_lessons")


class ExtractLessons(Extension):

    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        try:
            agent = self.agent
            agent_name = getattr(agent, 'agent_name', 'unknown')
            context_id = getattr(agent.context, 'id', '') if hasattr(agent, 'context') else ''
            
            # Only extract on final iteration (when loop ends)
            if not getattr(loop_data, 'final', True):
                return

            engine = LessonsLearnedEngine()
            await engine.load_lessons_from_storage()

            # Get intervention/iteration stats from agent data
            interventions = agent.data.get('_supervisor_interventions', 0)
            iterations = agent.data.get('_iteration_count', 0)
            task_desc = agent.data.get('_current_task', '')
            patterns = agent.data.get('_detected_patterns', [])

            if not task_desc:
                return

            lesson = await engine.extract_lesson_from_task_completion(
                agent_id=agent_name,
                context_id=context_id,
                task_description=task_desc,
                patterns_encountered=patterns if isinstance(patterns, list) else [],
                interventions_applied=interventions if isinstance(interventions, int) else 0,
                escalations=0,
                total_iterations=iterations if isinstance(iterations, int) else 0,
                success=True,
            )

            if lesson:
                logger.info(f"Lesson extracted for {agent_name}: {lesson.title}")

            # Auto-promote eligible lessons to global rules
            try:
                promoted = await engine.promote_to_rules(
                    min_occurrences=3,
                    min_success_rate=0.8,
                )
                if promoted > 0:
                    logger.info(f"Auto-promoted {promoted} lesson(s) to global rules")
            except Exception as e:
                logger.debug(f"Rule promotion check skipped: {e}")

        except Exception as e:
            logger.debug(f"Lesson extraction skipped: {e}")

