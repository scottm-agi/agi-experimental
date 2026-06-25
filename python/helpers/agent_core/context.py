"""
AgentContext class for agent_core package.

Contains the AgentContext class which manages agent lifecycle, state,
logging, and communication between agents and the UI.
"""
from __future__ import annotations

import asyncio
import gc
import logging
import os
import random
import re
import string
import threading
from collections import OrderedDict, deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Coroutine, Optional

from .base import AgentContextType

if TYPE_CHECKING:
    from python.agent import Agent
    from .config import AgentConfig, UserMessage
    import python.helpers.log as Log

logger = logging.getLogger(__name__)


# ─── F-1: Control Command Classifier (ITR-34 → DETERMINISTIC FIX) ────
# CANONICAL patterns now live in user_intent_patterns.py (single source of truth).
# This eliminates the divergent regex sets that caused 8 failed fix attempts.

# Pause patterns remain local — they're only used here, not by _02_user_stop_directive
_PAUSE_PHRASES = re.compile(
    r'\bwait\s+for\s+(my|further|future)\s+(commands?|instructions?)\b'
    r'|\bhold\s+on\b'
    r'|\bpause\s+for\s+now\b'
    r'|\bwait\s+a\s+(moment|second|minute|bit)\b',
    re.IGNORECASE | re.DOTALL
)


def _classify_user_message(msg) -> str:
    """Classify a user message as a control command or content.

    Returns:
        'control_stop'  — user wants everything to stop
        'control_pause' — user wants the system to wait
        'content'       — normal message (default)
    """
    if not msg or not isinstance(msg, str):
        return "content"

    text = str(msg).strip()
    if not text:
        return "content"

    # CANONICAL stop detection — single source of truth
    from python.helpers.user_intent_patterns import is_stop_directive
    if is_stop_directive(text):
        return "control_stop"

    # Check pause patterns (local — only used by context.py)
    if _PAUSE_PHRASES.search(text):
        return "control_pause"

    return "content"


# ─── F-3: Downward Intervention Propagation (ITR-34) ─────────────────────
# When a user sends a stop/pause command, propagate the paused state
# DOWN to all active subordinates (opposite of current UP-only behavior).

def _propagate_pause_to_subordinates(agent, user_stop: bool = False) -> None:
    """Walk DOWN the subordinate chain and set paused=True on each.

    Args:
        agent: The agent whose subordinates should be paused.
              Expects agent.data dict with optional '_subordinate' key.
        user_stop: If True, also set _user_stop_directive on each
                   subordinate's data dict. This flag is permanent and
                   prevents auto-resume timers from overriding the stop.
    """
    if not agent or not hasattr(agent, 'data'):
        return

    subordinate = agent.data.get("_subordinate", None)
    if subordinate is None:
        return

    # Pause the immediate subordinate
    if hasattr(subordinate, 'context') and subordinate.context:
        subordinate.context.paused = True
        # F-5: Set permanent stop directive on subordinate data
        if user_stop and hasattr(subordinate, 'data'):
            subordinate.data["_user_stop_directive"] = True
        logger.info(
            f"[USER DIRECTIVE] Propagated {'STOP' if user_stop else 'pause'} "
            f"to subordinate {getattr(subordinate, 'agent_name', 'unknown')}"
        )

    # Recurse into deeper subordinates
    _propagate_pause_to_subordinates(subordinate, user_stop=user_stop)


def _kill_subordinate_tasks(agent) -> None:
    """Walk DOWN the subordinate chain and KILL all tasks immediately.

    RCA-ITR41: Unlike _propagate_pause_to_subordinates (which sets paused=True
    and gets defeated by auto-resume timers), this function directly cancels
    the asyncio tasks running subordinate monologues. No pause, no waiting.

    Args:
        agent: The agent whose subordinates should be killed.
    """
    if not agent or not hasattr(agent, 'data'):
        return

    subordinate = agent.data.get("_subordinate", None)
    if subordinate is None:
        return

    # Set stop directive on subordinate
    if hasattr(subordinate, 'data'):
        subordinate.data["_user_stop_directive"] = True

    # Kill the subordinate's running task
    if hasattr(subordinate, 'context') and subordinate.context:
        subordinate.context.paused = False  # Clear any pause first
        if subordinate.context.task and subordinate.context.task.is_alive():
            try:
                subordinate.context.task.kill()
                logger.warning(
                    f"[USER DIRECTIVE] KILLED subordinate task: "
                    f"{getattr(subordinate, 'agent_name', 'unknown')}"
                )
            except Exception as e:
                logger.debug(f"[USER DIRECTIVE] Kill failed for subordinate: {e}")

    # Recurse into deeper subordinates
    _kill_subordinate_tasks(subordinate)


class AgentContext:
    """
    Context management for agent instances.
    
    AgentContext manages the lifecycle, state, and communication channels
    for agent instances. It provides:
    - Global context registry and lookup
    - Heartbeat management for liveness detection
    - Data storage (in-memory and persistent)
    - Log management via Log.Log
    - State tracking (paused, streaming, etc.)
    - Task execution via DeferredTask
    - Project/environment context
    
    Class Attributes:
        _contexts: Global registry of all active contexts by ID
        _creation_locks: Locks for thread-safe context creation
        _global_version: Version counter for change detection
        _counter: Counter for context numbering
        _notification_manager: Shared notification manager instance
    """

    MAX_CONTEXTS_IN_MEMORY: ClassVar[int] = int(os.environ.get("AGIX_MAX_CONTEXTS", "25"))
    _contexts: OrderedDict[str, "AgentContext"] = OrderedDict()
    # Lightweight metadata registry for ALL contexts (never evicted).
    # Ensures sidebar always shows all chats even when full agent objects
    # are LRU-evicted from _contexts to save memory. (Forgejo #1019 fix)
    _context_metadata: dict[str, dict] = {}
    _creation_locks: dict[str, threading.Lock] = {}
    _per_context_locks: dict[str, threading.Lock] = {}
    _creation_lock_mutex = threading.Lock()
    _global_version: ClassVar[int] = 0
    _counter: int = 0
    _notification_manager = None

    @classmethod
    def _increment_version(cls):
        """Increment the global contexts version."""
        cls._global_version += 1

    def __init__(
        self,
        config: "AgentConfig | None" = None,
        id: str | None = None,
        name: str | None = None,
        agent0: "Agent | None" = None,
        log: "Log.Log | None" = None,
        paused: bool = False,
        streaming_agent: "Agent | None" = None,
        created_at: datetime | None = None,
        type: AgentContextType = AgentContextType.USER,
        last_message: datetime | None = None,
        data: dict | None = None,
        output_data: dict | None = None,
        parent_id: str | None = None,
        set_current: bool = False,
        skip_agent_init: bool = False,  # Skip default agent creation
        skip_version_increment: bool = False,  # Skip version increment for bulk loading
    ):
        """
        Initialize an AgentContext instance.
        
        Args:
            config: Agent configuration. If None, will initialize default config.
            id: Unique context ID. If None, will be auto-generated.
            name: Human-readable name for the context.
            agent0: Pre-created root agent. If None, will be created.
            log: Log instance. If None, will be created.
            paused: Whether the context starts paused.
            streaming_agent: Currently streaming agent reference.
            created_at: Creation timestamp. If None, uses current time.
            type: Context type (USER, TASK, BACKGROUND).
            last_message: Timestamp of last message.
            data: Initial data dictionary.
            output_data: Initial output data dictionary.
            parent_id: ID of parent context (for hierarchical contexts).
            set_current: Whether to set this as the current context.
            skip_agent_init: Skip default agent creation (for deserialization).
            skip_version_increment: Skip version increment (for bulk loading).
        """
        import python.helpers.log as Log
        from python.helpers.defer import DeferredTask
        from python.helpers.crash_recovery import get_crash_recovery
        
        # Initialize context ID first
        self.id = id or AgentContext.generate_id()
        existing = AgentContext.get(self.id)
        if existing:
            AgentContext.remove(self.id)

        # Initialize state
        self.name = name
        
        # Ensure config exists, default to initialized agent if none provided
        if not config:
            from python.initialize import initialize_agent
            config = initialize_agent()
            
        self.config = config
        self.log = log or Log.Log()
        self.log.context = self
        
        self.paused = paused
        self.streaming_agent = streaming_agent
        self.task: DeferredTask | None = None
        self.created_at = created_at or datetime.now(timezone.utc)
        self.type = type
        AgentContext._counter += 1
        self.no = AgentContext._counter
        self.last_message = last_message or datetime.now(timezone.utc)
        self.data = data or {}
        self.output_data = output_data or {}
        self.parent_id = parent_id
        self.execution_state = "idle"  # Issue #1095: Track mid-execution for post-restart re-nudge

        # Default to 'default' project if not set
        if not self.get_data("project"):
            from python.helpers import projects
            projects.ensure_default_project_exists()
            self.set_data("project", "default")

        # Initialize agents last
        # Optimization: Skip default agent creation if we are about to deserialize agents
        if not skip_agent_init:
            from python.agent import Agent
            self.agent0 = agent0 or Agent(0, self.config, self)
        else:
            self.agent0 = agent0  # Might be None, caller must set it later

        # NOW register globally once fully initialized
        
        # Validation for Issue #278
        if not isinstance(self, AgentContext):
            import traceback
            logger.warning(f"CRITICAL: Non-AgentContext object being registered: {type(self)}")
            traceback.print_stack()

        AgentContext._contexts[self.id] = self
        AgentContext._contexts.move_to_end(self.id) # Mark as most recently used

        # Always register lightweight metadata (survives LRU eviction)
        AgentContext._context_metadata[self.id] = {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "type": self.type.value if hasattr(self.type, 'value') else str(self.type),
            "last_message": self.last_message,
            "project_name": self.get_data("project") or "default",
            "parent_id": self.parent_id,
            "no": self.no,
        }

        # Prune older contexts if we exceeded the limit (Forgejo #891)
        evicted_count = 0
        while len(AgentContext._contexts) > AgentContext.MAX_CONTEXTS_IN_MEMORY:
            # Pop the oldest item (FIFO in OrderedDict is LRU if we always move_to_end)
            oldest_id, oldest_ctx = AgentContext._contexts.popitem(last=False)
            logger.debug(f"Memory Pruning: Removing context {oldest_id} from memory to save space.")
            # Heartbeat task cancellation for pruned context
            if hasattr(oldest_ctx, '_heartbeat_task') and oldest_ctx._heartbeat_task:
                oldest_ctx._heartbeat_task.cancel()
            if oldest_ctx.task:
                oldest_ctx.task.kill()
            # Release agent references to free memory (Forgejo #891)
            oldest_ctx.agent0 = None
            oldest_ctx.streaming_agent = None
            oldest_ctx.log = None
            oldest_ctx.data = {}
            oldest_ctx.output_data = {}
            evicted_count += 1
        # Explicit GC after eviction batch to reclaim memory under high-load
        # scenarios (e.g., benchmark runs creating 100+ contexts rapidly).
        # Without this, Python's generational GC may not collect evicted
        # agent/log/config objects fast enough, causing memory pressure.
        if evicted_count > 0:
            gc.collect()
            logger.info(f"Memory Pruning: Evicted {evicted_count} context(s), {len(AgentContext._contexts)} remaining (limit={AgentContext.MAX_CONTEXTS_IN_MEMORY}).")
        if set_current:
            AgentContext.set_current(self.id)

        # Register with crash recovery
        get_crash_recovery().register_context(self)
        
        if not skip_version_increment:
            AgentContext._increment_version()
            
        # Server-side message queue for busy-agent queuing
        # When queue_if_busy=True in communicate(), messages are queued here
        # instead of being set as interventions (which get silently dropped).
        self._message_queue: deque = deque()
        # Safety limit to prevent unbounded growth.
        # Configurable via AGIX_MESSAGE_QUEUE_DEPTH env var (default: 50).
        try:
            _raw_depth = os.environ.get("AGIX_MESSAGE_QUEUE_DEPTH", "50")
            self.MAX_QUEUE_DEPTH: int = max(1, int(_raw_depth))
        except (ValueError, TypeError):
            self.MAX_QUEUE_DEPTH: int = 50

        # Initialize heartbeat
        self.last_heartbeat = datetime.now(timezone.utc).timestamp()
        self._heartbeat_task: asyncio.Task | None = None
        self._start_heartbeat_task()

    def _start_heartbeat_task(self):
        """Start the background heartbeat task."""
        try:
            loop = asyncio.get_running_loop()
            self._heartbeat_task = loop.create_task(self._heartbeat_loop())
        except RuntimeError:
            # Not running in an event loop (e.g. during some tests)
            pass

    async def _heartbeat_loop(self):
        """Background loop to update heartbeat."""
        try:
            while True:
                self.last_heartbeat = datetime.now(timezone.utc).timestamp()
                if self.agent0:
                    logger.debug(f"[HEARTBEAT] {self.agent0.agent_name} ({self.id}): {self.last_heartbeat}")
                await asyncio.sleep(30)  # Heartbeat every 30 seconds (increased from 60s for stability)
        except asyncio.CancelledError:
            logger.debug("[AgentContext] Heartbeat loop cancelled — shutting down gracefully")

    # ==========================================================================
    # STATIC CONTEXT MANAGEMENT METHODS
    # ==========================================================================

    @staticmethod
    def get(id: str) -> "AgentContext | None":
        """
        Get a context by ID.
        
        Args:
            id: The context ID to look up.
            
        Returns:
            The AgentContext instance or None if not found.
        """
        ctx = AgentContext._contexts.get(id, None)
        if ctx:
            if not isinstance(ctx, AgentContext):
                import traceback
                logger.error(
                    f"CRITICAL: AgentContext.get({id}) returned a non-instance object of type: {type(ctx)}. "
                    "Check for context pollution."
                )
                traceback.print_stack()
                return None
            
            # Move to end to mark as recently used (LRU logic)
            with AgentContext._creation_lock_mutex:
                 if id in AgentContext._contexts:
                     AgentContext._contexts.move_to_end(id)
        return ctx

    @staticmethod
    def use(id: str) -> "AgentContext | None":
        """
        Get a context by ID and set it as current.
        
        Args:
            id: The context ID to look up and activate.
            
        Returns:
            The AgentContext instance or None if not found.
        """
        context = AgentContext.get(id)
        if context:
            AgentContext.set_current(id)
        else:
            AgentContext.set_current("")
        return context

    @staticmethod
    def current() -> "AgentContext | None":
        """
        Get the current thread-local context.
        
        Returns:
            The current AgentContext or None if not set.
        """
        from python.helpers import context as context_helper
        
        ctxid = context_helper.get_context_data("agent_context_id", "")
        if not ctxid:
            return None
        
        ctx = AgentContext.get(ctxid)
        if ctx and not isinstance(ctx, AgentContext):
            import traceback
            logger.error(
                f"CRITICAL: AgentContext.current() returned a non-instance object of type: {type(ctx)} "
                f"for ID: {ctxid}. Check for context pollution."
            )
            traceback.print_stack()
            return None
        return ctx

    @staticmethod
    def set_current(ctxid: str):
        """
        Set the current thread-local context ID.
        
        Args:
            ctxid: The context ID to set as current.
        """
        from python.helpers import context as context_helper
        context_helper.set_context_data("agent_context_id", ctxid)

    @staticmethod
    def first() -> "AgentContext | None":
        """
        Get the first context in the registry.
        
        Returns:
            The first AgentContext or None if registry is empty.
        """
        if not AgentContext._contexts:
            return None
        return list(AgentContext._contexts.values())[0]

    @staticmethod
    def all() -> list["AgentContext"]:
        """
        Get all registered contexts.
        
        Returns:
            List of all AgentContext instances.
        """
        return list(AgentContext._contexts.values())

    @staticmethod
    def generate_id() -> str:
        """
        Generate a unique context ID.
        
        Returns:
            An 8-character alphanumeric ID not already in use.
        """
        def generate_short_id():
            return ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        
        while True:
            short_id = generate_short_id()
            if short_id not in AgentContext._contexts:
                return short_id

    @classmethod
    def get_creation_lock(cls, id: str) -> threading.Lock:
        """
        Get or create a lock for a specific context ID for thread-safe initialization.
        
        Args:
            id: The context ID to get a lock for.
            
        Returns:
            A threading.Lock for the specified context ID.
        """
        with cls._creation_lock_mutex:
            if id not in cls._creation_locks:
                cls._creation_locks[id] = threading.Lock()
            return cls._creation_locks[id]

    @classmethod
    def get_per_context_lock(cls, id: str) -> threading.Lock:
        """
        Get or create a threading.Lock for a specific context ID for thread-safe initialization.
        
        Args:
            id: The context ID to get a lock for.
            
        Returns:
            A threading.Lock for the specified context ID.
        """
        if not id:
            # For anonymous contexts, we use a separate lock or return the global mutex
            # but usually ctxid is provided for loading/creating persistent chats.
            return cls._creation_lock_mutex
            
        with cls._creation_lock_mutex:
            if id not in cls._per_context_locks:
                cls._per_context_locks[id] = threading.Lock()
            return cls._per_context_locks[id]

    @classmethod
    def get_notification_manager(cls):
        """
        Get the shared notification manager instance.
        
        Returns:
            The NotificationManager singleton.
        """
        if cls._notification_manager is None:
            from python.helpers.notification import NotificationManager
            cls._notification_manager = NotificationManager()
        return cls._notification_manager

    @staticmethod
    def remove(id: str) -> "AgentContext | None":
        """
        Remove a context from the registry.
        
        Args:
            id: The context ID to remove.
            
        Returns:
            The removed AgentContext or None if not found.
        """
        from python.helpers.crash_recovery import get_crash_recovery
        
        # Register in REMOVED_CONTEXTS to prevent re-saving by race conditions
        try:
            from python.helpers.persist_chat import REMOVED_CONTEXTS
            REMOVED_CONTEXTS.add(id)
        except ImportError:
            pass

        context = AgentContext._contexts.pop(id, None)
        # Also remove from metadata registry (full deletion, not just eviction)
        AgentContext._context_metadata.pop(id, None)
        if context:
            # Unregister from crash recovery
            get_crash_recovery().unregister_context(id)
            
            # Cancel heartbeat
            if hasattr(context, '_heartbeat_task') and context._heartbeat_task:
                context._heartbeat_task.cancel()
                
            if context.task:
                context.task.kill()

        # Issue #1070: Always increment version after removal so poll cache
        # is invalidated and frontend gets the updated context list immediately.
        AgentContext._increment_version()
        return context

    @staticmethod
    def register_metadata(meta: dict):
        """
        Register lightweight context metadata without creating a full AgentContext.
        
        Used during startup to populate _context_metadata from disk JSON
        before full deserialization, ensuring all chats appear in sidebar
        even if full context objects are LRU-evicted.
        
        Args:
            meta: Dict with keys: id, name, created_at, type, last_message,
                  project_name, parent_id (at minimum).
        """
        ctx_id = meta.get("id")
        if ctx_id:
            AgentContext._context_metadata[ctx_id] = meta

    @staticmethod
    def output_light_from_metadata(meta: dict) -> dict:
        """
        Generate a sidebar-compatible output dict from lightweight metadata.
        
        Used by poll API for contexts that are in _context_metadata but
        have been LRU-evicted from _contexts (no full agent objects in memory).
        
        Args:
            meta: Lightweight metadata dict from _context_metadata.
            
        Returns:
            Dict compatible with output_light() format for sidebar rendering.
        """
        from python.helpers.localization import Localization
        from python.helpers import projects

        created_at = meta.get("created_at")
        last_message = meta.get("last_message")
        
        # Handle both datetime objects and ISO strings
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except (ValueError, TypeError):
                created_at = datetime.fromtimestamp(0)
        if isinstance(last_message, str):
            try:
                last_message = datetime.fromisoformat(last_message)
            except (ValueError, TypeError):
                last_message = datetime.fromtimestamp(0)
        
        p_name = meta.get("project_name", "default")
        
        res = {
            "id": meta.get("id", ""),
            "name": meta.get("name"),
            "created_at": (
                Localization.get().serialize_datetime(created_at)
                if created_at
                else Localization.get().serialize_datetime(datetime.fromtimestamp(0))
            ),
            "no": meta.get("no", 0),
            "paused": False,  # Evicted contexts are not paused
            "last_message": (
                Localization.get().serialize_datetime(last_message)
                if last_message
                else Localization.get().serialize_datetime(datetime.fromtimestamp(0))
            ),
            "type": meta.get("type", "user"),
            "parent_id": meta.get("parent_id"),
            "project_name": p_name,
            "project": None,
        }
        
        # Resolve project for UI filtering
        p_header = projects.load_project_header(p_name)
        if p_header:
            res["project"] = {
                "name": p_name,
                "title": p_header.get("title", p_name),
                "color": p_header.get("color", ""),
            }
        
        return res

    # ==========================================================================
    # DATA ACCESS METHODS
    # ==========================================================================

    def get_data(self, key: str, recursive: bool = True) -> Any:
        """
        Get a value from the context data store.
        
        Args:
            key: The key to look up.
            recursive: Reserved for future context hierarchy support.
            
        Returns:
            The value or None if not found.
        """
        return self.data.get(key, None)

    def set_data(self, key: str, value: Any, recursive: bool = True):
        """
        Set a value in the context data store.
        
        Args:
            key: The key to set.
            value: The value to store.
            recursive: Reserved for future context hierarchy support.
        """
        self.data[key] = value

    def get_output_data(self, key: str, recursive: bool = True) -> Any:
        """
        Get a value from the output data store.
        
        Args:
            key: The key to look up.
            recursive: Reserved for future context hierarchy support.
            
        Returns:
            The value or None if not found.
        """
        return self.output_data.get(key, None)

    def set_output_data(self, key: str, value: Any, recursive: bool = True):
        """
        Set a value in the output data store.
        
        Args:
            key: The key to set.
            value: The value to store.
            recursive: Reserved for future context hierarchy support.
        """
        self.output_data[key] = value

    # ==========================================================================
    # OUTPUT METHODS
    # ==========================================================================

    def output(self) -> dict:
        """
        Generate full output dictionary for API responses.
        
        Returns:
            Dictionary containing all context state for serialization.
        """
        from python.helpers.localization import Localization
        from python.helpers import projects
        
        res = {
            "id": self.id,
            "name": self.name,
            "created_at": (
                Localization.get().serialize_datetime(self.created_at)
                if self.created_at
                else Localization.get().serialize_datetime(datetime.fromtimestamp(0))
            ),
            "no": self.no,
            "log_guid": self.log.guid,
            "log_version": len(self.log.updates),
            "log_length": len(self.log.logs),
            "paused": self.paused,
            "last_message": (
                Localization.get().serialize_datetime(self.last_message)
                if self.last_message
                else Localization.get().serialize_datetime(datetime.fromtimestamp(0))
            ),
            "type": self.type.value,
            "parent_id": self.parent_id,
        }
        
        # Resolve current project name from data
        p_name = self.get_data("project") or "default"
        res["project_name"] = p_name
        res.update(self.output_data)
        
        # Ensure 'project_name' is consistent with internal data
        res["project_name"] = self.get_data("project") or res.get("project_name") or "default"

        # Ensure 'project' object exists for UI filtering
        p_obj = res.get("project")
        if not p_obj or not isinstance(p_obj, dict) or p_obj.get("name") != p_name:
            p_header = projects.load_project_header(p_name)
            if p_header:
                res["project"] = {
                    "name": p_name,
                    "title": p_header.get("title", p_name),
                    "color": p_header.get("color", "")
                }
                # Sync back to output_data for future efficient polls
                self.output_data["project"] = res["project"]
        return res

    def output_light(self) -> dict:
        """
        Generate minimal output dictionary for efficient navigation list loading.
        
        Used by poll API in light_mode for faster chat list rendering.
        Excludes log details and output_data to reduce payload size.
        
        Returns:
            Dictionary containing minimal context state.
        """
        from python.helpers.localization import Localization
        from python.helpers import projects
        
        res = {
            "id": self.id,
            "name": self.name,
            "created_at": (
                Localization.get().serialize_datetime(self.created_at)
                if self.created_at
                else Localization.get().serialize_datetime(datetime.fromtimestamp(0))
            ),
            "no": self.no,
            "paused": self.paused,
            "last_message": (
                Localization.get().serialize_datetime(self.last_message)
                if self.last_message
                else Localization.get().serialize_datetime(datetime.fromtimestamp(0))
            ),
            "type": self.type.value,
            "parent_id": self.parent_id,
        }

        # Resolve current project name from data
        p_name = self.get_data("project") or "default"
        res["project_name"] = p_name
        res["project"] = self.output_data.get("project", None)

        # Ensure 'project' object exists for UI filtering
        p_obj = res.get("project")
        if not p_obj or not isinstance(p_obj, dict) or p_obj.get("name") != p_name:
            p_header = projects.load_project_header(p_name)
            if p_header:
                res["project"] = {
                    "name": p_name,
                    "title": p_header.get("title", p_name),
                    "color": p_header.get("color", "")
                }
                # Sync back to output_data for future efficient polls
                self.output_data["project"] = res["project"]
        return res

    @staticmethod
    def log_to_all(
        type: "Log.Type",
        heading: str | None = None,
        content: str | None = None,
        kvps: dict | None = None,
        temp: bool | None = None,
        update_progress: "Log.ProgressUpdate | None" = None,
        id: str | None = None,
        **kwargs,
    ) -> list["Log.LogItem"]:
        """
        Log a message to all registered contexts.
        
        Args:
            type: Log message type.
            heading: Optional heading text.
            content: Optional content text.
            kvps: Optional key-value pairs.
            temp: Whether this is a temporary log entry.
            update_progress: Optional progress update data.
            id: Optional log entry ID.
            **kwargs: Additional logging parameters.
            
        Returns:
            List of LogItem instances created.
        """
        items: list["Log.LogItem"] = []
        for context in AgentContext.all():
            items.append(
                context.log.log(
                    type, heading, content, kvps, temp, update_progress, id, **kwargs
                )
            )
        return items

    # ==========================================================================
    # LIFECYCLE METHODS
    # ==========================================================================

    def kill_process(self):
        """Kill the current task if running."""
        if self.task:
            self.task.kill()

    def reset(self):
        """Reset the context to initial state."""
        from python.agent import Agent
        
        self.kill_process()
        self.log.reset()
        self.agent0 = Agent(0, self.config, self)
        self.streaming_agent = None
        self.paused = False
        AgentContext._increment_version()

    def nudge(self):
        """Resume a paused context by restarting the monologue."""
        self.kill_process()
        self.paused = False
        self.task = self.run_task(self.get_agent().monologue)
        return self.task

    def get_agent(self) -> "Agent":
        """
        Get the currently active agent (streaming agent or root agent).
        
        Returns:
            The streaming agent if set, otherwise the root agent.
        """
        return self.streaming_agent or self.agent0

    def communicate(self, msg: "UserMessage", broadcast_level: int = 1, queue_if_busy: bool = False):
        """
        Send a message to the agent, starting or interrupting the message loop.
        
        Args:
            msg: The user message to send.
            broadcast_level: How many levels up to broadcast interventions.
            queue_if_busy: If True and agent is busy, queue the message for
                processing after the current monologue completes instead of
                setting it as an intervention. Used by webhook handlers to
                prevent messages from being silently dropped.
            
        Returns:
            The DeferredTask running the message loop.
        """
        from python.agent import Agent
        
        # F-2 (ITR-34): Classify message before deciding whether to unpause.
        # Control commands (stop/pause) set _user_stop_directive and flow
        # through as NORMAL interventions so the agent sees the message,
        # responds with cleanup/flushing, and then stops permanently.
        msg_text = str(getattr(msg, 'message', msg) if msg else '')
        control_class = _classify_user_message(msg_text)

        if control_class in ("control_stop", "control_pause"):
            # RCA-ITR41+ITR49: Stop = IMMEDIATE kill + flush + go idle.
            # NO new monologue — starting _process_chain gives the LLM control
            # to run tools and continue working, which defeats the stop.
            logger.warning(
                f"[USER DIRECTIVE] STOP command detected — context {self.id}. "
                f"Killing ALL agents, flushing state, going idle."
            )

            # 1. Set permanent stop flag on EVERY agent in the chain
            # RCA-ITR49: The old code only set it on streaming_agent.data,
            # NOT agent0.data. When streaming_agent returned, agent0 ran
            # a full monologue without the flag and kept working.
            self.data["_user_stop_directive"] = True

            # Set on agent0 (root) — ALWAYS
            if self.agent0 and hasattr(self.agent0, 'data'):
                self.agent0.data["_user_stop_directive"] = True

            # Set on streaming_agent (current subordinate) — if different from agent0
            current_agent = self.get_agent()
            if current_agent and current_agent is not self.agent0 and hasattr(current_agent, 'data'):
                current_agent.data["_user_stop_directive"] = True

            # 2. Kill ALL subordinate tasks immediately (no pause)
            _kill_subordinate_tasks(current_agent)
            # Also kill from agent0 down (in case streaming_agent != agent0)
            if self.agent0 and self.agent0 is not current_agent:
                _kill_subordinate_tasks(self.agent0)

            # 3. Flush state to disk NOW
            try:
                from python.helpers.persist_chat import save_tmp_chat
                save_tmp_chat(self)
            except Exception as e:
                logger.debug(f"[USER DIRECTIVE] State flush error (non-fatal): {e}")
            try:
                from python.helpers.requirements_ledger import persist_ledger_to_project
                if current_agent and hasattr(current_agent, 'data'):
                    project_dir = current_agent.data.get("_active_project_dir", "")
                    if project_dir:
                        persist_ledger_to_project(current_agent.data, project_dir)
            except Exception as e:
                logger.debug(f"[USER DIRECTIVE] Ledger flush error (non-fatal): {e}")

            # 4. Cancel the running task if alive
            if self.task and self.task.is_alive():
                try:
                    self.task.kill()
                    import time as _time
                    _kill_start = _time.time()
                    while self.task.is_alive() and (_time.time() - _kill_start) < 2.0:
                        _time.sleep(0.1)
                except Exception:
                    pass

            # 5. Clear message queue — no queued work should run after stop
            self._message_queue.clear()

            # gate_block_counters stub removed — reset_all_gate_counters was a no-op


            # 6. RCA-ITR49: DO NOT start a new _process_chain / monologue.
            # Starting a monologue gives the LLM control to call tools and
            # continue working — which is exactly what the user wants to STOP.
            # Instead: add the user message to history, write a canned stop
            # acknowledgment, and go idle. NO LLM CALL.
            root_agent = self.agent0 or current_agent
            if root_agent:
                try:
                    # Add user's stop message to history so it's recorded
                    import asyncio
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # We're in a sync context (communicate), can't await.
                        # Just log the stop — history will be added by the UI.
                        pass
                    # Write stop acknowledgment directly to agent log
                    root_agent.log(
                        type="warning",
                        heading="🛑 All Work Stopped",
                        content=(
                            "User stop directive received. All agents killed, "
                            "state flushed, queue cleared. System is idle. "
                            "Send a new message to resume work."
                        ),
                    )
                except Exception as e:
                    logger.debug(f"[USER DIRECTIVE] History write error (non-fatal): {e}")

            # 7. Clear streaming agent — root is now active
            self.streaming_agent = None
            self.paused = False
            self.execution_state = "idle"

            # 8. Final state flush
            try:
                from python.helpers.persist_chat import save_tmp_chat
                save_tmp_chat(self)
            except Exception:
                pass

            logger.warning(
                f"[USER DIRECTIVE] STOP complete — context {self.id} is now idle. "
                f"No monologue started. All agents have _user_stop_directive=True."
            )

            # Return None — no task running. System is idle.
            return None
        else:
            self.paused = False  # Normal message — unpause if paused

            # RCA-ITR49: Clear _user_stop_directive on all agents when a new
            # normal message arrives. This allows resuming after a stop.
            if self.data.get("_user_stop_directive"):
                logger.info(
                    f"[USER DIRECTIVE] Clearing _user_stop_directive — "
                    f"new message received, resuming normal operation."
                )
                self.data.pop("_user_stop_directive", None)
                if self.agent0 and hasattr(self.agent0, 'data'):
                    self.agent0.data.pop("_user_stop_directive", None)
                current_streaming = self.get_agent()
                if current_streaming and current_streaming is not self.agent0 and hasattr(current_streaming, 'data'):
                    current_streaming.data.pop("_user_stop_directive", None)

        current_agent = self.get_agent()

        if self.task and self.task.is_alive():
            if queue_if_busy:
                # Queue message for processing after current monologue completes.
                # This prevents webhook messages from being treated as interventions
                # (which get acknowledged as side-notes but never properly executed).
                if len(self._message_queue) >= self.MAX_QUEUE_DEPTH:
                    logger.warning(
                        f"Message queue full for context {self.id} "
                        f"(depth={self.MAX_QUEUE_DEPTH}), dropping message"
                    )
                    return self.task
                self._message_queue.append(msg)
                logger.info(
                    f"Queued message for context {self.id} "
                    f"(queue depth: {len(self._message_queue)})"
                )
                # Create a visible session task so the Chat Queue sidebar shows it
                # Fix #1: Also persist message_text for restart rehydration
                try:
                    from python.helpers.session_tasks import get_or_create_session_tasks
                    task_list = get_or_create_session_tasks(self.id)
                    msg_text = str(getattr(msg, 'message', msg))
                    msg_preview = msg_text[:100]
                    session_task = task_list.add_task(
                        description=f"Queued: {msg_preview}",
                        created_by="webhook",
                        priority=2,  # HIGH
                        metadata={
                            "source": "message_queue",
                            "queue_position": len(self._message_queue),
                            "message_text": msg_text,  # Fix #1: full text for rehydration
                        }
                    )
                    task_list.save_sync()
                    # Attach task_id to message so _process_chain can update status
                    msg._queue_task_id = session_task.id  # type: ignore[attr-defined]
                    logger.info(f"Created session task {session_task.id} for queued message in {self.id}")
                except Exception as e:
                    logger.warning(f"Failed to create session task for queued message: {e}")
                return self.task
            else:
                # Set intervention messages to agent(s) — for interactive users
                intervention_agent = current_agent
                while intervention_agent and broadcast_level != 0:
                    intervention_agent.intervention = msg
                    broadcast_level -= 1
                    intervention_agent = intervention_agent.data.get(
                        Agent.DATA_NAME_SUPERIOR, None
                    )
        else:
            self.execution_state = "executing"  # Issue #1095: Mark as executing before starting monologue
            # Write async execution marker for crash recovery.
            # If the container OOM-crashes, this marker file survives on disk
            # and _post_restart_nudge() can detect interrupted work.
            try:
                from python.helpers.execution_markers import write_execution_marker
                write_execution_marker(self.id)
            except Exception:
                pass  # Fire-and-forget — periodic save_tmp_chat is fallback
            self.task = self.run_task(self._process_chain, current_agent, msg)

        return self.task

    def run_task(
        self, func: Callable[..., Coroutine[Any, Any, Any]], *args: Any, **kwargs: Any
    ):
        """
        Run an async function as a background task.
        
        Args:
            func: The async function to run.
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.
            
        Returns:
            The DeferredTask running the function.
        """
        from python.helpers.defer import DeferredTask
        
        if not self.task:
            self.task = DeferredTask(
                thread_name="Background",
            )
        self.task.start_task(func, *args, **kwargs)
        return self.task

    async def _process_chain(self, agent: "Agent", msg: "UserMessage | str", user=True):
        """
        Process a message through the agent chain.
        
        This wrapper ensures that superior agents are called back if the chat
        was loaded from file and original callstack is gone.
        
        After the primary monologue completes, this method drains the
        _message_queue (populated by communicate(queue_if_busy=True)) by
        processing each queued message as a proper user message with a
        fresh monologue.
        
        Args:
            agent: The agent to process the message.
            msg: The message to process.
            user: Whether this is a user message (True) or agent response (False).
        """
        from python.agent import Agent
        
        try:
            self.execution_state = "executing"  # Issue #1095: Ensure executing state during chain
            # Reset chain-level death spiral counters on fresh user messages
            # This ensures the counter only accumulates within a single
            # _process_chain callstack (subordinate → parent → grandparent),
            # not across separate user conversations.
            if user:
                self._chain_monologue_iterations = 0
                self._chain_monologue_entries = 0

            if agent is None:
                # Issue #278: Attempt to fall back to agent0 if agent is None
                agent = self.agent0
                
            if agent is None:
                # If still None, we cannot proceed
                error_msg = f"CRITICAL: AgentContext._process_chain called with agent=None for context {self.id}. Agent initialization likely failed."
                logger.error(error_msg)
                raise Exception(error_msg)

            msg_template = (
                await agent.hist_add_user_message(msg, sender_type="user" if user else "agent")
                if user
                else await agent.hist_add_tool_result(
                    tool_name="call_subordinate", tool_result=msg, sender_type="agent"
                )
            )
            response = await agent.monologue()

            # P0-2 taint fix: Check monologue() return for error sentinels.
            # monologue() can return [CANCELLED], [USER_STOP], [ITERATION_LIMIT],
            # [RESTART_LIMIT] — these must be detected to prevent silent work loss.
            _ERROR_SENTINELS = ("[CANCELLED]", "[USER_STOP]", "[ITERATION_LIMIT]", "[RESTART_LIMIT]")
            if isinstance(response, str) and any(response.startswith(s) for s in _ERROR_SENTINELS):
                logger.warning(
                    f"[MONOLOGUE_SENTINEL] monologue() returned error sentinel "
                    f"for context {self.id}: {response[:80]}"
                )

            # F-5: If the user sent a stop directive during this monologue,
            # the agent has seen it and responded. Skip the queue drain so no
            # further queued messages restart work.
            if self.data.get("_user_stop_directive"):
                logger.info(
                    f"[USER DIRECTIVE] User stop directive active — skipping "
                    f"queue drain for context {self.id}"
                )
                return response

            # Drain queued messages after monologue completes.
            # Messages are queued by communicate(queue_if_busy=True) when
            # webhooks send messages while the agent is busy.
            # Fix #2/#6: Cap drain at MAX_QUEUE_DEPTH to prevent unbounded
            # loops when new messages arrive during drain processing.
            # Fix #3: Collect errors instead of re-raising immediately so
            # remaining messages still get processed.
            drain_count = 0
            drain_errors = []
            while self._message_queue and not self.paused and drain_count < self.MAX_QUEUE_DEPTH:
                queued_msg = self._message_queue.popleft()
                queue_task_id = getattr(queued_msg, '_queue_task_id', None)
                remaining = len(self._message_queue)
                drain_count += 1
                logger.info(
                    f"Processing queued message for context {self.id} "
                    f"(drain {drain_count}/{self.MAX_QUEUE_DEPTH}, {remaining} remaining)"
                )
                # Mark session task as in_progress in Chat Queue sidebar
                if queue_task_id:
                    try:
                        from python.helpers.session_tasks import get_session_tasks
                        task_list = get_session_tasks(self.id)
                        if task_list:
                            task_list.start_task(queue_task_id)
                            task_list.save_sync()
                    except Exception as e:
                        logger.debug(f"Failed to start session task {queue_task_id}: {e}")
                # === QUEUED MESSAGE = NEW DIRECTION ===
                # The queued message is the user's next instruction.
                # Reset all stale state so the agent starts fresh.
                agent.last_user_message = queued_msg
                agent.reset_for_user_message()
                # gate_block_counters stub removed — reset_all_gate_counters was a no-op
                pass
                await agent.hist_add_user_message(queued_msg, sender_type="user")
                try:
                    response = await agent.monologue()
                    # P0-2 taint fix: Check for error sentinels before marking success.
                    _ERROR_SENTINELS = ("[CANCELLED]", "[USER_STOP]", "[ITERATION_LIMIT]", "[RESTART_LIMIT]")
                    _is_sentinel = isinstance(response, str) and any(response.startswith(s) for s in _ERROR_SENTINELS)
                    if _is_sentinel:
                        logger.warning(
                            f"[MONOLOGUE_SENTINEL] Queue drain monologue returned error "
                            f"sentinel for context {self.id}: {response[:80]}"
                        )
                    # Mark session task as completed (only if NOT an error sentinel)
                    if queue_task_id and not _is_sentinel:
                        try:
                            from python.helpers.session_tasks import get_session_tasks
                            task_list = get_session_tasks(self.id)
                            if task_list:
                                task_list.complete_task(queue_task_id, "Processed from queue")
                                task_list.save_sync()
                        except Exception as e:
                            logger.debug(f"Failed to complete session task {queue_task_id}: {e}")
                except Exception as monologue_err:
                    # Fix #3: Mark failed but continue draining remaining messages
                    logger.error(
                        f"Monologue failed during queue drain for context {self.id} "
                        f"(drain {drain_count}): {monologue_err}"
                    )
                    drain_errors.append(monologue_err)
                    if queue_task_id:
                        try:
                            from python.helpers.session_tasks import get_session_tasks
                            task_list = get_session_tasks(self.id)
                            if task_list:
                                task_list.fail_task(queue_task_id, str(monologue_err)[:200])
                                task_list.save_sync()
                        except Exception:
                            pass
                    # Continue draining — don't re-raise yet
            
            # Log if messages remain after hitting drain cap
            if self._message_queue and drain_count >= self.MAX_QUEUE_DEPTH:
                logger.warning(
                    f"Queue drain cap reached for context {self.id} "
                    f"({len(self._message_queue)} messages remain, will drain on next cycle)"
                )
            
            # Fix #3: Re-raise first drain error after all messages processed
            if drain_errors:
                logger.error(
                    f"Queue drain completed with {len(drain_errors)} error(s) "
                    f"for context {self.id}. Re-raising first error."
                )
                raise drain_errors[0]

            superior = agent.data.get(Agent.DATA_NAME_SUPERIOR, None)
            if superior:
                response = await self._process_chain(superior, response, False)
            return response
        except Exception as e:
            if agent:
                await agent.handle_critical_exception(e)
            else:
                logger.error(f"CRITICAL: Exception in message loop with NO AGENT in context {self.id}: {e}")
                raise e
        finally:
            self.execution_state = "idle"  # Issue #1095: Always reset to idle when chain completes
            # RCA-280 FIX: Update last_message timestamp on completion.
            # ROOT CAUSE: last_message was only set at context construction time
            # and never refreshed during the agentic lifecycle. This caused:
            # 1. UI sidebar to sort this chat at its creation position (stale)
            # 2. UI poll to miss refresh triggers (old timestamp → no change detected)
            # 3. save_tmp_chat to serialize stale last_message to disk
            # FIX: Update here so every completed execution updates the timestamp.
            try:
                from datetime import datetime, timezone
                self.last_message = datetime.now(timezone.utc)
            except Exception:
                pass  # Non-critical — timestamp update is best-effort
            # Clear execution marker — context is no longer executing.
            # If the marker isn't cleared (crash), _post_restart_nudge finds it.
            try:
                from python.helpers.execution_markers import clear_execution_marker
                clear_execution_marker(self.id)
            except Exception:
                pass  # Non-critical — marker cleanup is best-effort
            # Issue #1095 FIX: Persist the idle state to disk immediately.
            # Without this, the last save_tmp_chat (in monologue.finally) wrote
            # execution_state="executing" BEFORE this finally block ran. The disk
            # file would remain stale at "executing" forever. On restart/reload,
            # the stale state caused chats to appear permanently stuck.
            try:
                from python.helpers.persist_chat import save_tmp_chat
                save_tmp_chat(self)
            except Exception as e:
                logger.warning(f"Failed to persist idle state for context {self.id}: {e}")

    def refresh_agents_config(self):
        """Refresh configuration for all agents in this context."""
        if self.agent0:
            self.agent0.refresh_config()
        else:
            # If agent was skipped during init, create it now (project-aware)
            from python.agent import Agent
            from python.initialize import initialize_agent
            self.agent0 = Agent(0, initialize_agent(context=self), self)