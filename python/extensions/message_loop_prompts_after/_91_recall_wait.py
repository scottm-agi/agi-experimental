from __future__ import annotations
import asyncio
import logging
from python.helpers.extension import Extension
from python.agent import LoopData
from python.extensions.message_loop_prompts_after._50_recall_memories import DATA_NAME_TASK as DATA_NAME_TASK_MEMORIES, DATA_NAME_ITER as DATA_NAME_ITER_MEMORIES
# from python.extensions.message_loop_prompts_after._51_recall_solutions import DATA_NAME_TASK as DATA_NAME_TASK_SOLUTIONS
from python.helpers import settings

logger = logging.getLogger("agix.recall_wait")

# Maximum time to wait for memory recall before proceeding without memories.
# Calibrated to the recall pipeline budget:
#   - call_utility_model() query generation: up to 60s per attempt (agent.py:1798)
#   - Embedding model init / FAISS search: ~5-15s
#   - Optional post-filter LLM call: up to 60s (worst case)
# Previous value of 30s was SHORTER than the utility model timeout (60s),
# causing successful but slow LLM responses to be killed before returning.
# (RCA-238: commit 037aa834 overcorrected from 60→30s)
RECALL_TIMEOUT_SECONDS = 90

class RecallWait(Extension):
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):

        set = settings.get_settings()

        task = self.agent.get_data(DATA_NAME_TASK_MEMORIES)
        iter = self.agent.get_data(DATA_NAME_ITER_MEMORIES) or 0

        if task and not task.done():

            # if memory recall is set to delayed mode, do not await on the iteration it was called
            if set["memory_recall_delayed"]:
                if iter == loop_data.iteration:
                    # insert info about delayed memory to extras
                    delay_text = self.agent.read_prompt("memory.recall_delay_msg.md")
                    loop_data.extras_temporary["memory_recall_delayed"] = delay_text
                    return
            
            # Await with timeout to prevent infinite hangs (Issue: #1153 stuck at "Building prompt")
            try:
                await asyncio.wait_for(task, timeout=RECALL_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                logger.warning(
                    f"[{self.agent.agent_name}] Memory recall timed out after {RECALL_TIMEOUT_SECONDS}s. "
                    f"Proceeding without memories. This may happen during concurrent agent processing."
                )
                self.agent.context.log.log(
                    type="warning",
                    heading="⏰ Memory recall timed out",
                    content=f"Memory search took longer than {RECALL_TIMEOUT_SECONDS}s. Proceeding without recalled memories.",
                )
                # Cancel the hung task to free resources
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        # task = self.agent.get_data(DATA_NAME_TASK_SOLUTIONS)
        # if task and not task.done():
        #     # self.agent.context.log.set_progress("Recalling solutions...")
        #     try:
        #         await asyncio.wait_for(task, timeout=RECALL_TIMEOUT_SECONDS)
        #     except asyncio.TimeoutError:
        #         logger.warning(f"[{self.agent.agent_name}] Solutions recall timed out after {RECALL_TIMEOUT_SECONDS}s.")
        #         task.cancel()

