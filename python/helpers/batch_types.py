"""
Batch Delegation Types, Constants, and Utility Functions

Extracted from call_subordinate_batch.py during P1.2 modularization.
Contains all pure data structures (enums, dataclasses), module-level
constants, and stateless utility functions used by the batch delegation system.

No class method dependencies — all functions are standalone.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agix.batch_subordinate")

# ── Module-level constants ──

# Time in seconds before a parent is considered inactive/crashed
# Increased from 1200→3600 in iter 62: batch runs routinely take 20-30 min,
# and the old timeout killed active subordinates prematurely.
PARENT_HEARTBEAT_TIMEOUT = 3600  

# Minimum allowed batch task timeout (seconds)
_MIN_BATCH_TIMEOUT = 30.0
# Default batch task timeout (seconds)
# RCA-264: Raised from 600→900. 10min was too low for real coding tasks;
# complex integration tasks were being cancelled at 601s.
_DEFAULT_BATCH_TIMEOUT = 900.0
# Hard ceiling for parallel subordinates — each spawns MCP+DB+threads
# RC-18→RC-19: Raised from 3→9 to enable full multiagentdev parallelism.
# Thread math: idle(~75) + 9*8 = 147. Sequential threshold raised to 150.
_MAX_PARALLEL_SUBORDINATES = 9
# Thread count above which we force SEQUENTIAL mode
# RC-18→RC-19: Was 85→150. Idle baseline is ~70-75 threads.
# Each subordinate adds ~5-8 threads. At 9 parallel: 75+(9*8)=147.
# Setting 150 allows full 9-agent parallelism with minimal headroom.
# The THREAD_WARN_THRESHOLD (500) in thread_monitor.py is the hard safety net.
_SEQUENTIAL_THREAD_THRESHOLD = 150

# ── Fix #1 (RCA-2026-04-18): Absolute timeout fallback ──
# Per-task stall threshold in seconds. A RUNNING task with no activity
# beyond this duration is considered stalled.
# Fix #2 (RCA-2026-04-18): Per-task stall detection.
TASK_STALL_THRESHOLD = 300  # 5 minutes

# ── Fix #3 (RCA-2026-04-20): Batch-level absolute timeout ──
# asyncio.gather() has NO timeout. If a single subordinate hangs (e.g.,
# dead HTTPS connection + nest_asyncio preventing wait_for cancellation),
# the entire batch hangs forever. Observed: Front agent hung 25+ hours.
# This ceiling wraps the ENTIRE asyncio.wait() call.
BATCH_ABSOLUTE_TIMEOUT = 1800.0  # 30 minutes hard ceiling
_BATCH_TIMEOUT_MULTIPLIER = 1.5  # Safety margin over max task timeout

# ── Iteration 23: Batch-level auto-retry constants ──
_DEFAULT_MAX_TASK_RETRIES = 1  # How many times to retry a failed/timed-out task


# ── Enums ──

class BatchExecutionMode(Enum):
    """Execution modes for batch delegation."""
    SEQUENTIAL = "sequential"  # One at a time (fallback)
    PARALLEL = "parallel"      # All at once (up to max_concurrent)
    WAVE = "wave"              # Dependency-based waves
    ADAPTIVE = "adaptive"      # Auto-select based on task analysis


class TaskStatus(Enum):
    """Status of individual tasks in batch."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


# ── Dataclasses ──

@dataclass
class BatchTask:
    """Individual task in a batch delegation."""
    id: str
    message: str
    profile: Optional[str] = None
    priority: int = 0
    dependencies: List[str] = field(default_factory=list)
    timeout: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Runtime state
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    agent_number: Optional[int] = None
    # RCA Fix #2: Per-task stall detection — tracks last observed activity
    last_activity_time: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "message": self.message,
            "profile": self.profile,
            "priority": self.priority,
            "dependencies": self.dependencies,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "duration": (self.end_time - self.start_time) if self.end_time and self.start_time else None,
            "agent_number": self.agent_number,
        }


@dataclass
class BatchResult:
    """Aggregated result from batch delegation."""
    batch_id: str
    total_tasks: int
    completed: int
    failed: int
    timeout: int
    cancelled: int
    tasks: List[BatchTask]
    aggregated_result: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    
    @property
    def success_rate(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return self.completed / self.total_tasks
    
    @property
    def duration(self) -> Optional[float]:
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "total_tasks": self.total_tasks,
            "completed": self.completed,
            "failed": self.failed,
            "timeout": self.timeout,
            "cancelled": self.cancelled,
            "success_rate": self.success_rate,
            "duration": self.duration,
            "tasks": [t.to_dict() for t in self.tasks],
            "aggregated_result": self.aggregated_result,
        }


# ── Pure utility functions ──

def _is_non_retriable(task) -> bool:
    """Check if a task should NOT be retried.
    
    Tasks that hit structural limits (ITERATION_LIMIT, CHAIN_LIMIT,
    RESTART_LIMIT) or exhausted rate-limit retries should not be retried
    at the batch level — the root cause won't change.
    
    Iteration 23: Required for batch auto-retry to skip hopeless tasks.
    
    Args:
        task: A BatchTask instance.
    
    Returns:
        True if the task should NOT be retried.
    """
    error_str = str(task.error or "")
    result_str = str(task.result or "")
    combined = error_str + result_str
    
    # Structural limits — retrying won't help
    non_retriable_tags = [
        "[ITERATION_LIMIT]",
        "[CHAIN_LIMIT]",
        "[RESTART_LIMIT]",
    ]
    for tag in non_retriable_tags:
        if tag in combined:
            return True
    
    # Rate limit exhaustion — already retried internally
    if "exhausted retry limit" in combined.lower():
        return True
    if "rate limit" in error_str.lower() and "retries" in error_str.lower():
        return True
    
    return False


def compute_batch_timeout(task_timeouts: list[float]) -> float:
    """Derive batch-level timeout from per-task timeouts.
    
    For parallel execution, the batch timeout is:
        max(task_timeouts) * multiplier, capped at BATCH_ABSOLUTE_TIMEOUT.
    
    For empty lists, returns _DEFAULT_BATCH_TIMEOUT.
    
    Args:
        task_timeouts: List of per-task timeout values (seconds).
    
    Returns:
        Batch timeout in seconds. Always > 0, always <= BATCH_ABSOLUTE_TIMEOUT.
    """
    if not task_timeouts:
        return _DEFAULT_BATCH_TIMEOUT
    max_timeout = max(task_timeouts)
    computed = max_timeout * _BATCH_TIMEOUT_MULTIPLIER
    return min(computed, BATCH_ABSOLUTE_TIMEOUT)



def get_effective_timeout(timeout: Optional[float]) -> float:
    """Return the effective timeout, falling back to _DEFAULT_BATCH_TIMEOUT.
    
    RCA Fix #1: The original code had a falsy-value gap — if task.timeout
    was None or 0, `asyncio.wait_for` was skipped entirely and the subordinate
    monologue ran without any timeout. This caused infinite hangs.
    
    Args:
        timeout: The requested timeout value (may be None, 0, or negative).
    
    Returns:
        A positive float timeout in seconds. Always > 0.
    """
    if timeout is not None and timeout > 0:
        return float(timeout)
    return _DEFAULT_BATCH_TIMEOUT


def detect_stalled_tasks(tasks: list) -> list:
    """Detect RUNNING tasks that haven't had activity within TASK_STALL_THRESHOLD.
    
    RCA Fix #2: The orphan monitor only checked aggregate parent health,
    not individual task progress. A stuck subordinate stayed RUNNING forever.
    
    Args:
        tasks: List of BatchTask instances.
    
    Returns:
        List of BatchTask instances that are stalled (RUNNING but stale).
    """
    if not tasks:
        return []
    
    now = time.time()
    stalled = []
    for task in tasks:
        if task.status == TaskStatus.RUNNING and task.last_activity_time > 0:
            elapsed = now - task.last_activity_time
            if elapsed > TASK_STALL_THRESHOLD:
                stalled.append(task)
    return stalled


def get_batch_task_timeout() -> float:
    """Get the batch task timeout from AGIX_BATCH_TASK_TIMEOUT env var.
    
    Returns:
        Timeout in seconds. Defaults to 600.0 (10 minutes).
        Falls back to 600.0 for invalid, negative, zero, or empty values.
        Clamps to minimum of 30.0 seconds.
    """
    raw = os.environ.get("AGIX_BATCH_TASK_TIMEOUT", "")
    if not raw.strip():
        return _DEFAULT_BATCH_TIMEOUT
    try:
        value = float(raw)
        if value <= 0:
            logger.warning(
                f"AGIX_BATCH_TASK_TIMEOUT={raw} is not positive, "
                f"using default {_DEFAULT_BATCH_TIMEOUT}s"
            )
            return _DEFAULT_BATCH_TIMEOUT
        if value < _MIN_BATCH_TIMEOUT:
            logger.warning(
                f"AGIX_BATCH_TASK_TIMEOUT={raw} below minimum {_MIN_BATCH_TIMEOUT}s, "
                f"clamping to {_MIN_BATCH_TIMEOUT}s"
            )
            return _MIN_BATCH_TIMEOUT
        return value
    except (ValueError, TypeError):
        logger.warning(
            f"AGIX_BATCH_TASK_TIMEOUT={raw!r} is not a valid number, "
            f"using default {_DEFAULT_BATCH_TIMEOUT}s"
        )
        return _DEFAULT_BATCH_TIMEOUT


# ── RCA-264: Keyword-based timeout tiers ──
# Ordered from highest to lowest timeout so max() picks the right one.
_TIMEOUT_TIERS = [
    # HIGH — complex multi-step tasks
    {
        "timeout": 1200.0,
        "label": "HIGH",
        "keywords": [
            "integration", "deploy", "deployment", "push to github",
            "git push", "scaffold", "full-stack", "full stack",
            "production", "release", "publish",
        ],
    },
    # MEDIUM — implementation/build tasks
    {
        "timeout": 900.0,
        "label": "MEDIUM",
        "keywords": [
            "frontend", "backend", "implement", "implementation",
            "build", "compile", "create all", "develop",
        ],
    },
    # LOW — simple config/setup tasks
    {
        "timeout": 300.0,
        "label": "LOW",
        "keywords": [
            "set secret", "set the", "configure", "environment",
            "secret_set", "secrets",
        ],
    },
]

# Map timeout values to tier labels for reverse lookup
_TIMEOUT_TO_LABEL = {tier["timeout"]: tier["label"] for tier in _TIMEOUT_TIERS}


def estimate_task_timeout(message: str) -> float:
    """Estimate an appropriate timeout based on task message keywords.

    RCA-264: The batch system was killing complex tasks because all tasks
    got the same 600s timeout. A 'set secrets' task (~30s) and a
    'final integration + build + fix + push' task (~20min) were treated
    identically.

    This function scans the task message for complexity-indicating keywords
    and returns the highest matching timeout tier.

    Args:
        message: The task message/instruction text.

    Returns:
        Estimated timeout in seconds. Always > 0.
    """
    if not message:
        return _DEFAULT_BATCH_TIMEOUT

    msg_lower = message.lower()
    matched_timeouts = []

    for tier in _TIMEOUT_TIERS:
        for keyword in tier["keywords"]:
            if keyword in msg_lower:
                matched_timeouts.append(tier["timeout"])
                break  # Found a match in this tier, check next tier

    if not matched_timeouts:
        return _DEFAULT_BATCH_TIMEOUT

    # Use the HIGHEST matching tier (e.g., if both HIGH and LOW match,
    # the task is complex enough to need the HIGH timeout)
    return float(max(matched_timeouts))


def estimate_task_timeout_with_tier(message: str) -> tuple:
    """Estimate timeout and return the tier label alongside the value.

    Similar to estimate_task_timeout but also returns which tier was matched.
    This is used by build_budget_message to tell subordinates their tier.

    Args:
        message: The task message/instruction text.

    Returns:
        Tuple of (timeout_seconds: float, tier_label: str).
        Tier label is one of 'HIGH', 'MEDIUM', 'LOW', or 'DEFAULT'.
    """
    if not message:
        return (_DEFAULT_BATCH_TIMEOUT, "DEFAULT")

    msg_lower = message.lower()
    matched = []  # List of (timeout, label) tuples

    for tier in _TIMEOUT_TIERS:
        for keyword in tier["keywords"]:
            if keyword in msg_lower:
                matched.append((tier["timeout"], tier["label"]))
                break

    if not matched:
        return (_DEFAULT_BATCH_TIMEOUT, "DEFAULT")

    # Return the highest matching tier
    best = max(matched, key=lambda x: x[0])
    return (float(best[0]), best[1])

def build_budget_message(
    profile: str,
    timeout_seconds: float,
    max_iterations: int,
    timeout_tier: str = "",
) -> str:
    """Build a budget awareness message to inject into subordinate task messages.
    
    This tells the subordinate about its iteration and time limits so it can
    plan its work within the available budget rather than hitting the hard
    limit silently.
    
    Forgejo #370: Budget-aware subordinate injection.
    RCA-264 Part 2: Now includes timeout tier label so subordinates know
    their complexity classification.
    
    Args:
        profile: The agent profile (e.g. 'code', 'browser', 'e2e')
        timeout_seconds: Task timeout in seconds
        max_iterations: Maximum iteration count for this profile
        timeout_tier: Optional tier label ('HIGH', 'MEDIUM', 'LOW', 'DEFAULT')
    
    Returns:
        A formatted budget message string to prepend to task instructions.
    """
    timeout_minutes = int(timeout_seconds / 60)
    
    tier_info = ""
    if timeout_tier:
        tier_info = f"- **Timeout tier**: {timeout_tier} (auto-estimated from task complexity)\n"
    
    return (
        f"\n\n---\n"
        f"**⏱️ Budget Constraints** (plan your work within these limits):\n"
        f"- **Iteration budget**: {max_iterations} iterations maximum\n"
        f"- **Time budget**: ~{timeout_minutes} minutes ({int(timeout_seconds)}s)\n"
        f"- **Profile**: {profile}\n"
        f"{tier_info}\n"
        f"**📋 Task Planning (MANDATORY)**:\n"
        f"Before starting, list your tasks with estimated iteration counts. "
        f"If total estimated iterations > 40, split into MUST-DO (essential) "
        f"and DEFER (nice-to-have). Complete all MUST-DO items first. "
        f"At 60% of your iteration budget, stop new work and prepare your response.\n\n"
        f"**Priority guidance**: Focus on the highest-priority items first. "
        f"Do NOT attempt open-ended exploration or re-verification loops — "
        f"plan concrete steps and execute them efficiently. "
        f"If a quality gate has already passed, do NOT re-run it.\n"
        f"---\n"
    )


# Lifecycle phase keywords for ordering
_PUBLISH_DEPLOY_KEYWORDS = [
    "deploy", "publish", "push to github", "push code",
    "git push", "release", "production",
]
_VERIFY_TEST_KEYWORDS = [
    "verify", "test", "e2e", "uat", "check", "validate", "qa",
]


def enforce_lifecycle_ordering(
    tasks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Reorder tasks to enforce lifecycle dependencies.
    
    Ensures that publish/deploy/push tasks always come AFTER verification
    and testing tasks. Build tasks remain first.
    
    Forgejo #371: Lifecycle dependency inference.
    
    Args:
        tasks: List of task dicts with 'id', 'message', 'profile' keys
    
    Returns:
        Reordered list of tasks (does not modify the original).
    """
    if not tasks or len(tasks) <= 1:
        return list(tasks)
    
    publish_tasks = []
    verify_tasks = []
    other_tasks = []
    
    for task in tasks:
        msg_lower = task.get("message", "").lower()
        
        # Check if this is a publish/deploy task
        is_publish = any(kw in msg_lower for kw in _PUBLISH_DEPLOY_KEYWORDS)
        is_verify = any(kw in msg_lower for kw in _VERIFY_TEST_KEYWORDS)
        
        if is_publish and not is_verify:
            publish_tasks.append(task)
        elif is_verify and not is_publish:
            verify_tasks.append(task)
        else:
            other_tasks.append(task)
    
    # Order: other (build) → verify → publish
    return other_tasks + verify_tasks + publish_tasks
