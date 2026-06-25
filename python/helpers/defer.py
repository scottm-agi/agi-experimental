from __future__ import annotations
import asyncio
from dataclasses import dataclass
import threading
from concurrent.futures import Future
from typing import Any, Callable, Optional, Coroutine, TypeVar, Awaitable, Dict
from python.helpers import context as context_helper

T = TypeVar("T")

class EventLoopThread:
    def __init__(self, thread_name: str = "Background") -> None:
        """Initialize the event loop thread."""
        self.thread_name = thread_name
        self.loop = None
        self.thread = None
        # Explicitly call the method to ensure it's bound
        self._start()

    def _start(self):
        if not hasattr(self, "loop") or not self.loop:
            self.loop = asyncio.new_event_loop()
        if not hasattr(self, "thread") or not self.thread:
            self.thread = threading.Thread(
                target=self._run_event_loop, daemon=True, name=self.thread_name
            )
            self.thread.start()

    def _run_event_loop(self):
        if not self.loop:
            raise RuntimeError("Event loop is not initialized")
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def terminate(self):
        if self.loop and self.loop.is_running():
            self.loop.stop()
        self.loop = None
        self.thread = None

    def run_coroutine(self, coro):
        self._start()
        if not self.loop:
            raise RuntimeError("Event loop is not initialized")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


class AgentThreadPool:
    """A limited pool of EventLoopThreads for agent background tasks."""
    _instances: Dict[int, 'AgentThreadPool'] = {}
    _lock = threading.Lock()

    def __init__(self, size: int = 5):
        self.size = size
        self.threads = [EventLoopThread(f"AgentPool-{i}") for i in range(size)]
        self._counter = 0

    @classmethod
    def get_instance(cls, size: int = 5) -> 'AgentThreadPool':
        with cls._lock:
            try:
                loop = asyncio.get_running_loop()
                loop_id = id(loop)
            except RuntimeError:
                loop_id = 0
            
            if loop_id not in cls._instances:
                cls._instances[loop_id] = cls(size)
            return cls._instances[loop_id]

    def get_thread(self) -> EventLoopThread:
        """Get the next available thread in the pool (Round-robin)."""
        thread = self.threads[self._counter % self.size]
        self._counter += 1
        return thread


@dataclass
class ChildTask:
    task: "DeferredTask"
    terminate_thread: bool


class DeferredTask:
    def __init__(
        self,
        thread_name: str = "Background",
    ):
        if thread_name == "Background":
            # Shared pool for general background tasks
            self.event_loop_thread = AgentThreadPool.get_instance().get_thread()
        else:
            # Dedicated thread if name specified (e.g. from pool)
            self.event_loop_thread = EventLoopThread(thread_name)
        self._future: Optional[Future] = None
        self.children: list[ChildTask] = []
        
        # Capture current agent context ID to propagate to the background thread
        # This ensures AgentContext.current() works correctly in agents/tools/extensions
        self.agent_context_id = context_helper.get_context_data("agent_context_id")

    def start_task(
        self, func: Callable[..., Coroutine[Any, Any, Any]], *args: Any, **kwargs: Any
    ):
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self._start_task()
        return self

    def __del__(self):
        self.kill()

    def _start_task(self):
        self._future = self.event_loop_thread.run_coroutine(self._run())

    async def _run(self):
        if self.agent_context_id:
            context_helper.set_context_data("agent_context_id", self.agent_context_id)
        return await self.func(*self.args, **self.kwargs)

    def is_ready(self) -> bool:
        return self._future.done() if self._future else False

    def result_sync(self, timeout: Optional[float] = None) -> Any:
        if not self._future:
            raise RuntimeError("Task hasn't been started")
        try:
            return self._future.result(timeout)
        except TimeoutError:
            raise TimeoutError(
                "The task did not complete within the specified timeout."
            )

    async def result(self, timeout: Optional[float] = None) -> Any:
        if not self._future:
            raise RuntimeError("Task hasn't been started")

        loop = asyncio.get_running_loop()

        def _get_result():
            try:
                result = self._future.result(timeout)  # type: ignore
                # self.kill()
                return result
            except TimeoutError:
                raise TimeoutError(
                    "The task did not complete within the specified timeout."
                )

        return await loop.run_in_executor(None, _get_result)

    def kill(self, terminate_thread: bool = False) -> None:
        """Kill the task and optionally terminate its thread."""
        self.kill_children()
        if self._future and not self._future.done():
            self._future.cancel()

        if (
            terminate_thread
            and self.event_loop_thread.loop
            and self.event_loop_thread.loop.is_running()
        ):

            def cleanup():
                tasks = [
                    t
                    for t in asyncio.all_tasks(self.event_loop_thread.loop)
                    if t is not asyncio.current_task(self.event_loop_thread.loop)
                ]
                for task in tasks:
                    task.cancel()
                    try:
                        # Give tasks a chance to cleanup
                        if self.event_loop_thread.loop:
                            self.event_loop_thread.loop.run_until_complete(
                                asyncio.gather(task, return_exceptions=True)
                            )
                    except Exception:
                        pass  # Ignore cleanup errors

            self.event_loop_thread.loop.call_soon_threadsafe(cleanup)
            self.event_loop_thread.terminate()

    def kill_children(self) -> None:
        for child in self.children:
            child.task.kill(terminate_thread=child.terminate_thread)
        self.children = []

    def is_alive(self) -> bool:
        return self._future and not self._future.done()  # type: ignore

    def restart(self, terminate_thread: bool = False) -> None:
        self.kill(terminate_thread=terminate_thread)
        self._start_task()

    def add_child_task(
        self, task: "DeferredTask", terminate_thread: bool = False
    ) -> None:
        self.children.append(ChildTask(task, terminate_thread))

    async def _execute_in_task_context(
        self, func: Callable[..., T], *args, **kwargs
    ) -> T:
        """Execute a function in the task's context and return its result."""
        result = func(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result

    def execute_inside(self, func: Callable[..., T], *args, **kwargs) -> Awaitable[T]:
        if not self.event_loop_thread.loop:
            raise RuntimeError("Event loop is not initialized")

        future: Future = Future()

        async def wrapped():
            if not self.event_loop_thread.loop:
                raise RuntimeError("Event loop is not initialized")
            try:
                result = await self._execute_in_task_context(func, *args, **kwargs)
                # Keep awaiting until we get a concrete value
                while isinstance(result, Awaitable):
                    result = await result
                self.event_loop_thread.loop.call_soon_threadsafe(
                    future.set_result, result
                )
            except Exception as e:
                self.event_loop_thread.loop.call_soon_threadsafe(
                    future.set_exception, e
                )

        asyncio.run_coroutine_threadsafe(wrapped(), self.event_loop_thread.loop)
        return asyncio.wrap_future(future)


class LazyLoader:
    """
    Defer importing a module until it's actually used.
    Used for heavy modules like transformers, torch, faiss.
    """

    def __init__(self, module_name: str):
        self._module_name = module_name
        self._module = None

    def _load(self):
        if self._module is None:
            import importlib
            self._module = importlib.import_module(self._module_name)
        return self._module

    def __getattr__(self, item):
        module = self._load()
        return getattr(module, item)

    def __dir__(self):
        module = self._load()
        return dir(module)

    def __repr__(self):
        return f"<LazyLoader for {self._module_name}>"


class MemoryCache:
    """
    Simple TTL-based in-memory cache.
    """

    def __init__(self, default_ttl: float = 60.0):
        self.default_ttl = default_ttl
        self._cache: dict[Any, tuple[Any, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any:
        import time
        with self._lock:
            if key in self._cache:
                value, expiry = self._cache[key]
                if expiry > time.time():
                    return value
                else:
                    del self._cache[key]
        return None

    def set(self, key: Any, value: Any, ttl: Optional[float] = None) -> None:
        import time
        duration = ttl if ttl is not None else self.default_ttl
        with self._lock:
            self._cache[key] = (value, time.time() + duration)

    def invalidate(self, key: Optional[Any] = None) -> None:
        with self._lock:
            if key is not None:
                self._cache.pop(key, None)
            else:
                self._cache.clear()

    def memoize(self, ttl: Optional[float] = None):
        """Decorator for memoizing functions."""
        def decorator(func):
            def wrapper(*args, **kwargs):
                # Create a cache key from function name and arguments
                # Note: This is a simple hashable key. For complex args, this might need adjustment.
                key = (func.__module__, func.__name__, args, frozenset(kwargs.items()))
                cached = self.get(key)
                if cached is not None:
                    return cached
                result = func(*args, **kwargs)
                self.set(key, result, ttl)
                return result
            return wrapper
        return decorator
