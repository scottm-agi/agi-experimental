from __future__ import annotations
"""
Agent Tracer - Comprehensive tracing system for multi-agent workflows

This module provides structured tracing of agent lifecycle events including:
- Agent creation and hierarchy
- Task assignments and prompts
- LLM calls and responses
- Tool executions
- Agent completion

Usage:
    from python.helpers.agent_tracer import AgentTracer
    
    # Enable tracing
    AgentTracer.enable()
    
    # Or enable with file output
    AgentTracer.enable(trace_file="traces/my_trace.json")
    
    # Trace events are automatically captured via extensions
    # Or manually:
    AgentTracer.trace_agent_created(agent)
    AgentTracer.trace_task_assigned(agent, "Do something")
"""

import json
import logging
import sys
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, TYPE_CHECKING
from enum import Enum
import uuid

if TYPE_CHECKING:
    from python.agent import Agent

class TraceEventType(str, Enum):
    """Types of trace events"""
    AGENT_CREATED = "agent_created"
    AGENT_TASK_ASSIGNED = "agent_task_assigned"
    AGENT_PROMPT_BUILT = "agent_prompt_built"
    AGENT_LLM_CALL_START = "agent_llm_call_start"
    AGENT_LLM_RESPONSE = "agent_llm_response"
    AGENT_TOOL_CALLED = "agent_tool_called"
    AGENT_TOOL_RESULT = "agent_tool_result"
    AGENT_SUBORDINATE_CREATED = "agent_subordinate_created"
    AGENT_SUBORDINATE_COMPLETED = "agent_subordinate_completed"
    AGENT_MONOLOGUE_START = "agent_monologue_start"
    AGENT_MONOLOGUE_END = "agent_monologue_end"
    AGENT_MESSAGE_LOOP_ITERATION = "agent_message_loop_iteration"
    AGENT_ERROR = "agent_error"
    AGENT_COMPLETED = "agent_completed"

    # Gate/Guard events (ADR-088)
    GATE_REJECT = "gate_reject"
    GATE_ACCEPT = "gate_accept"
    GUARD_BLOCK = "guard_block"
    GUARD_ALLOW = "guard_allow"

    # Delegation events (ADR-088)
    DELEGATION_SENT = "delegation_sent"
    DELEGATION_RETURNED = "delegation_returned"
    REMEDIATION_APPLIED = "remediation_applied"

    # State events (ADR-088)
    PHASE_STATUS_CHANGE = "phase_status"
    REQ_STATUS_CHANGE = "req_status"

    # Detection events (ADR-088)
    DEADLOCK_DETECTED = "deadlock_detected"
    DEADLOCK_RESOLVED = "deadlock_resolved"
    STUB_DETECTED = "stub_detected"
    TOPIC_DEDUP = "topic_dedup"


@dataclass
class TraceEvent:
    """A single trace event"""
    event_type: TraceEventType
    timestamp: str
    agent_id: str
    agent_number: int
    data: Dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "agent_id": self.agent_id,
            "agent_number": self.agent_number,
            "trace_id": self.trace_id,
            **self.data
        }


@dataclass
class AgentTrace:
    """Complete trace for a session"""
    trace_id: str
    start_time: str
    events: List[TraceEvent] = field(default_factory=list)
    end_time: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "event_count": len(self.events),
            "events": [e.to_dict() for e in self.events]
        }
    
    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)



class JsonlTraceWriter:
    """High-speed JSONL trace writer with buffered I/O.
    
    Writes one JSON object per line to a trace_<session_id>.jsonl file.
    All keys are 2-4 chars for minimal serialization overhead (ADR-088).
    """

    def __init__(self, log_dir: Path, session_id: str):
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_root_dir = log_dir  # Remember the ORIGINAL root — never nest
        self._file = open(log_dir / f"trace_{session_id}.jsonl", "a", buffering=8192)
        self._seq = 0
        self._session_id = session_id
        self._current_chat_id: str = ""  # Track current chat binding

    def write_event(self, evt: str, agent_id: str, agent_num: int,
                    profile: str, data: dict, chat_id: str = "", project: str = ""):
        self._seq += 1
        line = json.dumps({
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "sid": self._session_id,
            "seq": self._seq,
            "evt": evt,
            "aid": agent_id,
            "anum": agent_num,
            "pid": profile,
            "cid": chat_id,
            "proj": project,
            "d": data
        }, separators=(',', ':'), default=str)
        self._file.write(line + "\n")

    def flush(self):
        if self._file and not self._file.closed:
            self._file.flush()

    def close(self):
        if self._file and not self._file.closed:
            self._file.flush()
            self._file.close()


class AgentTracer:
    """
    Singleton tracer for capturing agent lifecycle events.
    
    Enable/disable via:
        AgentTracer.enable()
        AgentTracer.disable()
    
    Or check settings for automatic enable.
    """
    
    _instance: Optional["AgentTracer"] = None
    _lock = threading.Lock()
    
    # Class-level settings
    _enabled: bool = False
    _trace_file: Optional[str] = None
    _console_output: bool = True
    _log_to_context: bool = True
    _log_to_file: bool = True  # Log to logs/ directory
    
    # ADR-088 FIX: ClassVar instead of ContextVar.
    # ContextVar caused all 72 trace files to be empty because enable() set
    # the trace in one async context, but extensions fire in different contexts
    # → _current_trace_var.get() returned None → _add_event() returned early.
    # Thread safety is NOT a concern: all agents run in the same asyncio
    # event loop (single-threaded). Events are append-only.
    _current_trace: ClassVar[Optional['AgentTrace']] = None
    
    # JSONL writer for machine-readable trace output (ADR-088)
    _jsonl_writer: ClassVar[Optional['JsonlTraceWriter']] = None
    
    _file_logger: Optional[logging.Logger] = None

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def get_instance(cls) -> "AgentTracer":
        """Get or create the singleton instance"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def enable(cls, 
               trace_file: Optional[str] = None, 
               console_output: bool = True,
               log_to_context: bool = False,
               log_to_file: bool = False) -> None:
        """
        Enable agent tracing.
        
        Args:
            trace_file: Optional path to write JSON trace file
            console_output: Whether to print trace events to console
            log_to_context: Whether to log trace events to agent context log
            log_to_file: Whether to log trace events to logs/agent_trace.log
        """
        cls._enabled = True
        cls._trace_file = trace_file
        cls._console_output = console_output
        cls._log_to_context = log_to_context
        cls._log_to_file = log_to_file
        
        # Start a new trace session
        current_trace = AgentTrace(
            trace_id=str(uuid.uuid4())[:8],
            start_time=cls._get_timestamp()
        )
        cls._current_trace = current_trace
        
        # Create JSONL writer for machine-readable output (ADR-088)
        cls._jsonl_writer = JsonlTraceWriter(
            log_dir=Path(__file__).parent.parent.parent / "logs",
            session_id=current_trace.trace_id
        )
        
        # Set up file logger
        if log_to_file:
            cls._setup_file_logger()
        
        if cls._console_output:
            cls._print_header("AGENT TRACING ENABLED")
            if trace_file:
                print(f"  Trace file: {trace_file}", file=sys.stderr)
    
    @classmethod
    def disable(cls) -> Optional[AgentTrace]:
        """
        Disable agent tracing and return the completed trace.
        
        Returns:
            The completed AgentTrace object, or None if tracing wasn't enabled
        """
        if not cls._enabled:
            return None
        
        cls._enabled = False
        trace = cls._current_trace
        
        if trace:
            trace.end_time = cls._get_timestamp()
            
            # Write to file if configured
            if cls._trace_file:
                cls._write_trace_file(trace)
            
            # Log final summary to file
            if cls._file_logger:
                cls._file_logger.info(f"=== TRACE SESSION ENDED === Total events: {len(trace.events)}")
            
            if cls._console_output:
                cls._print_header("AGENT TRACING DISABLED")
                print(f"  Total events: {len(trace.events)}", file=sys.stderr)
        
        # R-9 (ITR-52 D-12): Write session summary to JSONL before closing
        if cls._jsonl_writer and trace:
            event_counts: dict = {}
            error_count = 0
            guard_block_count = 0
            for evt in trace.events:
                evt_type = evt.event_type.value
                event_counts[evt_type] = event_counts.get(evt_type, 0) + 1
                if evt.event_type == TraceEventType.AGENT_ERROR:
                    error_count += 1
                if evt.event_type == TraceEventType.GUARD_BLOCK:
                    guard_block_count += 1
            
            # Calculate session duration in seconds
            duration_s = 0.0
            if trace.start_time and trace.end_time:
                try:
                    from datetime import datetime as _dt
                    fmt = "%Y-%m-%dT%H:%M:%S.%f"
                    # Handle timezone suffix
                    start_str = trace.start_time.replace("Z", "+00:00")
                    end_str = trace.end_time.replace("Z", "+00:00")
                    # Strip timezone for simple parsing
                    start_clean = start_str[:26]  # YYYY-MM-DDTHH:MM:SS.ffffff
                    end_clean = end_str[:26]
                    start_dt = _dt.strptime(start_clean, fmt)
                    end_dt = _dt.strptime(end_clean, fmt)
                    duration_s = (end_dt - start_dt).total_seconds()
                except (ValueError, TypeError):
                    duration_s = 0.0
            
            cls._jsonl_writer.write_event(
                evt="session_summary",
                agent_id="system",
                agent_num=0,
                profile="system",
                data={
                    "total_events": len(trace.events),
                    "event_counts": event_counts,
                    "errors": error_count,
                    "guard_blocks": guard_block_count,
                    "duration_s": round(duration_s, 3),
                }
            )
        
        # Close JSONL writer (ADR-088)
        if cls._jsonl_writer:
            cls._jsonl_writer.close()
            cls._jsonl_writer = None
        
        cls._current_trace = None
        cls._file_logger = None
        return trace
    
    @classmethod
    def is_enabled(cls) -> bool:
        """Check if tracing is enabled"""
        return cls._enabled
    
    @classmethod
    def get_current_trace(cls) -> Optional[AgentTrace]:
        """Get the current trace session"""
        return cls._current_trace

    @classmethod
    def set_chat_id(cls, chat_id: str, log_dir: Optional[Path] = None) -> None:
        """Set the chat ID for this trace session (R-8).

        Creates ``logs/<chat_id>/`` and moves the current trace JSONL into
        that directory as ``trace.jsonl``.  All subsequent events are written
        to the new file.  Sequence counters are preserved across the move.

        CRITICAL: Always resolves the chat directory relative to the ORIGINAL
        log root (``_log_root_dir``), never relative to the current file's
        parent. This prevents triple-nesting when called multiple times.

        Args:
            chat_id: Human-readable chat identifier (e.g. ``MSR_Ph3_1781747418``).
            log_dir: Override for the logs root directory (used by tests).
        """
        if not cls._jsonl_writer or not chat_id:
            return

        # Idempotent: skip if already bound to this chat_id
        if cls._jsonl_writer._current_chat_id == chat_id:
            return

        try:
            old_file = cls._jsonl_writer._file
            old_path = Path(old_file.name)
            old_seq = cls._jsonl_writer._seq
            root_dir = cls._jsonl_writer._log_root_dir

            # Flush & close old writer
            cls._jsonl_writer.close()

            # ALWAYS resolve from the ORIGINAL log root — never old_path.parent
            base_log_dir = log_dir or root_dir
            chat_dir = base_log_dir / chat_id
            chat_dir.mkdir(parents=True, exist_ok=True)
            new_path = chat_dir / "trace.jsonl"

            # Move the temp trace file into the chat directory
            if old_path.exists() and old_path != new_path:
                import shutil
                shutil.move(str(old_path), str(new_path))

            # Open a new writer pointing at the chat directory
            cls._jsonl_writer = JsonlTraceWriter.__new__(JsonlTraceWriter)
            cls._jsonl_writer._log_root_dir = base_log_dir  # Preserve root
            cls._jsonl_writer._file = open(new_path, "a", buffering=8192)
            cls._jsonl_writer._seq = old_seq
            cls._jsonl_writer._session_id = chat_id
            cls._jsonl_writer._current_chat_id = chat_id

        except Exception as e:
            import sys
            print(f"[TRACE] Warning: set_chat_id failed: {e}", file=sys.stderr)
    
    # =========================================================================
    # New Trace Event Methods (ADR-088)
    # =========================================================================
    
    @classmethod
    def trace_gate_reject(cls, agent: "Agent", gate: str, reason: str,
                          phase_seq: str, rejection_count: int = 0) -> None:
        """Trace a gate rejection (ADR-088: ITR-52c)"""
        if not cls._enabled:
            return
        cls._add_event(
            event_type=TraceEventType.GATE_REJECT,
            agent=agent,
            data={"gate": gate, "reason": cls._truncate(reason, 200),
                  "phase_seq": phase_seq, "rejection_count": rejection_count}
        )
    
    @classmethod
    def trace_gate_accept(cls, agent: "Agent", gate: str,
                          forced: bool = False, deadlock_count: int = 0) -> None:
        """Trace a gate acceptance (ADR-088: ITR-52c)"""
        if not cls._enabled:
            return
        cls._add_event(
            event_type=TraceEventType.GATE_ACCEPT,
            agent=agent,
            data={"gate": gate, "forced": forced, "deadlock_count": deadlock_count}
        )
    
    @classmethod
    def trace_guard_block(cls, agent: "Agent", guard: str, reason: str,
                          similarity: float = 0.0, count: int = 0,
                          limit: int = 0) -> None:
        """Trace a guard block (ADR-088: ITR-52c)"""
        if not cls._enabled:
            return
        cls._add_event(
            event_type=TraceEventType.GUARD_BLOCK,
            agent=agent,
            data={"guard": guard, "reason": cls._truncate(reason, 200),
                  "similarity": similarity, "count": count, "limit": limit}
        )
    
    @classmethod
    def trace_guard_allow(cls, agent: "Agent", guard: str,
                          count: int = 0) -> None:
        """Trace a guard allow (ADR-088: ITR-52c)"""
        if not cls._enabled:
            return
        cls._add_event(
            event_type=TraceEventType.GUARD_ALLOW,
            agent=agent,
            data={"guard": guard, "count": count}
        )
    
    @classmethod
    def trace_delegation_sent(cls, agent: "Agent", profile: str,
                              phase_seq: str, message_hash: str,
                              req_ids: list, attempt_num: int = 1) -> None:
        """Trace a delegation sent (ADR-088: ITR-52a)"""
        if not cls._enabled:
            return
        cls._add_event(
            event_type=TraceEventType.DELEGATION_SENT,
            agent=agent,
            data={"profile": profile, "phase_seq": phase_seq,
                  "message_hash": message_hash, "req_ids": req_ids,
                  "attempt_num": attempt_num}
        )
    
    @classmethod
    def trace_delegation_returned(cls, agent: "Agent", profile: str,
                                   phase_seq: str, status: str,
                                   files_created: int = 0, stubs: int = 0,
                                   errors: int = 0) -> None:
        """Trace a delegation return (ADR-088: ITR-52a)"""
        if not cls._enabled:
            return
        cls._add_event(
            event_type=TraceEventType.DELEGATION_RETURNED,
            agent=agent,
            data={"profile": profile, "phase_seq": phase_seq,
                  "status": status, "files_created": files_created,
                  "stubs": stubs, "errors": errors}
        )
    
    @classmethod
    def trace_remediation_applied(cls, agent: "Agent", phase_seq: str,
                                   attempt_num: int, issues_count: int,
                                   original_hash: str) -> None:
        """Trace remediation applied (ADR-088: ADR-087)"""
        if not cls._enabled:
            return
        cls._add_event(
            event_type=TraceEventType.REMEDIATION_APPLIED,
            agent=agent,
            data={"phase_seq": phase_seq, "attempt_num": attempt_num,
                  "issues_count": issues_count, "original_hash": original_hash}
        )
    
    @classmethod
    def trace_phase_status_change(cls, agent: "Agent", phase_seq: str,
                                   from_status: str, to_status: str,
                                   evidence_type: str = "") -> None:
        """Trace phase status change (ADR-088: ITR-52b)"""
        if not cls._enabled:
            return
        cls._add_event(
            event_type=TraceEventType.PHASE_STATUS_CHANGE,
            agent=agent,
            data={"phase_seq": phase_seq, "from_status": from_status,
                  "to_status": to_status, "evidence_type": evidence_type}
        )
    
    @classmethod
    def trace_req_status_change(cls, agent: "Agent", req_id: str,
                                 from_status: str, to_status: str,
                                 trigger: str = "") -> None:
        """Trace requirement status change (ADR-088: ITR-40)"""
        if not cls._enabled:
            return
        cls._add_event(
            event_type=TraceEventType.REQ_STATUS_CHANGE,
            agent=agent,
            data={"req_id": req_id, "from_status": from_status,
                  "to_status": to_status, "trigger": trigger}
        )
    
    @classmethod
    def trace_deadlock_detected(cls, agent: "Agent", count: int,
                                 tool_blocker: str = "",
                                 gate_rejector: str = "") -> None:
        """Trace deadlock detected (ADR-088: ITR-52c)"""
        if not cls._enabled:
            return
        cls._add_event(
            event_type=TraceEventType.DEADLOCK_DETECTED,
            agent=agent,
            data={"count": count, "tool_blocker": tool_blocker,
                  "gate_rejector": gate_rejector}
        )
    
    @classmethod
    def trace_deadlock_resolved(cls, agent: "Agent", count: int,
                                 resolution: str = "") -> None:
        """Trace deadlock resolved (ADR-088: ITR-52c)"""
        if not cls._enabled:
            return
        cls._add_event(
            event_type=TraceEventType.DEADLOCK_RESOLVED,
            agent=agent,
            data={"count": count, "resolution": resolution}
        )
    
    @classmethod
    def trace_stub_detected(cls, agent: "Agent", file: str, line: int,
                             content: str = "", count: int = 1) -> None:
        """Trace stub detected (ADR-088: ITR-37, ITR-51)"""
        if not cls._enabled:
            return
        cls._add_event(
            event_type=TraceEventType.STUB_DETECTED,
            agent=agent,
            data={"file": file, "line": line,
                  "content": cls._truncate(content, 200), "count": count}
        )
    
    @classmethod
    def trace_topic_dedup(cls, agent: "Agent", similarity: float,
                           count: int, hard_limit: int,
                           action: str = "allow") -> None:
        """Trace topic dedup (ADR-088: ITR-52c)"""
        if not cls._enabled:
            return
        cls._add_event(
            event_type=TraceEventType.TOPIC_DEDUP,
            agent=agent,
            data={"similarity": similarity, "count": count,
                  "hard_limit": hard_limit, "action": action}
        )
    
    # =========================================================================
    # Trace Event Methods
    # =========================================================================
    
    @classmethod
    def trace_agent_created(cls, agent: "Agent", parent_agent: Optional["Agent"] = None) -> None:
        """Trace agent creation"""
        if not cls._enabled:
            return
        
        parent_id = parent_agent.agent_name if parent_agent else None
        
        cls._add_event(
            event_type=TraceEventType.AGENT_CREATED,
            agent=agent,
            data={
                "profile": agent.config.profile,
                "parent_agent": parent_id,
                "context_id": agent.context.id if agent.context else None
            }
        )
    
    @classmethod
    def trace_task_assigned(cls, agent: "Agent", message: str) -> None:
        """Trace when a task/message is assigned to an agent"""
        if not cls._enabled:
            return
        
        cls._add_event(
            event_type=TraceEventType.AGENT_TASK_ASSIGNED,
            agent=agent,
            data={
                "message": cls._truncate(message, 500),
                "message_length": len(message)
            }
        )
    
    @classmethod
    def trace_prompt_built(cls, agent: "Agent", 
                          system_prompt: str,
                          history_length: int,
                          extras: Optional[Dict] = None) -> None:
        """Trace when a prompt is built for LLM call"""
        if not cls._enabled:
            return
        
        cls._add_event(
            event_type=TraceEventType.AGENT_PROMPT_BUILT,
            agent=agent,
            data={
                "system_prompt_preview": cls._truncate(system_prompt, 300),
                "system_prompt_length": len(system_prompt),
                "history_messages": history_length,
                "extras_keys": list(extras.keys()) if extras else []
            }
        )
    
    @classmethod
    def trace_llm_call_start(cls, agent: "Agent", model_name: str) -> None:
        """Trace start of LLM call"""
        if not cls._enabled:
            return
        
        cls._add_event(
            event_type=TraceEventType.AGENT_LLM_CALL_START,
            agent=agent,
            data={
                "model": model_name
            }
        )
    
    @classmethod
    def trace_llm_response(cls, agent: "Agent", 
                          response: str, 
                          duration_ms: Optional[float] = None,
                          tokens_in: Optional[int] = None,
                          tokens_out: Optional[int] = None) -> None:
        """Trace LLM response received"""
        if not cls._enabled:
            return
        
        cls._add_event(
            event_type=TraceEventType.AGENT_LLM_RESPONSE,
            agent=agent,
            data={
                "response_preview": cls._truncate(response, 300),
                "response_length": len(response),
                "duration_ms": duration_ms,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out
            }
        )
    
    @classmethod
    def trace_tool_called(cls, agent: "Agent", 
                         tool_name: str, 
                         tool_args: Dict[str, Any]) -> None:
        """Trace tool execution start"""
        if not cls._enabled:
            return
        
        # Truncate arg values for logging
        safe_args = {}
        for k, v in tool_args.items():
            if isinstance(v, str):
                safe_args[k] = cls._truncate(v, 200)
            else:
                safe_args[k] = v
        
        cls._add_event(
            event_type=TraceEventType.AGENT_TOOL_CALLED,
            agent=agent,
            data={
                "tool_name": tool_name,
                "tool_args": safe_args
            }
        )
    
    @classmethod
    def trace_tool_result(cls, agent: "Agent",
                         tool_name: str,
                         result: str,
                         duration_ms: Optional[float] = None,
                         success: bool = True) -> None:
        """Trace tool execution result"""
        if not cls._enabled:
            return
        
        cls._add_event(
            event_type=TraceEventType.AGENT_TOOL_RESULT,
            agent=agent,
            data={
                "tool_name": tool_name,
                "result_preview": cls._truncate(result, 300),
                "result_length": len(result),
                "duration_ms": duration_ms,
                "success": success
            }
        )
    
    @classmethod
    def trace_subordinate_created(cls, parent_agent: "Agent", 
                                  subordinate_agent: "Agent",
                                  mission: str) -> None:
        """Trace subordinate agent creation"""
        if not cls._enabled:
            return
        
        cls._add_event(
            event_type=TraceEventType.AGENT_SUBORDINATE_CREATED,
            agent=parent_agent,
            data={
                "subordinate_id": subordinate_agent.agent_name,
                "subordinate_number": subordinate_agent.number,
                "subordinate_profile": subordinate_agent.config.profile,
                "mission": cls._truncate(mission, 500)
            }
        )
    
    @classmethod
    def trace_subordinate_completed(cls, parent_agent: "Agent",
                                    subordinate_agent: "Agent",
                                    result: str) -> None:
        """Trace subordinate agent completion"""
        if not cls._enabled:
            return
        
        cls._add_event(
            event_type=TraceEventType.AGENT_SUBORDINATE_COMPLETED,
            agent=parent_agent,
            data={
                "subordinate_id": subordinate_agent.agent_name,
                "result_preview": cls._truncate(result, 300),
                "result_length": len(result)
            }
        )
    
    @classmethod
    def trace_monologue_start(cls, agent: "Agent") -> None:
        """Trace start of agent monologue"""
        if not cls._enabled:
            return
        
        cls._add_event(
            event_type=TraceEventType.AGENT_MONOLOGUE_START,
            agent=agent,
            data={}
        )
    
    @classmethod
    def trace_monologue_end(cls, agent: "Agent", 
                           iterations: int,
                           final_response: Optional[str] = None) -> None:
        """Trace end of agent monologue"""
        if not cls._enabled:
            return
        
        cls._add_event(
            event_type=TraceEventType.AGENT_MONOLOGUE_END,
            agent=agent,
            data={
                "iterations": iterations,
                "final_response_preview": cls._truncate(final_response or "", 300) if final_response else None
            }
        )
    
    @classmethod
    def trace_message_loop_iteration(cls, agent: "Agent", iteration: int) -> None:
        """Trace message loop iteration"""
        if not cls._enabled:
            return
        
        cls._add_event(
            event_type=TraceEventType.AGENT_MESSAGE_LOOP_ITERATION,
            agent=agent,
            data={
                "iteration": iteration
            }
        )
    
    @classmethod
    def trace_error(cls, agent: "Agent", error: str, error_type: str = "unknown") -> None:
        """Trace an error"""
        if not cls._enabled:
            return
        
        cls._add_event(
            event_type=TraceEventType.AGENT_ERROR,
            agent=agent,
            data={
                "error": cls._truncate(error, 500),
                "error_type": error_type
            }
        )
    
    @classmethod
    def trace_agent_completed(cls, agent: "Agent", 
                             result: str,
                             total_iterations: int = 0) -> None:
        """Trace agent task completion"""
        if not cls._enabled:
            return
        
        cls._add_event(
            event_type=TraceEventType.AGENT_COMPLETED,
            agent=agent,
            data={
                "result_preview": cls._truncate(result, 300),
                "result_length": len(result),
                "total_iterations": total_iterations
            }
        )
    
    @classmethod
    def get_current_trace(cls) -> Optional[AgentTrace]:
        """Get the current trace session"""
        return cls._current_trace
    
    # =========================================================================
    # Trace Event Methods
    # =========================================================================
    
    @classmethod
    def _add_event(cls, event_type: TraceEventType, agent: "Agent", data: Dict[str, Any]) -> None:
        """Add an event to the current trace"""
        current_trace = cls._current_trace
        if not current_trace:
            return
        
        event = TraceEvent(
            event_type=event_type,
            timestamp=cls._get_timestamp(),
            agent_id=agent.agent_name,
            agent_number=agent.number,
            data=data,
            trace_id=current_trace.trace_id
        )
        
        current_trace.events.append(event)
        
        # Write to JSONL (ADR-088) — machine-readable trace output
        if cls._jsonl_writer:
            profile = ""
            chat_id = ""
            project = ""
            try:
                if hasattr(agent, 'config') and hasattr(agent.config, 'profile'):
                    profile = agent.config.profile or ""
                if hasattr(agent, 'context') and agent.context:
                    chat_id = getattr(agent.context, 'id', '') or ""
                project = agent.data.get('_active_project_name', '') if hasattr(agent, 'data') else ''
            except Exception:
                pass
            cls._jsonl_writer.write_event(
                evt=event.event_type.value,
                agent_id=event.agent_id,
                agent_num=event.agent_number,
                profile=profile,
                data=event.data,
                chat_id=chat_id,
                project=project
            )
        
        # Console output
        if cls._console_output:
            cls._print_event(event)
        
        # Log to file
        if cls._log_to_file and cls._file_logger:
            cls._log_event_to_file(event)
        
        # Log to context
        if cls._log_to_context and agent.context:
            cls._log_to_agent_context(agent, event)
    
    @classmethod
    def _print_event(cls, event: TraceEvent) -> None:
        """Print event to console with formatting"""
        indent = "  " * event.agent_number
        
        # Color codes for different event types
        colors = {
            TraceEventType.AGENT_CREATED: "\033[92m",  # Green
            TraceEventType.AGENT_TASK_ASSIGNED: "\033[94m",  # Blue
            TraceEventType.AGENT_PROMPT_BUILT: "\033[90m",  # Gray
            TraceEventType.AGENT_LLM_CALL_START: "\033[93m",  # Yellow
            TraceEventType.AGENT_LLM_RESPONSE: "\033[93m",  # Yellow
            TraceEventType.AGENT_TOOL_CALLED: "\033[95m",  # Magenta
            TraceEventType.AGENT_TOOL_RESULT: "\033[95m",  # Magenta
            TraceEventType.AGENT_SUBORDINATE_CREATED: "\033[96m",  # Cyan
            TraceEventType.AGENT_SUBORDINATE_COMPLETED: "\033[96m",  # Cyan
            TraceEventType.AGENT_ERROR: "\033[91m",  # Red
            TraceEventType.AGENT_COMPLETED: "\033[92m",  # Green
        }
        reset = "\033[0m"
        color = colors.get(event.event_type, "")
        
        # Format based on event type
        if event.event_type == TraceEventType.AGENT_CREATED:
            msg = f"[TRACE] {indent}🤖 {event.agent_id} CREATED (profile: {event.data.get('profile', 'default')})"
        elif event.event_type == TraceEventType.AGENT_TASK_ASSIGNED:
            msg = f"[TRACE] {indent}📋 {event.agent_id} TASK: {event.data.get('message', '')[:100]}..."
        elif event.event_type == TraceEventType.AGENT_PROMPT_BUILT:
            msg = f"[TRACE] {indent}📝 {event.agent_id} PROMPT BUILT ({event.data.get('system_prompt_length', 0)} chars, {event.data.get('history_messages', 0)} history msgs)"
        elif event.event_type == TraceEventType.AGENT_LLM_CALL_START:
            msg = f"[TRACE] {indent}🔄 {event.agent_id} LLM CALL → {event.data.get('model', 'unknown')}"
        elif event.event_type == TraceEventType.AGENT_LLM_RESPONSE:
            duration = event.data.get('duration_ms')
            dur_str = f" ({duration:.0f}ms)" if duration else ""
            msg = f"[TRACE] {indent}✅ {event.agent_id} LLM RESPONSE{dur_str}: {event.data.get('response_preview', '')[:80]}..."
        elif event.event_type == TraceEventType.AGENT_TOOL_CALLED:
            msg = f"[TRACE] {indent}🔧 {event.agent_id} TOOL: {event.data.get('tool_name', 'unknown')}"
        elif event.event_type == TraceEventType.AGENT_TOOL_RESULT:
            success = "✅" if event.data.get('success', True) else "❌"
            msg = f"[TRACE] {indent}{success} {event.agent_id} TOOL RESULT: {event.data.get('result_preview', '')[:80]}..."
        elif event.event_type == TraceEventType.AGENT_SUBORDINATE_CREATED:
            msg = f"[TRACE] {indent}👶 {event.agent_id} → CREATED SUB-AGENT {event.data.get('subordinate_id', '?')}"
        elif event.event_type == TraceEventType.AGENT_SUBORDINATE_COMPLETED:
            msg = f"[TRACE] {indent}👶 {event.agent_id} ← SUB-AGENT {event.data.get('subordinate_id', '?')} COMPLETED"
        elif event.event_type == TraceEventType.AGENT_ERROR:
            msg = f"[TRACE] {indent}❌ {event.agent_id} ERROR: {event.data.get('error', '')[:100]}"
        elif event.event_type == TraceEventType.AGENT_COMPLETED:
            msg = f"[TRACE] {indent}🏁 {event.agent_id} COMPLETED ({event.data.get('total_iterations', 0)} iterations)"
        elif event.event_type == TraceEventType.AGENT_MONOLOGUE_START:
            msg = f"[TRACE] {indent}▶️ {event.agent_id} MONOLOGUE START"
        elif event.event_type == TraceEventType.AGENT_MONOLOGUE_END:
            msg = f"[TRACE] {indent}⏹️ {event.agent_id} MONOLOGUE END ({event.data.get('iterations', 0)} iterations)"
        elif event.event_type == TraceEventType.AGENT_MESSAGE_LOOP_ITERATION:
            msg = f"[TRACE] {indent}🔁 {event.agent_id} ITERATION {event.data.get('iteration', 0)}"
        else:
            msg = f"[TRACE] {indent}{event.agent_id} {event.event_type.value}"
        
        print(f"{color}{msg}{reset}", file=sys.stderr)
    
    @classmethod
    def _log_to_agent_context(cls, agent: "Agent", event: TraceEvent) -> None:
        """Log trace event to agent's context log"""
        try:
            # Only log significant events to context to avoid spam
            # AGENT_CREATED is intentionally excluded - it clutters UI since every message creates an agent
            significant_events = {
                TraceEventType.AGENT_TASK_ASSIGNED,
                TraceEventType.AGENT_SUBORDINATE_CREATED,
                TraceEventType.AGENT_SUBORDINATE_COMPLETED,
                TraceEventType.AGENT_ERROR,
                TraceEventType.AGENT_COMPLETED,
            }
            
            if event.event_type not in significant_events:
                return
            
            print(f"[DEBUG] Tracing event to context: {event.event_type.value} for agent {agent.agent_name}", flush=True, file=sys.stderr)
            
            # Use update_progress="none" to prevent trace events from appearing in progress bar
            agent.context.log.log(
                type="info",
                heading=f"[TRACE] {event.event_type.value}",
                content=json.dumps(event.data, indent=2, default=str),
                kvps={"agent": event.agent_id, "trace_id": event.trace_id},
                update_progress="none",  # Don't show trace events in progress bar
                verbose=True
            )
            print(f"[DEBUG] Tracing event logged successfully", flush=True, file=sys.stderr)
        except Exception as e:
            print(f"[DEBUG] Error logging trace event to context: {e}", flush=True, file=sys.stderr)
            pass  # Don't let logging errors break the flow
    
    @classmethod
    def _write_trace_file(cls, trace: AgentTrace) -> None:
        """Write trace to JSON file"""
        try:
            path = Path(cls._trace_file)  # type: ignore
            path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(path, 'w') as f:
                f.write(trace.to_json())
            
            if cls._console_output:
                print(f"[TRACE] Trace written to: {path}", file=sys.stderr)
        except Exception as e:
            print(f"[TRACE] Error writing trace file: {e}", file=sys.stderr)
    
    @classmethod
    def _get_timestamp(cls) -> str:
        """Get current timestamp in ISO format"""
        return datetime.now(timezone.utc).isoformat()
    
    @classmethod
    def _truncate(cls, text: str, max_len: int) -> str:
        """Truncate text to max length"""
        if len(text) <= max_len:
            return text
        return text[:max_len] + "..."
    
    @classmethod
    def _print_header(cls, title: str) -> None:
        """Print a header line"""
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"  {title}", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)
    
    @classmethod
    def _setup_file_logger(cls) -> None:
        """Set up file logger for trace events"""
        try:
            # Create logs directory if it doesn't exist
            logs_dir = Path(__file__).parent.parent.parent / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            
            # Create a unique log file for this trace session
            current_trace = cls._current_trace
            trace_id = current_trace.trace_id if current_trace else "unknown"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = logs_dir / f"agent_trace_{timestamp}_{trace_id}.log"
            
            # Set up logger
            cls._file_logger = logging.getLogger(f"agent_tracer_{trace_id}")
            cls._file_logger.setLevel(logging.DEBUG)
            cls._file_logger.handlers = []  # Clear any existing handlers
            
            # File handler
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(logging.DEBUG)
            
            # Format: timestamp | trace_id | agent | event_type | details
            formatter = logging.Formatter(
                '%(asctime)s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(formatter)
            cls._file_logger.addHandler(file_handler)
            
            # Log session start
            cls._file_logger.info(f"=== TRACE SESSION STARTED === trace_id={trace_id}")
            
            if cls._console_output:
                print(f"  Log file: {log_file}", file=sys.stderr)
                
        except Exception as e:
            print(f"[TRACE] Warning: Could not set up file logger: {e}", file=sys.stderr)
            cls._file_logger = None
    
    @classmethod
    def _log_event_to_file(cls, event: TraceEvent) -> None:
        """Log a trace event to the log file"""
        if not cls._file_logger:
            return
        
        try:
            # Format: trace_id | agent_id | event_type | key details
            indent = "  " * event.agent_number
            
            # Build detail string based on event type
            details = ""
            if event.event_type == TraceEventType.AGENT_CREATED:
                details = f"profile={event.data.get('profile', 'default')}, parent={event.data.get('parent_agent', 'none')}"
            elif event.event_type == TraceEventType.AGENT_TASK_ASSIGNED:
                msg = event.data.get('message', '')[:100]
                details = f"message={msg}..."
            elif event.event_type == TraceEventType.AGENT_TOOL_CALLED:
                details = f"tool={event.data.get('tool_name', 'unknown')}, args={event.data.get('tool_args', {})}"
            elif event.event_type == TraceEventType.AGENT_TOOL_RESULT:
                success = "✓" if event.data.get('success', True) else "✗"
                details = f"tool={event.data.get('tool_name', 'unknown')}, success={success}"
            elif event.event_type == TraceEventType.AGENT_SUBORDINATE_CREATED:
                details = f"subordinate={event.data.get('subordinate_id', '?')}, mission={event.data.get('mission', '')[:50]}..."
            elif event.event_type == TraceEventType.AGENT_SUBORDINATE_COMPLETED:
                details = f"subordinate={event.data.get('subordinate_id', '?')}"
            elif event.event_type == TraceEventType.AGENT_MONOLOGUE_END:
                details = f"iterations={event.data.get('iterations', 0)}"
            elif event.event_type == TraceEventType.AGENT_MESSAGE_LOOP_ITERATION:
                details = f"iteration={event.data.get('iteration', 0)}"
            elif event.event_type == TraceEventType.AGENT_ERROR:
                details = f"error={event.data.get('error', '')[:100]}"
            elif event.event_type == TraceEventType.AGENT_COMPLETED:
                details = f"iterations={event.data.get('total_iterations', 0)}"
            
            log_line = f"{event.trace_id} | {indent}{event.agent_id} | {event.event_type.value} | {details}"
            cls._file_logger.info(log_line)
            
        except Exception as e:
            pass  # Don't let logging errors break the flow


# Convenience functions for direct import
def enable_tracing(trace_file: Optional[str] = None, 
                   console_output: bool = True,
                   log_to_context: bool = True) -> None:
    """Enable agent tracing"""
    AgentTracer.enable(trace_file, console_output, log_to_context)


def disable_tracing() -> Optional[AgentTrace]:
    """Disable agent tracing and return the trace"""
    return AgentTracer.disable()


def is_tracing_enabled() -> bool:
    """Check if tracing is enabled"""
    return AgentTracer.is_enabled()


def cleanup_stale_log_files(
    log_dir: Optional[Path] = None,
    max_age_hours: int = 24,
    max_shell_size: int = 200,
) -> int:
    """Remove empty HTML shell files from the logs directory (R-11).

    "Empty shells" are HTML log files smaller than *max_shell_size* bytes
    that contain only the initial boilerplate (e.g. ``Preparing
    environment...``).  These pollute the log directory and have zero
    diagnostic value.

    Args:
        log_dir: Path to the logs directory.  Defaults to ``<project>/logs/``.
        max_age_hours: Only remove files older than this many hours.
            Pass ``0`` to remove regardless of age (useful for tests).
        max_shell_size: Files smaller than this are considered empty shells.

    Returns:
        Number of files removed.
    """
    if log_dir is None:
        log_dir = Path(__file__).parent.parent.parent / "logs"

    log_dir = Path(log_dir)
    if not log_dir.is_dir():
        return 0

    import time
    now = time.time()
    cutoff = now - (max_age_hours * 3600)
    removed = 0

    for f in log_dir.glob("log_*.html"):
        try:
            if f.stat().st_size < max_shell_size:
                if max_age_hours == 0 or f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
        except Exception:
            pass

    return removed
