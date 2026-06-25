"""
Goal State Module
Persistent representation of user goals for supervisor tracking.
Part of Supervisor Reliability Enhancement - Gap 2.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime, timezone
import uuid
import json
import os
from python.helpers.files import save_json_atomic


class GoalStatus(Enum):
    """Status of a goal."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    VERIFIED = "verified"
    FAILED = "failed"


class SubgoalStatus(Enum):
    """Status of a subgoal."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


@dataclass
class Subgoal:
    """A sub-task within a goal."""
    id: str
    description: str
    status: SubgoalStatus = SubgoalStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None
    evidence: Optional[str] = None  # File path, test output, etc.

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "evidence": self.evidence
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Subgoal":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            description=data["description"],
            status=SubgoalStatus(data.get("status", "pending")),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            completed_at=data.get("completed_at"),
            evidence=data.get("evidence")
        )


@dataclass
class GoalState:
    """
    Persistent representation of a user's goal for an agent context.
    Survives context condensation and is inherited by subordinate agents.
    """
    goal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    context_id: str = ""
    agent_id: str = ""
    
    # Core goal data
    original_prompt: str = ""
    extracted_objective: str = ""  # LLM-extracted single-sentence goal
    success_criteria: List[str] = field(default_factory=list)
    
    # Hierarchical tracking
    subgoals: List[Subgoal] = field(default_factory=list)
    parent_goal_id: Optional[str] = None  # For subordinate agents
    
    # Status tracking
    status: GoalStatus = GoalStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_verified_at: Optional[str] = None
    completion_claimed_at: Optional[str] = None
    verified_complete_at: Optional[str] = None
    
    # Intervention history
    intervention_count: int = 0
    last_intervention_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "goal_id": self.goal_id,
            "context_id": self.context_id,
            "agent_id": self.agent_id,
            "original_prompt": self.original_prompt,
            "extracted_objective": self.extracted_objective,
            "success_criteria": self.success_criteria,
            "subgoals": [sg.to_dict() for sg in self.subgoals],
            "parent_goal_id": self.parent_goal_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "last_verified_at": self.last_verified_at,
            "completion_claimed_at": self.completion_claimed_at,
            "verified_complete_at": self.verified_complete_at,
            "intervention_count": self.intervention_count,
            "last_intervention_reason": self.last_intervention_reason
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GoalState":
        """Create from dictionary."""
        subgoals = [Subgoal.from_dict(sg) for sg in data.get("subgoals", [])]
        return cls(
            goal_id=data.get("goal_id", str(uuid.uuid4())),
            context_id=data.get("context_id", ""),
            agent_id=data.get("agent_id", ""),
            original_prompt=data.get("original_prompt", ""),
            extracted_objective=data.get("extracted_objective", ""),
            success_criteria=data.get("success_criteria", []),
            subgoals=subgoals,
            parent_goal_id=data.get("parent_goal_id"),
            status=GoalStatus(data.get("status", "pending")),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            last_verified_at=data.get("last_verified_at"),
            completion_claimed_at=data.get("completion_claimed_at"),
            verified_complete_at=data.get("verified_complete_at"),
            intervention_count=data.get("intervention_count", 0),
            last_intervention_reason=data.get("last_intervention_reason")
        )

    def save(self, base_path: str = "/agix/work_dir", project_dir: str = None) -> str:
        """Persist goal state to disk.
        
        Args:
            base_path: Legacy base path for goal storage (default: /agix/work_dir).
                       Used when project_dir is not provided.
            project_dir: Project directory path. When provided, saves to
                         {project_dir}/.agix.proj/goal_states/ instead of
                         {base_path}/.goal_states/. Takes priority over base_path.
        
        Returns:
            The absolute file path where the goal state was saved.
        """
        if project_dir:
            goal_dir = os.path.join(project_dir, ".agix.proj", "goal_states")
        else:
            goal_dir = os.path.join(base_path, ".goal_states")
        os.makedirs(goal_dir, exist_ok=True)
        
        file_path = os.path.join(goal_dir, f"{self.context_id}_{self.goal_id}.json")
        save_json_atomic(file_path, self.to_dict())
        return file_path

    @classmethod
    def load(cls, context_id: str, goal_id: str = None, base_path: str = "/agix/work_dir", project_dir: str = None) -> Optional["GoalState"]:
        """Load goal state from disk.
        
        Args:
            context_id: The context ID to load the goal for.
            goal_id: Optional specific goal ID. If None, loads most recent.
            base_path: Legacy base path for goal storage (default: /agix/work_dir).
            project_dir: Project directory path. When provided, tries loading from
                         {project_dir}/.agix.proj/goal_states/ first, then falls
                         back to {base_path}/.goal_states/.
        
        Returns:
            GoalState if found, None otherwise.
        """
        # Build search directories in priority order
        search_dirs = []
        if project_dir:
            search_dirs.append(os.path.join(project_dir, ".agix.proj", "goal_states"))
        search_dirs.append(os.path.join(base_path, ".goal_states"))
        
        for goal_dir in search_dirs:
            result = cls._load_from_dir(context_id, goal_id, goal_dir)
            if result is not None:
                return result
        return None

    @classmethod
    def _load_from_dir(cls, context_id: str, goal_id: str, goal_dir: str) -> Optional["GoalState"]:
        """Load goal state from a specific directory.
        
        Args:
            context_id: The context ID to load the goal for.
            goal_id: Optional specific goal ID.
            goal_dir: Directory containing goal JSON files.
        
        Returns:
            GoalState if found, None otherwise.
        """
        if goal_id:
            file_path = os.path.join(goal_dir, f"{context_id}_{goal_id}.json")
            if os.path.exists(file_path):
                with open(file_path, "r") as f:
                    return cls.from_dict(json.load(f))
        else:
            # Find most recent goal for this context
            pattern = f"{context_id}_"
            candidates = []
            if os.path.exists(goal_dir):
                for fname in os.listdir(goal_dir):
                    if fname.startswith(pattern) and fname.endswith(".json"):
                        fpath = os.path.join(goal_dir, fname)
                        candidates.append((os.path.getmtime(fpath), fpath))
            if candidates:
                candidates.sort(reverse=True)
                with open(candidates[0][1], "r") as f:
                    return cls.from_dict(json.load(f))
        return None

    def mark_subgoal_complete(self, subgoal_id: str, evidence: str = None) -> None:
        """Mark a subgoal as completed."""
        for sg in self.subgoals:
            if sg.id == subgoal_id:
                sg.status = SubgoalStatus.COMPLETED
                sg.completed_at = datetime.now(timezone.utc).isoformat()
                sg.evidence = evidence
                # RCA-475 E2: SM wrap
                self._wire_subgoal_sm(sg, "completed", "mark_subgoal_complete")
                break

    def mark_subgoal_in_progress(self, subgoal_id: str) -> None:
        """Mark a subgoal as in progress."""
        for sg in self.subgoals:
            if sg.id == subgoal_id:
                sg.status = SubgoalStatus.IN_PROGRESS
                # RCA-475 E2: SM wrap
                self._wire_subgoal_sm(sg, "in_progress", "mark_subgoal_in_progress")
                break

    @staticmethod
    def _wire_subgoal_sm(sg: "Subgoal", target_status: str, source_method: str) -> None:
        """RCA-475 E2: Create/transition SubgoalSM alongside status assignment.

        SM instances live as a transient `_sm` attribute on the Subgoal dataclass.
        Warn-only during migration — never blocks the original assignment.
        """
        import logging
        from python.helpers.state_machines.goal_sm import SubgoalSM

        _logger = logging.getLogger("agix.goal_state")

        sm = getattr(sg, "_sm", None)
        if sm is None:
            sm = SubgoalSM(entity_id=sg.id)
            sg._sm = sm  # type: ignore[attr-defined]

        if sm.status == target_status:
            return  # idempotent

        ok, msg = sm.transition(
            target_status,
            reason=source_method,
            source="goal_state.py",
        )
        if not ok:
            _logger.warning("[SUBGOAL SM] %s — status set anyway (migration mode)", msg)
            sm.transition(
                target_status,
                reason=f"force-sync: {msg}",
                source="goal_state.py",
                force=True,
            )

    def get_progress_summary(self) -> str:
        """Generate a human-readable progress summary."""
        total = len(self.subgoals)
        completed = sum(1 for sg in self.subgoals if sg.status == SubgoalStatus.COMPLETED)
        in_progress = sum(1 for sg in self.subgoals if sg.status == SubgoalStatus.IN_PROGRESS)
        
        lines = [
            f"**Goal**: {self.extracted_objective}",
            f"**Status**: {self.status.value}",
            f"**Progress**: {completed}/{total} subgoals complete"
        ]
        
        if in_progress > 0:
            lines.append(f"**In Progress**: {in_progress} subgoals")
        
        if self.subgoals:
            lines.append("\n**Subgoals**:")
            for sg in self.subgoals:
                if sg.status == SubgoalStatus.COMPLETED:
                    icon = "✅"
                elif sg.status == SubgoalStatus.IN_PROGRESS:
                    icon = "🔄"
                else:
                    icon = "⬜"
                lines.append(f"  {icon} {sg.description}")
        
        return "\n".join(lines)

    def get_completion_percentage(self) -> float:
        """Get the percentage of subgoals completed."""
        if not self.subgoals:
            return 0.0
        completed = sum(1 for sg in self.subgoals if sg.status == SubgoalStatus.COMPLETED)
        return (completed / len(self.subgoals)) * 100

    def increment_intervention(self, reason: str = None) -> None:
        """Record an intervention against this goal."""
        self.intervention_count += 1
        self.last_intervention_reason = reason
        self.last_verified_at = datetime.now(timezone.utc).isoformat()
