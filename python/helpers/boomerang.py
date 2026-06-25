from __future__ import annotations
"""
Boomerang Orchestration System

Implements RooCode's "Boomerang" workflow pattern:
1. Analyze task complexity
2. Decompose into subtasks
3. Delegate to appropriate mode agents
4. Collect and verify results
5. Run QA (challenger/inspector)
6. Iterate until complete

This integrates the orchestrator.py library into the agent's workflow.

Enhanced with Session Task List integration for persistent task tracking.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from python.helpers.session_tasks import (
    SessionTaskList,
    SessionTask,
    TaskStatus,
    get_or_create_session_tasks,
    save_session_tasks,
)

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger("agix.boomerang")


class TaskComplexity(Enum):
    """Task complexity levels."""
    SIMPLE = "simple"      # Single agent can handle
    MODERATE = "moderate"  # May benefit from delegation
    COMPLEX = "complex"    # Should decompose and delegate


class BoomerangPhase(Enum):
    """Phases of the Boomerang workflow."""
    ANALYZE = "analyze"
    DECOMPOSE = "decompose"
    DELEGATE = "delegate"
    COLLECT = "collect"
    VERIFY = "verify"
    ITERATE = "iterate"
    COMPLETE = "complete"


@dataclass
class SubtaskResult:
    """Result from a delegated subtask."""
    subtask_id: str
    description: str
    mode: str
    result: str
    success: bool
    error: Optional[str] = None
    duration_seconds: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BoomerangState:
    """State of a Boomerang orchestration session."""
    task_id: str
    original_task: str
    phase: BoomerangPhase = BoomerangPhase.ANALYZE
    complexity: TaskComplexity = TaskComplexity.SIMPLE
    subtasks: List[Dict[str, Any]] = field(default_factory=list)
    results: List[SubtaskResult] = field(default_factory=list)
    iterations: int = 0
    max_iterations: int = 3
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    qa_passed: bool = False
    final_result: Optional[str] = None


class BoomerangOrchestrator:
    """
    Orchestrates complex tasks using the Boomerang pattern.
    
    The Boomerang pattern:
    1. Task comes in → analyze complexity
    2. If complex → decompose into subtasks
    3. Delegate subtasks to appropriate mode agents
    4. Collect results → verify quality
    5. If issues found → iterate with fixes
    6. Return final result
    """
    
    # Keywords that suggest task complexity
    COMPLEXITY_INDICATORS = {
        "complex": [
            "build", "create", "implement", "develop", "design and implement",
            "full stack", "end to end", "complete", "entire", "whole",
            "multiple", "several", "various", "different",
            "api", "database", "frontend", "backend", "authentication",
            "test", "deploy", "integrate", "migrate", "ui", "ux", "landing page",
        ],
        "moderate": [
            "add", "update", "modify", "change", "fix",
            "refactor", "improve", "optimize", "enhance",
            "review", "analyze", "document",
        ],
        "simple": [
            "explain", "what is", "how to", "show", "list",
            "find", "search", "check", "verify",
        ],
    }
    
    # Mode suggestions based on subtask type
    # Re-ordered to prioritize specialized modes over generic ones
    MODE_SUGGESTIONS = {
        "ui": "frontend",
        "ux": "frontend",
        "frontend": "frontend",
        "landing page": "frontend",
        "page": "frontend",
        "css": "frontend",
        "style": "frontend",
        "debug": "debug",
        "error": "debug",
        "fix": "debug",
        "review": "review",
        "audit": "review",
        "design": "architect",
        "plan": "architect",
        "architecture": "architect",
        "document": "architect",
        "implement": "code",
        "code": "code",
        "write": "code",
        "test": "code",
        "explain": "ask",
        "question": "ask",
    }
    
    def __init__(self, agent: "Agent"):
        """
        Initialize Boomerang orchestrator.
        
        Args:
            agent: The parent agent that will orchestrate
        """
        self.agent = agent
        self._state: Optional[BoomerangState] = None
        self._session_tasks: Optional[SessionTaskList] = None
        self._enabled = True
    
    @property
    def is_active(self) -> bool:
        """Check if orchestration is currently active."""
        return self._state is not None and self._state.phase != BoomerangPhase.COMPLETE
    
    def analyze_complexity(self, task: str) -> TaskComplexity:
        """
        Analyze task complexity to determine if orchestration is needed.
        
        Args:
            task: The task description
            
        Returns:
            TaskComplexity level
        """
        task_lower = task.lower()
        
        # Count complexity indicators
        complex_count = sum(
            1 for kw in self.COMPLEXITY_INDICATORS["complex"]
            if kw in task_lower
        )
        moderate_count = sum(
            1 for kw in self.COMPLEXITY_INDICATORS["moderate"]
            if kw in task_lower
        )
        simple_count = sum(
            1 for kw in self.COMPLEXITY_INDICATORS["simple"]
            if kw in task_lower
        )
        
        # Check for multiple components mentioned
        component_patterns = [
            r'\b(api|frontend|backend|database|auth|ui|server|client)\b',
            r'\b(and|then|also|plus|with)\b.*\b(and|then|also|plus|with)\b',
        ]
        has_multiple_components = any(
            re.search(pattern, task_lower) for pattern in component_patterns
        )
        
        # Determine complexity
        if complex_count >= 2 or has_multiple_components:
            return TaskComplexity.COMPLEX
        elif complex_count >= 1 or moderate_count >= 2:
            return TaskComplexity.MODERATE
        else:
            return TaskComplexity.SIMPLE
    
    def should_orchestrate(self, task: str) -> bool:
        """
        Determine if a task should use Boomerang orchestration.
        
        Args:
            task: The task description
            
        Returns:
            True if orchestration is recommended
        """
        if not self._enabled:
            return False
        
        complexity = self.analyze_complexity(task)
        return complexity in [TaskComplexity.COMPLEX, TaskComplexity.MODERATE]
    
    async def decompose_task(self, task: str) -> List[Dict[str, Any]]:
        """
        Decompose a complex task into subtasks.
        
        This uses pattern matching and heuristics. In production,
        could use LLM for more intelligent decomposition.
        
        Args:
            task: The task description
            
        Returns:
            List of subtask dictionaries
        """
        subtasks = []
        task_lower = task.lower()
        
        # Check for explicit steps (numbered or bulleted)
        step_patterns = [
            r'(\d+[\.\)]\s*[^\n]+)',  # 1. Step or 1) Step
            r'[-*]\s*([^\n]+)',        # - Step or * Step
        ]
        
        for pattern in step_patterns:
            matches = re.findall(pattern, task)
            if matches:
                for i, match in enumerate(matches):
                    subtasks.append({
                        "id": f"step_{i+1}",
                        "description": match.strip(),
                        "mode": self._suggest_mode(match),
                        "dependencies": [f"step_{i}"] if i > 0 else [],
                    })
                if subtasks:
                    return subtasks
        
        # Auto-decompose based on keywords
        if "design" in task_lower or "architecture" in task_lower:
            subtasks.append({
                "id": "design",
                "description": f"Design the architecture for: {task}",
                "mode": "architect",
                "dependencies": [],
            })
        
        if any(kw in task_lower for kw in ["implement", "build", "create", "code"]):
            subtasks.append({
                "id": "implement",
                "description": f"Implement: {task}",
                "mode": "code",
                "dependencies": ["design"] if "design" in [s["id"] for s in subtasks] else [],
            })
        
        if "test" in task_lower:
            subtasks.append({
                "id": "test",
                "description": f"Test the implementation",
                "mode": "code",
                "dependencies": ["implement"] if "implement" in [s["id"] for s in subtasks] else [],
            })
        
        if "review" in task_lower:
            subtasks.append({
                "id": "review",
                "description": f"Review the code quality",
                "mode": "review",
                "dependencies": ["implement"] if "implement" in [s["id"] for s in subtasks] else [],
            })
        
        # If no subtasks identified, create a single task
        if not subtasks:
            subtasks.append({
                "id": "main",
                "description": task,
                "mode": self._suggest_mode(task),
                "dependencies": [],
            })
        
        return subtasks
    
    def _suggest_mode(self, task_text: str) -> str:
        """
        Suggest appropriate mode for a task.
        
        Args:
            task_text: Task description
            
        Returns:
            Mode slug
        """
        task_lower = task_text.lower()
        
        for keyword, mode in self.MODE_SUGGESTIONS.items():
            if keyword in task_lower:
                return mode
        
        return "code"  # Default to code mode
    
    async def start_orchestration(self, task: str) -> BoomerangState:
        """
        Start Boomerang orchestration for a task.
        
        Args:
            task: The task description
            
        Returns:
            BoomerangState with initial setup
        """
        import uuid
        
        # Analyze complexity
        complexity = self.analyze_complexity(task)
        
        # Decompose task
        subtasks = await self.decompose_task(task)
        
        # Create state
        self._state = BoomerangState(
            task_id=str(uuid.uuid4())[:8],
            original_task=task,
            phase=BoomerangPhase.DECOMPOSE,
            complexity=complexity,
            subtasks=subtasks,
        )
        
        logger.info(
            f"Started Boomerang orchestration: {self._state.task_id} "
            f"with {len(subtasks)} subtasks"
        )
        
        return self._state
    
    async def execute_subtask(
        self,
        subtask: Dict[str, Any],
        delegate_fn: Callable[[str, str], Any],
    ) -> SubtaskResult:
        """
        Execute a single subtask by delegating to appropriate mode.
        
        Args:
            subtask: Subtask dictionary
            delegate_fn: Function to delegate (call_subordinate)
            
        Returns:
            SubtaskResult with outcome
        """
        start_time = datetime.now()
        
        try:
            # Delegate to subordinate with specified mode
            result = await delegate_fn(
                subtask["description"],
                subtask.get("mode", "code"),
            )
            
            duration = (datetime.now() - start_time).total_seconds()
            
            return SubtaskResult(
                subtask_id=subtask["id"],
                description=subtask["description"],
                mode=subtask.get("mode", "code"),
                result=str(result),
                success=True,
                duration_seconds=duration,
            )
            
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            logger.error(f"Subtask {subtask['id']} failed: {e}")
            
            return SubtaskResult(
                subtask_id=subtask["id"],
                description=subtask["description"],
                mode=subtask.get("mode", "code"),
                result="",
                success=False,
                error=str(e),
                duration_seconds=duration,
            )
    
    async def run_qa(self, results: List[SubtaskResult]) -> Dict[str, Any]:
        """
        Run quality assurance on subtask results.
        
        Args:
            results: List of subtask results
            
        Returns:
            QA report with pass/fail and issues
        """
        issues = []
        
        # Check for failures
        failed = [r for r in results if not r.success]
        if failed:
            issues.extend([
                f"Subtask '{r.subtask_id}' failed: {r.error}"
                for r in failed
            ])
        
        # Check for empty results
        empty = [r for r in results if r.success and not r.result.strip()]
        if empty:
            issues.extend([
                f"Subtask '{r.subtask_id}' returned empty result"
                for r in empty
            ])
        
        # Calculate success rate
        success_rate = len([r for r in results if r.success]) / len(results) if results else 0
        
        return {
            "passed": len(issues) == 0 and success_rate >= 0.8,
            "success_rate": success_rate,
            "issues": issues,
            "total_subtasks": len(results),
            "successful_subtasks": len([r for r in results if r.success]),
            "failed_subtasks": len(failed),
        }
    
    def get_orchestration_prompt(self) -> str:
        """
        Generate a prompt addition for orchestration context.
        
        Returns:
            Prompt text to add to agent's context
        """
        if not self._state:
            return ""
        
        prompt = f"""
## Boomerang Orchestration Active

**Task ID:** {self._state.task_id}
**Phase:** {self._state.phase.value}
**Complexity:** {self._state.complexity.value}
**Iteration:** {self._state.iterations + 1}/{self._state.max_iterations}

### Subtasks:
"""
        for i, subtask in enumerate(self._state.subtasks):
            status = "✅" if any(
                r.subtask_id == subtask["id"] and r.success
                for r in self._state.results
            ) else "⏳"
            prompt += f"{i+1}. [{status}] {subtask['description']} (mode: {subtask.get('mode', 'code')})\n"
        
        if self._state.results:
            prompt += "\n### Completed Results:\n"
            for result in self._state.results:
                status = "✅" if result.success else "❌"
                prompt += f"- [{status}] {result.subtask_id}: {result.result[:100]}...\n"
        
        return prompt
    
    def complete_orchestration(self, final_result: str) -> Optional[BoomerangState]:
        """
        Mark orchestration as complete.
        
        Args:
            final_result: The final synthesized result
            
        Returns:
            Final BoomerangState or None if no active orchestration
        """
        if self._state:
            self._state.phase = BoomerangPhase.COMPLETE
            self._state.completed_at = datetime.now()
            self._state.final_result = final_result
            
            logger.info(
                f"Completed Boomerang orchestration: {self._state.task_id} "
                f"in {self._state.iterations + 1} iterations"
            )
        
        return self._state
    
    # ==================== Session Task List Integration ====================
    
    def get_session_task_list(self) -> Optional[SessionTaskList]:
        """Get the session task list for this orchestration."""
        return self._session_tasks
    
    async def create_session_task_list(self, context_id: str) -> SessionTaskList:
        """
        Create a session task list from the current orchestration state.
        
        Args:
            context_id: The context ID to associate with the task list
            
        Returns:
            SessionTaskList with tasks from decomposed subtasks
        """
        if not self._state:
            # No active orchestration, create empty task list
            self._session_tasks = get_or_create_session_tasks(
                context_id=context_id,
                mission="",
                owner="alex",
            )
            return self._session_tasks
        
        # Create task list from Boomerang subtasks
        self._session_tasks = SessionTaskList.from_boomerang_subtasks(
            context_id=context_id,
            mission=self._state.original_task,
            subtasks=self._state.subtasks,
            owner="orchestrator",
        )
        
        # Save to disk
        await self._session_tasks.save()
        
        logger.info(
            f"Created session task list for orchestration {self._state.task_id} "
            f"with {len(self._session_tasks.tasks)} tasks"
        )
        
        return self._session_tasks
    
    async def sync_task_status(self, subtask_id: str, result: SubtaskResult) -> None:
        """
        Sync a subtask result to the session task list.
        
        Args:
            subtask_id: The subtask ID (Boomerang ID)
            result: The SubtaskResult from execution
        """
        if not self._session_tasks:
            return
        
        # Find the task by boomerang_id in metadata
        for task in self._session_tasks.tasks:
            if task.metadata.get("boomerang_id") == subtask_id:
                if result.success:
                    self._session_tasks.complete_task(task.id, result.result)
                else:
                    self._session_tasks.fail_task(task.id, result.error)
                break
        
        # Save changes
        await self._session_tasks.save()
    
    async def start_task_in_session(self, subtask_id: str, assigned_to: str) -> None:
        """
        Mark a task as started in the session task list.
        
        Args:
            subtask_id: The subtask ID (Boomerang ID)
            assigned_to: The agent/mode assigned to execute
        """
        if not self._session_tasks:
            return
        
        # Find the task by boomerang_id in metadata
        for task in self._session_tasks.tasks:
            if task.metadata.get("boomerang_id") == subtask_id:
                self._session_tasks.start_task(task.id, assigned_to)
                break
        
        # Save changes
        await self._session_tasks.save()
    
    def get_task_list_markdown(self) -> str:
        """
        Get the session task list as markdown.
        
        Returns:
            Markdown representation of the task list
        """
        if self._session_tasks:
            return self._session_tasks.to_markdown()
        return ""
    
    def get_task_list_summary(self) -> str:
        """
        Get a brief summary of the session task list.
        
        Returns:
            Summary string
        """
        if self._session_tasks:
            return self._session_tasks.to_summary()
        return "No tasks"


# Global orchestrator instance per agent
_orchestrators: Dict[str, BoomerangOrchestrator] = {}


def get_boomerang_orchestrator(agent: "Agent") -> BoomerangOrchestrator:
    """
    Get or create Boomerang orchestrator for an agent.
    
    Args:
        agent: The agent
        
    Returns:
        BoomerangOrchestrator instance
    """
    agent_id = agent.agent_name
    if agent_id not in _orchestrators:
        _orchestrators[agent_id] = BoomerangOrchestrator(agent)
    return _orchestrators[agent_id]


def should_use_boomerang(agent: "Agent", task: str) -> bool:
    """
    Check if Boomerang orchestration should be used for a task.
    
    Args:
        agent: The agent
        task: Task description
        
    Returns:
        True if Boomerang should be used
    """
    orchestrator = get_boomerang_orchestrator(agent)
    return orchestrator.should_orchestrate(task)
