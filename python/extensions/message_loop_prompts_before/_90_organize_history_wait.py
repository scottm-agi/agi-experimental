from __future__ import annotations
from python.helpers.extension import Extension
from python.agent import LoopData
from python.extensions.message_loop_end._10_organize_history import DATA_NAME_TASK
import asyncio
import logging

logger = logging.getLogger("agix.organize_history_wait")

# F-4 (RCA-467): Maximum compression attempts before breaking out of the
# while loop.  Prevents infinite stall when LLM summarization fails to
# reduce token count.  The agent continues with an oversized context
# (which may cause an API error, but is better than permanent stalling).
MAX_COMPRESS_ATTEMPTS = 20


class OrganizeHistoryWait(Extension):
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):

        # sync action only required if the history is too large, otherwise leave it in background
        compress_attempts = 0
        while self.agent.history.is_over_limit():
            compress_attempts += 1
            if compress_attempts > MAX_COMPRESS_ATTEMPTS:
                logger.critical(
                    f"[HISTORY_STALL] Compression failed to reduce history below "
                    f"limit after {MAX_COMPRESS_ATTEMPTS} attempts. Breaking out "
                    f"to prevent infinite stall. Agent will continue with "
                    f"oversized context."
                )
                break

            # get task
            task = self.agent.get_data(DATA_NAME_TASK)

            # Check if the task is already done
            if task:
                if not task.done():
                    self.agent.context.log.set_progress("Compressing history...")

                # Wait for the task to complete
                await task

                # Clear the coroutine data after it's done
                self.agent.set_data(DATA_NAME_TASK, None)
            else:
                # no task running, start and wait
                self.agent.context.log.set_progress("Compressing history...")
                await self.agent.history.compress()

