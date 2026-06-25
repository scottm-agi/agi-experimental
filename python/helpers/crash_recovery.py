from __future__ import annotations
"""
Crash Recovery Module for AGIX

Provides global exception handling and crash recovery coordination.
This is the foundation for all crash prevention - without catching 
top-level exceptions, subsequent work could be lost.

Key Features:
- System-wide exception catching via sys.excepthook
- Agent state preservation on crash
- Graceful degradation triggers
- Crash notification system
"""

import asyncio
import json
import logging
import os
import signal
import sys
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from python.agent import Agent, AgentContext

logger = logging.getLogger("agix.crash_recovery")


@dataclass
class CrashEvent:
    """Record of a crash event."""
    timestamp: datetime
    exception_type: str
    exception_message: str
    stack_trace: str
    agent_states: Dict[str, Any] = field(default_factory=dict)
    recovery_action: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "stack_trace": self.stack_trace,
            "agent_states": self.agent_states,
            "recovery_action": self.recovery_action,
        }


class CrashRecovery:
    """
    Global crash recovery coordinator.
    
    Provides:
    - sys.excepthook integration for catching unhandled exceptions
    - Agent state preservation before crash
    - Notification system for crash events
    - Recovery hooks for graceful degradation
    """
    
    _instance: Optional["CrashRecovery"] = None
    _original_excepthook: Optional[Callable] = None
    _registered: bool = False
    
    # Crash log storage
    CRASH_LOG_DIR = "tmp/crash_logs"
    MAX_CRASH_LOGS = 50
    
    def __init__(self):
        self._crash_handlers: List[Callable[[CrashEvent], None]] = []
        self._agent_registry: Dict[str, "Agent"] = {}
        self._context_registry: Dict[str, "AgentContext"] = {}
        self._is_handling_crash = False
        self._last_error_path: Optional[Path] = None # Added for kill escalation
        
    @classmethod
    def get_instance(cls) -> "CrashRecovery":
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def register_handler(cls) -> None:
        """
        Register the global exception handler.
        
        This should be called once at application startup to ensure
        all unhandled exceptions are caught and processed.
        """
        if cls._registered:
            return
            
        instance = cls.get_instance()
        cls._original_excepthook = sys.excepthook
        sys.excepthook = instance._global_exception_handler
        if os.environ.get("AGIX_MCP_SERVER") == "true":
            try:
                from python.helpers.mcp_logging import init_mcp_logging
                init_mcp_logging()
            except ImportError:
                logging.basicConfig(level=logging.INFO, stream=sys.stderr)
            
        cls._registered = True
        logger.info("Global crash recovery handler registered")

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, instance.graceful_shutdown)
        signal.signal(signal.SIGTERM, instance.graceful_shutdown)
        logger.info("Registered SIGINT and SIGTERM handlers for graceful shutdown")

        # Issue #925: Track restarts to detect crash loops
        instance._check_restart_loop()
        
    @classmethod
    def unregister_handler(cls) -> None:
        """Restore original exception handler."""
        if cls._original_excepthook is not None:
            sys.excepthook = cls._original_excepthook
            cls._registered = False
            logger.info("Global crash recovery handler unregistered")
    
    def register_agent(self, agent: "Agent") -> None:
        """Register an agent for state preservation on crash."""
        agent_id = getattr(agent, 'id', str(id(agent)))
        self._agent_registry[agent_id] = agent
        logger.debug(f"Registered agent {agent_id} for crash recovery")
        
    def unregister_agent(self, agent_id: str) -> None:
        """Unregister an agent."""
        if agent_id in self._agent_registry:
            del self._agent_registry[agent_id]
            logger.debug(f"Unregistered agent {agent_id} from crash recovery")
            
    def register_context(self, context: "AgentContext") -> None:
        """Register a context for state preservation on crash."""
        context_id = getattr(context, 'id', str(id(context)))
        self._context_registry[context_id] = context
        logger.debug(f"Registered context {context_id} for crash recovery")
        
    def unregister_context(self, context_id: str) -> None:
        """Unregister a context."""
        if context_id in self._context_registry:
            del self._context_registry[context_id]
            logger.debug(f"Unregistered context {context_id} from crash recovery")
            
    def add_crash_handler(self, handler: Callable[[CrashEvent], None]) -> None:
        """Add a custom crash handler to be called on crash."""
        self._crash_handlers.append(handler)
        
    def remove_crash_handler(self, handler: Callable[[CrashEvent], None]) -> None:
        """Remove a custom crash handler."""
        if handler in self._crash_handlers:
            self._crash_handlers.remove(handler)
    
    def _global_exception_handler(
        self, 
        exc_type: type, 
        exc_value: BaseException, 
        exc_tb: Any
    ) -> None:
        """
        Global exception handler for uncaught exceptions.
        
        This is set as sys.excepthook to catch all unhandled exceptions
        and attempt graceful recovery.
        """
        # Prevent recursive crash handling
        if self._is_handling_crash:
            if CrashRecovery._original_excepthook:
                CrashRecovery._original_excepthook(exc_type, exc_value, exc_tb)
            return
            
        self._is_handling_crash = True
        
        try:
            # Format the exception
            stack_trace = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
            
            logger.critical(f"Unhandled exception: {exc_type.__name__}: {exc_value}")
            logger.critical(stack_trace)
            
            # Create crash event
            crash_event = CrashEvent(
                timestamp=datetime.now(),
                exception_type=exc_type.__name__,
                exception_message=str(exc_value),
                stack_trace=stack_trace,
            )
            
            # Preserve agent states
            crash_event.agent_states = self._preserve_all_agent_states()
            
            # Save crash log
            self._last_error_path = self._save_crash_log(crash_event)
            
            # Notify all agents of crash
            self._notify_agents_of_crash(crash_event)
            
            # Run custom crash handlers
            for handler in self._crash_handlers:
                try:
                    handler(crash_event)
                except Exception as e:
                    logger.error(f"Error in crash handler: {e}")
            
            # Attempt graceful shutdown
            self.graceful_shutdown(signum=None, frame=None)
            
        except Exception as e:
            logger.error(f"Error in crash recovery: {e}")
        finally:
            self._is_handling_crash = False
            
            # Call original exception hook
            if CrashRecovery._original_excepthook:
                CrashRecovery._original_excepthook(exc_type, exc_value, exc_tb)
    
    def _preserve_all_agent_states(self) -> Dict[str, Any]:
        """Preserve state of all registered agents."""
        states = {}
        
        for agent_id, agent in self._agent_registry.items():
            try:
                states[agent_id] = self._preserve_agent_state(agent)
            except Exception as e:
                logger.error(f"Failed to preserve state for agent {agent_id}: {e}")
                states[agent_id] = {"error": str(e)}
                
        return states
    
    def _preserve_agent_state(self, agent: "Agent") -> Dict[str, Any]:
        """Preserve state of a single agent."""
        state = {
            "agent_number": getattr(agent, 'number', None),
            "iteration": getattr(agent, 'iteration', None),
            "timestamp": datetime.now().isoformat(),
        }
        
        # Preserve history summary if available
        if hasattr(agent, 'history'):
            try:
                history = agent.history
                if hasattr(history, 'output_text'):
                    # Get last few messages only to avoid huge state
                    state["recent_history"] = history.output_text()[-5000:]
            except Exception:
                pass
                
        # Preserve context info if available
        if hasattr(agent, 'context') and agent.context:
            ctx = agent.context
            state["context_id"] = getattr(ctx, 'id', None)
            state["context_name"] = getattr(ctx, 'name', None)
            
        return state
    
    def _save_crash_log(self, crash_event: CrashEvent) -> None:
        """Save crash event to disk for later analysis."""
        try:
            crash_dir = Path(self.CRASH_LOG_DIR)
            crash_dir.mkdir(parents=True, exist_ok=True)
            
            # Generate unique filename
            timestamp = crash_event.timestamp.strftime("%Y%m%d_%H%M%S")
            filename = f"crash_{timestamp}_{crash_event.exception_type}.json"
            filepath = crash_dir / filename
            
            # Write crash log
            with open(filepath, 'w') as f:
                json.dump(crash_event.to_dict(), f, indent=2, default=str)
                
            logger.info(f"Crash log saved to {filepath}")
            
            # Cleanup old crash logs
            self._cleanup_old_crash_logs(crash_dir)
            
        except Exception as e:
            logger.error(f"Failed to save crash log: {e}")
    
    def _cleanup_old_crash_logs(self, crash_dir: Path) -> None:
        """Remove old crash logs to prevent disk space issues."""
        try:
            crash_files = sorted(
                crash_dir.glob("crash_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
            
            for old_file in crash_files[self.MAX_CRASH_LOGS:]:
                old_file.unlink()
                logger.debug(f"Removed old crash log: {old_file}")
                
        except Exception as e:
            logger.error(f"Failed to cleanup crash logs: {e}")
    
    def _notify_agents_of_crash(self, crash_event: CrashEvent) -> None:
        """Notify all registered contexts of the crash."""
        for context_id, context in self._context_registry.items():
            try:
                if hasattr(context, 'log'):
                    context.log.log(
                        type="error",
                        heading="System Crash Detected",
                        content=f"Unhandled exception: {crash_event.exception_type}: {crash_event.exception_message}"
                    )
            except Exception as e:
                logger.error(f"Failed to notify context {context_id} of crash: {e}")
    
    async def _async_shutdown(self) -> None:
        """Async shutdown routine."""
        try:
            loop = asyncio.get_event_loop()
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
        except Exception as e:
            logger.error(f"Error during async shutdown: {e}")

    def graceful_shutdown(self, signum=None, frame=None, timeout=5.0):
        """
        Execute graceful shutdown of all registered agents.
        Escalates to force-kill if it takes too long.
        """
        if signum:
            logger.info(f"Received signal {signum}, starting graceful shutdown...")
        else:
            logger.info("Starting graceful shutdown...")

        # Kill escalation: start a timer to force exit if graceful doesn't finish
        def force_exit():
            logger.error(f"Graceful shutdown timed out after {timeout}s. Escalating to force-kill.")
            os._exit(signum if signum else 1)

        timer = threading.Timer(timeout, force_exit)
        timer.daemon = True
        timer.start()

        # RCA-250: Write restart manifest BEFORE save_tmp_chats so we capture
        # which contexts are executing BEFORE the finally blocks set them idle.
        try:
            from python.helpers import files
            manifest_path = files.get_abs_path("tmp", "restart_manifest.json")
            _write_restart_manifest(manifest_path)
        except Exception as e:
            logger.error(f"Failed to write restart manifest: {e}")

        # Try to save any pending chat data
        try:
            from python.helpers.persist_chat import save_tmp_chats
            save_tmp_chats()
            logger.info("Saved pending chat data")
        except Exception as e:
            logger.error(f"Failed to save chat data during shutdown: {e}")

        # Stop all registered agents
        for agent_ref in list(self._agent_registry.values()):
            if agent_ref:
                try:
                    logger.info(f"Shutting down agent: {getattr(agent_ref, 'agent_name', 'unknown')}")
                except Exception as e:
                    logger.error(f"Error during agent shutdown: {e}")
        
        # Log final crash state if this was an exception
        if self._last_error_path and os.path.exists(str(self._last_error_path)):
            logger.info(f"Crash state preserved at {self._last_error_path}")

        logger.info("Graceful shutdown complete. Exiting.")
        sys.exit(signum if signum else 0)

    # Issue #925: Restart loop detection
    RESTART_LOG_PATH = "tmp/restart_log.json"
    CONTAINER_BOOT_ID_PATH = "tmp/container_boot_id"
    RESTART_LOOP_THRESHOLD = 5   # restarts
    RESTART_LOOP_WINDOW = 60     # seconds

    def _check_restart_loop(self):
        """
        Issue #925: Detect crash loops by tracking restart timestamps.
        If 5+ restarts happen within 60 seconds, log a CRITICAL warning.
        
        Uses container boot ID (HOSTNAME env var) to deduplicate sub-process
        restarts (e.g. prepare.py + run_ui.py both calling register_handler())
        within the same container boot.
        """
        import time
        now = time.time()
        restart_log: list = []

        try:
            # Deduplicate sub-process restarts within the same container boot
            boot_id = os.environ.get("HOSTNAME", str(os.getpid()))
            boot_id_path = Path(self.CONTAINER_BOOT_ID_PATH)
            boot_id_path.parent.mkdir(parents=True, exist_ok=True)

            if boot_id_path.exists() and boot_id_path.read_text().strip() == boot_id:
                # Same container boot — this is a sub-process restart, skip counting
                logger.debug(f"Sub-process restart detected (boot_id={boot_id}), skipping restart counter")
                return

            # New container boot — record the boot ID
            boot_id_path.write_text(boot_id)

            log_path = Path(self.RESTART_LOG_PATH)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            if log_path.exists():
                with open(log_path, 'r') as f:
                    restart_log = json.load(f)

            # Append current startup
            restart_log.append(now)

            # Keep only entries within the window
            cutoff = now - self.RESTART_LOOP_WINDOW
            restart_log = [t for t in restart_log if t > cutoff]

            # Save updated log
            with open(log_path, 'w') as f:
                json.dump(restart_log, f)

            if len(restart_log) >= self.RESTART_LOOP_THRESHOLD:
                logger.critical(
                    f"CRASH LOOP DETECTED: {len(restart_log)} restarts in "
                    f"{self.RESTART_LOOP_WINDOW}s (threshold: {self.RESTART_LOOP_THRESHOLD}). "
                    f"Check Docker logs, OOM killer, or config issues."
                )
                # Write a human-readable marker file for ops
                marker = Path("tmp/CRASH_LOOP_DETECTED")
                marker.write_text(
                    f"Detected {len(restart_log)} restarts at {datetime.now().isoformat()}\n"
                    f"Timestamps: {restart_log}\n"
                )
            else:
                logger.info(f"Startup #{len(restart_log)} in last {self.RESTART_LOOP_WINDOW}s (threshold: {self.RESTART_LOOP_THRESHOLD})")

        except Exception as e:
            logger.warning(f"Restart loop check failed: {e}")


# Convenience functions for module-level access
def register_crash_handler() -> None:
    """Register the global crash recovery handler."""
    CrashRecovery.register_handler()


def get_crash_recovery() -> CrashRecovery:
    """Get the crash recovery singleton."""
    return CrashRecovery.get_instance()


def preserve_agent_state(agent: "Agent") -> Dict[str, Any]:
    """Preserve state of a single agent."""
    return get_crash_recovery()._preserve_agent_state(agent)


def _should_resume_context(entry: dict) -> bool:
    """
    RCA-250: Intelligent decision — should this context be resumed after restart?
    
    The supervisor evaluates whether a context has enough meaningful progress
    to warrant automatic resumption. Contexts that were just opened (no progress)
    or casual chats (default project) are NOT resumed to avoid wasting resources.
    
    Args:
        entry: Dict with keys: id, name, project, agent_counter, execution_state
        
    Returns:
        True if the context should be automatically resumed.
    """
    # Must have been executing
    if entry.get("execution_state") != "executing":
        return False
    
    # Must have made real progress (agent_counter > 0)
    agent_counter = entry.get("agent_counter", 0)
    if agent_counter <= 0:
        logger.info(
            f"Skip resume for {entry.get('id')}: no progress (counter={agent_counter})"
        )
        return False
    
    # Must be on a real project (not 'default' casual chat)
    project = entry.get("project", "default")
    if project == "default":
        logger.info(
            f"Skip resume for {entry.get('id')}: default project (casual chat)"
        )
        return False
    
    logger.info(
        f"Context {entry.get('id')} qualifies for resume: "
        f"project={project}, counter={agent_counter}"
    )
    return True


def _write_restart_manifest(manifest_path: str) -> None:
    """
    RCA-250: Write a restart manifest capturing which contexts should resume.
    
    Called by graceful_shutdown() BEFORE save_tmp_chats() so we capture
    the live execution_state before the finally blocks clean it up.
    
    IMPORTANT: Also captures RECENTLY ACTIVE contexts (last 120s), not just
    currently-executing ones. By the time SIGTERM arrives, the agent loop may
    have finished its iteration and set execution_state back to 'idle'.
    """
    from python.helpers.agent_core.context import AgentContext
    
    try:
        executing_contexts = []
        seen_ids = set()
        now = datetime.now(timezone.utc)
        
        for ctx in AgentContext.all():
            ctx_id = ctx.id
            if ctx_id in seen_ids:
                continue
            
            is_executing = getattr(ctx, "execution_state", "idle") == "executing"
            
            # Also check if context was recently active (last 120s)
            # This catches the case where the agent loop finished its iteration
            # just before SIGTERM arrived
            is_recently_active = False
            last_msg = getattr(ctx, 'last_message', None)
            if last_msg:
                try:
                    if last_msg.tzinfo is None:
                        last_msg = last_msg.replace(tzinfo=timezone.utc)
                    age = (now - last_msg).total_seconds()
                    is_recently_active = age < 120  # Active within 2 minutes
                except Exception:
                    pass
            
            if not is_executing and not is_recently_active:
                continue
            
            # Capture rich context for intelligent resume decision
            agent_counter = 0
            try:
                if ctx.agent0:
                    hist = getattr(ctx.agent0, 'history', None)
                    if hist:
                        agent_counter = getattr(hist, 'counter', 0)
                        if not agent_counter:
                            agent_counter = hist.get("counter", 0) if isinstance(hist, dict) else 0
            except Exception:
                pass
            
            seen_ids.add(ctx_id)
            executing_contexts.append({
                "id": ctx_id,
                "name": getattr(ctx, "name", ""),
                "project": ctx.get_data("project") or "default",
                "agent_counter": agent_counter,
                "execution_state": "executing",  # Mark as executing for resume logic
                "was_executing": is_executing,
                "was_recently_active": is_recently_active,
            })
        
        manifest = {
            "timestamp": now.isoformat(),
            "reason": "graceful_shutdown",
            "executing_contexts": executing_contexts,
        }
        
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        
        logger.info(
            f"Restart manifest written: {len(executing_contexts)} context(s) "
            f"(executing + recently active) at {manifest_path}"
        )
    except Exception as e:
        logger.error(f"Failed to write restart manifest: {e}")


def _post_restart_nudge() -> None:
    """
    Issue #1095: Nudge the most recent active chats after container restart.
    
    Sorts all loaded contexts by last_message timestamp, takes the top 3 with
    real projects (non-default), restores ledger/project state, and calls
    nudge() on each to resume work without polluting chat history.
    
    This must be called AFTER load_tmp_chats completes so all contexts are loaded.
    """
    from python.helpers.agent_core.context import AgentContext
    
    MAX_NUDGE = 3  # Nudge up to 3 most recent active chats
    
    try:
        # Sort all loaded contexts by last_message (most recent first)
        def _safe_last_msg(ctx):
            ts = getattr(ctx, 'last_message', None)
            if ts is None:
                return datetime.min.replace(tzinfo=timezone.utc)
            if ts.tzinfo is None:
                return ts.replace(tzinfo=timezone.utc)
            return ts
        
        contexts = sorted(
            AgentContext._contexts.values(),
            key=_safe_last_msg,
            reverse=True,
        )
        
        if not contexts:
            logger.info("Post-restart nudge: No contexts loaded, no nudge needed.")
            return
        
        # Find candidates: recent contexts with real projects that aren't idle-default
        nudged_count = 0
        for ctx in contexts:
            if nudged_count >= MAX_NUDGE:
                break
            
            ctx_id = ctx.id
            project = ctx.get_data("project") or "default"
            
            # Skip default/casual chats — only nudge real project work
            if project == "default":
                continue
            
            # Skip contexts with no agent loaded
            if not ctx.agent0:
                continue
            
            logger.warning(
                f"Post-restart nudge: Context {ctx_id} "
                f"(name={getattr(ctx, 'name', '')}, project={project}) "
                f"is a recent active chat. Restoring state and nudging."
            )
            
            # Restore requirements ledger from disk BEFORE nudging
            try:
                from python.helpers.requirements_ledger import load_ledger_from_project
                from python.helpers.projects import get_project_folder
                
                project_dir = get_project_folder(project)
                if project_dir and os.path.isdir(project_dir):
                    agent0 = ctx.agent0
                    if agent0 and hasattr(agent0, 'data'):
                        loaded = load_ledger_from_project(agent0.data, project_dir)
                        if loaded:
                            logger.info(
                                f"Post-restart nudge: Restored ledger for {ctx_id}"
                            )
            except Exception as ledger_err:
                logger.warning(
                    f"Post-restart nudge: Ledger restore failed for "
                    f"{ctx_id} (non-fatal): {ledger_err}"
                )
            
            # System 7: Validate/set _active_project_dir
            try:
                from python.helpers.projects import get_project_folder
                
                project_dir = get_project_folder(project)
                agent0 = ctx.agent0
                if agent0 and hasattr(agent0, 'data') and project_dir:
                    stored = agent0.data.get("_active_project_dir", "")
                    if stored and stored != project_dir:
                        from python.helpers.agent_data_keys import invalidate_project_scoped_keys
                        cleared = invalidate_project_scoped_keys(agent0.data, project_dir)
                        logger.warning(
                            f"Post-restart nudge (System 7): Project dir changed for "
                            f"{ctx_id}. Cleared {len(cleared)} scoped keys."
                        )
                    elif not stored:
                        agent0.data["_active_project_dir"] = project_dir
            except Exception as proj_err:
                logger.warning(
                    f"Post-restart nudge: System 7 failed for "
                    f"{ctx_id} (non-fatal): {proj_err}"
                )
            
            # Nudge — restarts monologue without chat history pollution
            ctx.nudge()
            nudged_count += 1
            logger.info(f"Post-restart nudge: Nudged context {ctx_id} ({nudged_count}/{MAX_NUDGE})")
        
        if nudged_count == 0:
            logger.info(
                f"Post-restart nudge: No active project contexts found among "
                f"{len(contexts)} loaded contexts. No nudge needed."
            )
        else:
            logger.info(f"Post-restart nudge: Nudged {nudged_count} context(s).")
        
        # Secondary: clean up stale execution markers from old tests
        try:
            from python.helpers.execution_markers import get_interrupted_contexts, clear_marker
            stale_ids = get_interrupted_contexts()
            if stale_ids:
                logger.info(
                    f"Post-restart nudge: Cleaning {len(stale_ids)} stale "
                    f"execution markers."
                )
                for stale_id in stale_ids:
                    clear_marker(stale_id)
        except Exception:
            pass  # Non-fatal
        
        # Secondary: consume restart manifest if present
        try:
            from python.helpers import files as _files
            manifest_path = _files.get_abs_path("tmp", "restart_manifest.json")
            if os.path.isfile(manifest_path):
                os.remove(manifest_path)
                logger.info("Post-restart nudge: Consumed restart manifest.")
        except Exception:
            pass  # Non-fatal
        
    except Exception as e:
        logger.error(f"Post-restart nudge failed: {e}", exc_info=True)

