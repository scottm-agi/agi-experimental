"""
Structured Delegation Handoff Envelope — DelegationResult

Issue: #1160 (GAP-1)

Replaces raw string returns from call_subordinate / fan_out_subordinates
with typed fields that enable deterministic parent routing.

Backward compatible: `.to_string()` produces human-readable output that
the existing system can consume unchanged. The structured fields are a
bonus for deterministic routing and tracing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class DelegationResult:
    """Structured result envelope for agent delegation handoffs.
    
    Replaces raw string returns from call_subordinate / fan_out_subordinates
    with typed fields that enable deterministic parent routing.
    
    Fields:
        status: "success", "partial", or "failed"
        result: The actual response text from the subordinate
        profile: Which agent profile handled this (e.g. "code", "researcher")
        artifacts: File paths, URLs, or other deliverables created
        errors: Error messages encountered during execution
        tokens_used: Approximate token budget consumed
        iterations: Number of monologue loop iterations used
        next_steps: Suggested follow-up actions for the parent agent
    """
    status: Literal["success", "partial", "failed"]
    result: str
    profile: str = ""
    artifacts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    tokens_used: int = 0
    iterations: int = 0
    next_steps: list[str] = field(default_factory=list)
    # Task tracking metadata (Unified Pipeline)
    task_hash: str = ""        # 12-char canonical hash of the task
    sequence_id: int = 0       # Attempt number for this task (1-based)
    task_guid: str = ""        # REQ-xxxx from orchestrator (if provided)

    def to_string(self) -> str:
        """Render as a human-readable string for LLM consumption.
        
        This is the backward-compatible output — same format the parent
        agent currently receives, plus a structured header.
        """
        header_parts = [f"**Status**: {self.status.upper()}"]
        if self.profile:
            header_parts.append(f"**Agent**: {self.profile}")
        if self.iterations:
            header_parts.append(f"**Iterations**: {self.iterations}")
        if self.errors:
            header_parts.append(f"**Errors**: {len(self.errors)}")
        if self.artifacts:
            header_parts.append(f"**Artifacts**: {', '.join(self.artifacts)}")
        if self.task_hash:
            header_parts.append(f"**Task Hash**: `{self.task_hash}`")
        if self.sequence_id:
            header_parts.append(f"**Attempt**: #{self.sequence_id}")
        if self.task_guid:
            header_parts.append(f"**GUID**: `{self.task_guid}`")

        header = " | ".join(header_parts)

        parts = [f"[{header}]", "", self.result]

        if self.errors:
            parts.append("\n**Errors encountered:**")
            for err in self.errors:
                parts.append(f"- {err}")

        if self.next_steps:
            parts.append("\n**Suggested next steps:**")
            for step in self.next_steps:
                parts.append(f"- {step}")

        return "\n".join(parts)

    def to_dict(self) -> dict:
        """Serialize for logging/tracing.
        
        Includes result_length instead of the full result text to avoid
        bloating trace logs with potentially large subordinate outputs.
        """
        return {
            "status": self.status,
            "profile": self.profile,
            "result_length": len(self.result),
            "artifacts": self.artifacts,
            "errors": self.errors,
            "tokens_used": self.tokens_used,
            "iterations": self.iterations,
            "next_steps": self.next_steps,
            "task_hash": self.task_hash,
            "sequence_id": self.sequence_id,
            "task_guid": self.task_guid,
        }

    @property
    def succeeded(self) -> bool:
        """True only when status is exactly 'success'."""
        return self.status == "success"

    @property
    def failed(self) -> bool:
        """True only when status is exactly 'failed'."""
        return self.status == "failed"
