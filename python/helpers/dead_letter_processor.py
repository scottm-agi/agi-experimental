from __future__ import annotations
import asyncio
import logging
from enum import Enum
from typing import Optional, List
from python.helpers.message_queue import MessageQueue, Task, TaskStatus

logger = logging.getLogger(__name__)

class ProcessingAction(Enum):
    """Actions to take for a dead letter task."""
    RETRY = "retry"
    DISCARD = "discard"
    ESCALATE = "escalate"

class DeadLetterProcessor:
    """
    Automated processor for tasks in the dead letter queue.
    Implements policies for re-enqueueing or discarding permanently failed tasks.
    """
    
    def __init__(self, message_queue: MessageQueue):
        """
        Initialize the dead letter processor.
        
        Args:
            message_queue: The message queue instance to monitor.
        """
        self.queue = message_queue
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def determine_action(self, task: Task) -> ProcessingAction:
        """
        Determine the appropriate action for a dead letter task based on its error.
        
        Args:
            task: The task in the dead letter queue.
            
        Returns:
            ProcessingAction to perform.
        """
        error_msg = (task.error or "").lower()
        
        # Policy 1: Fatal errors should be discarded
        if any(fatal in error_msg for fatal in ["fatal", "invalid payload", "not found", "permission denied"]):
            return ProcessingAction.DISCARD
            
        # Policy 2: Recoverable errors (timeouts, network) can be retried if less than a threshold
        # For dead letters, they already exceeded max_retries. 
        # But we might want to try one last time with a higher timeout or different priority.
        if any(recoverable in error_msg for recoverable in ["timeout", "connection", "rate limit", "overloaded"]):
            return ProcessingAction.RETRY
            
        # Default policy: Escalate for manual review
        return ProcessingAction.ESCALATE

    async def process_task(self, task: Task, action: ProcessingAction) -> bool:
        """
        Process a single task according to the specified action.
        
        Args:
            task: The task to process.
            action: Action to perform.
            
        Returns:
            True if processed successfully.
        """
        try:
            if action == ProcessingAction.RETRY:
                logger.info(f"Retrying task {task.id} from dead letter queue")
                # We could modify task properties here (e.g. increase timeout)
                task.timeout *= 2
                await self.queue.retry_failed_task(task)
                return True
                
            elif action == ProcessingAction.DISCARD:
                logger.warning(f"Discarding task {task.id} from dead letter queue: {task.error}")
                # We simply don't re-enqueue it. 
                # In a real system, we might archive it to another storage.
                return True
                
            elif action == ProcessingAction.ESCALATE:
                logger.error(f"ESCALATION: Task {task.id} requires manual review: {task.error}")
                # This could trigger an alert or move to a special 'manual-review' queue
                return True
                
            return False
        except Exception as e:
            logger.error(f"Error processing dead letter task {task.id}: {e}")
            return False

    async def scan_and_process(self, batch_size: int = 10):
        """
        Scan the dead letter queue and process tasks in batches.
        """
        tasks = await self.queue.get_dead_letters(count=batch_size)
        processed_count = 0
        
        for task in tasks:
            action = await self.determine_action(task)
            if await self.process_task(task, action):
                # If we successfully re-enqueued or handled it, 
                # we should remove it from the dead letter stream.
                # Note: get_dead_letters uses xrange, so it doesn't remove.
                # We might need a way to 'acknowledge' dead letters if using consumer groups for them too.
                processed_count += 1
        
        if processed_count > 0:
            logger.info(f"DeadLetterProcessor handled {processed_count} tasks")

    async def run_loop(self, interval: int = 60):
        """
        Background loop for periodic processing.
        """
        self._running = True
        logger.info("Starting DeadLetterProcessor background loop")
        while self._running:
            try:
                await self.scan_and_process()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                logger.debug("[DeadLetterProcessor] Run loop cancelled — shutting down gracefully")
                break
            except Exception as e:
                logger.error(f"Error in DeadLetterProcessor loop: {e}")
                await asyncio.sleep(10) # Wait before retry

    def start(self, interval: int = 60):
        """Start the processor in the background."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self.run_loop(interval))

    def stop(self):
        """Stop the background processor."""
        self._running = False
        if self._task:
            self._task.cancel()
