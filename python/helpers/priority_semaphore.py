from __future__ import annotations
import asyncio
from enum import IntEnum
from collections import deque
from typing import Dict, Deque, Optional, Any
from contextlib import asynccontextmanager

class Priority(IntEnum):
    """Priority levels for resource acquisition."""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3

class PrioritySemaphore:
    """
    A semaphore that allows prioritized acquisition.
    Higher priority (lower integer value) waiters are served first.
    """
    
    def __init__(self, value: int = 1):
        """
        Initialize priority semaphore.
        
        Args:
            value: Number of concurrent units allowed.
        """
        if value < 0:
            raise ValueError("Semaphore initial value must be >= 0")
        self._value = value
        # Dict of deques for each priority level to maintain FIFO within same priority
        self._waiters: Dict[Priority, Deque[asyncio.Future]] = {
            p: deque() for p in Priority
        }

    @property
    def available_units(self) -> int:
        """Return the number of currently available units."""
        return self._value

    async def acquire(self, priority: Priority = Priority.NORMAL) -> bool:
        """
        Acquire a semaphore unit with the given priority.
        
        Args:
            priority: The priority level for this acquisition.
            
        Returns:
            Always returns True (consistent with asyncio.Semaphore.acquire).
        """
        if self._value > 0:
            self._value -= 1
            return True

        # Create a future and wait for release to wake us up
        fut = asyncio.get_running_loop().create_future()
        self._waiters[priority].append(fut)
        
        try:
            await fut
            return True
        except asyncio.CancelledError:
            # If cancelled while waiting, remove from queue
            if fut in self._waiters[priority]:
                self._waiters[priority].remove(fut)
            # If the future was already set (woken up), but we got cancelled before returning,
            # we must pass the unit to the next waiter
            if fut.done() and not fut.cancelled():
                self.release()
            raise

    def release(self) -> None:
        """
        Release a semaphore unit.
        Wakes the highest priority waiter.
        """
        self._value += 1
        self._wake_next()

    def _wake_next(self) -> None:
        """Wake the highest priority waiting task if units are available."""
        if self._value <= 0:
            return

        # Check priorities in order: CRITICAL > HIGH > NORMAL > LOW
        for p in sorted(Priority):
            while self._waiters[p]:
                fut = self._waiters[p].popleft()
                if not fut.done():
                    self._value -= 1
                    fut.set_result(True)
                    return
                # If fut is already done (e.g. cancelled), move to next in this level or next level

    @asynccontextmanager
    async def priority(self, priority: Priority = Priority.NORMAL):
        """
        Asynchronous context manager for prioritized acquisition.
        
        Args:
            priority: The priority level for this acquisition.
        """
        await self.acquire(priority)
        try:
            yield self
        finally:
            self.release()

    def get_queue_depths(self) -> Dict[str, int]:
        """Return current queue depths for each priority."""
        return {p.name: len(self._waiters[p]) for p in Priority}
