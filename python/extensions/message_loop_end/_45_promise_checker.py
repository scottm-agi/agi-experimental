from __future__ import annotations
"""
Mid-Turn Promise Checker extension.

This extension runs after each LLM turn and checks if the agent's 
associated task has a completion promise that hasn't been met yet.
If the promise is missing, it triggers a continuation within the same monologue.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from python.agent import Agent, LoopData

logger = logging.getLogger(__name__)

async def execute(agent: "Agent", loop_data: "LoopData", **kwargs):
    """
    Check for completion promise in the last turn's output.
    """
    # 1. Get context and task ID
    if not hasattr(agent, 'context') or not agent.context:
        return
    
    context_id = getattr(agent.context, 'id', None)
    if not context_id:
        return

    # 2. Try to get the task from the scheduler
    try:
        from python.helpers.task_scheduler import TaskScheduler
        scheduler = TaskScheduler.get()
        task = scheduler.get_task_by_uuid(context_id)
        
        if not task or not hasattr(task, 'completion_promise') or not task.completion_promise:
            return

        # 3. Check if the promise is in the last turn's output
        # loop_data.last_turn_output is expected to be the string content of the last AI message
        last_output = getattr(loop_data, 'last_turn_output', "")
        if not last_output and hasattr(agent, 'history') and agent.history.current:
            # Fallback to history if loop_data doesn't have it
            last_msg = agent.history.current.messages[-1]
            if last_msg.role == "assistant":
                last_output = last_msg.message

        if task.completion_promise not in last_output:
            # 4. Promise missing - trigger continuation
            logger.info(f"Promise '{task.completion_promise}' missing from turn output for task {task.uuid}. Triggering continuation.")
            
            # Set continuation flags in loop_data
            # These flags are used by agent.monologue() to decide whether to loop again
            loop_data.do_continue = True
            
            # Inject a nudge message for the next turn
            nudge = f"\n\n[SYSTEM NUDGE]: You have not yet output the completion promise '{task.completion_promise}'. You must continue until the task is fully complete and the promise is included in your final response."
            
            if not hasattr(loop_data, 'continuation_hint'):
                loop_data.continuation_hint = nudge
            else:
                loop_data.continuation_hint += nudge

    except Exception as e:
        logger.error(f"Error in promise checker extension: {e}")