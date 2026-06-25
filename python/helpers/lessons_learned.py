from __future__ import annotations
"""
Lessons Learned Engine for Master Agent Supervisor

This module provides a system for capturing, storing, and applying lessons
learned from python.agent interventions and task completions. It enables the
supervisor to improve over time by learning from past experiences.

Key Features:
- Automatic lesson extraction from interventions
- Pattern-based lesson categorization
- Lesson persistence and retrieval
- Application of lessons to future interventions
- Integration with memory bank for long-term storage
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, TYPE_CHECKING
from python.helpers.notification import NotificationManager, NotificationType, NotificationPriority

if TYPE_CHECKING:
    from python.redis_client import RedisClient

logger = logging.getLogger(__name__)


class LessonCategory(Enum):
    """Categories for lessons learned."""
    CONTEXT_MANAGEMENT = "context_management"
    RESEARCH_DECISION = "research_decision"
    TASK_COMPLETION = "task_completion"
    FILE_OPERATIONS = "file_operations"
    SERVICE_MANAGEMENT = "service_management"
    API_NETWORK = "api_network"
    CODE_GENERATION = "code_generation"
    STATE_MANAGEMENT = "state_management"
    OUTPUT_QUALITY = "output_quality"
    AGENT_COORDINATION = "agent_coordination"
    INTERVENTION_STRATEGY = "intervention_strategy"
    ESCALATION = "escalation"
    GENERAL = "general"


class LessonSeverity(Enum):
    """Severity levels for lessons."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class LessonEntry:
    """A single lesson learned entry."""
    id: str
    category: LessonCategory
    severity: LessonSeverity
    title: str
    description: str
    pattern_id: Optional[str]  # Related pattern ID (e.g., CTX-001)
    agent_id: Optional[str]
    context_id: Optional[str]
    
    # What happened
    trigger: str  # What triggered this lesson
    observation: str  # What was observed
    
    # What was learned
    root_cause: str
    solution: str
    prevention: str
    
    # Metadata
    created_at: datetime
    updated_at: datetime
    occurrence_count: int = 1
    success_rate: float = 0.0  # How often the solution worked
    
    # Tags for searchability
    tags: List[str] = field(default_factory=list)
    
    # Related lessons
    related_lessons: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category.value,
            "severity": self.severity.value,
            "title": self.title,
            "description": self.description,
            "pattern_id": self.pattern_id,
            "agent_id": self.agent_id,
            "context_id": self.context_id,
            "trigger": self.trigger,
            "observation": self.observation,
            "root_cause": self.root_cause,
            "solution": self.solution,
            "prevention": self.prevention,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "occurrence_count": self.occurrence_count,
            "success_rate": self.success_rate,
            "tags": self.tags,
            "related_lessons": self.related_lessons,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LessonEntry":
        return cls(
            id=data["id"],
            category=LessonCategory(data["category"]),
            severity=LessonSeverity(data["severity"]),
            title=data["title"],
            description=data["description"],
            pattern_id=data.get("pattern_id"),
            agent_id=data.get("agent_id"),
            context_id=data.get("context_id"),
            trigger=data["trigger"],
            observation=data["observation"],
            root_cause=data["root_cause"],
            solution=data["solution"],
            prevention=data["prevention"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            occurrence_count=data.get("occurrence_count", 1),
            success_rate=data.get("success_rate", 0.0),
            tags=data.get("tags", []),
            related_lessons=data.get("related_lessons", []),
        )
    
    def to_markdown(self) -> str:
        """Convert lesson to markdown format for memory bank."""
        return f"""## {self.title}

**ID**: {self.id}
**Category**: {self.category.value}
**Severity**: {self.severity.value}
**Pattern**: {self.pattern_id or 'N/A'}
**Occurrences**: {self.occurrence_count}
**Success Rate**: {self.success_rate:.1%}

### Trigger
{self.trigger}

### Observation
{self.observation}

### Root Cause
{self.root_cause}

### Solution
{self.solution}

### Prevention
{self.prevention}

**Tags**: {', '.join(self.tags) if self.tags else 'None'}
**Created**: {self.created_at.isoformat()}
**Updated**: {self.updated_at.isoformat()}

---
"""


@dataclass
class InterventionOutcomeData:
    """Data about an intervention outcome for lesson extraction."""
    intervention_id: str
    agent_id: str
    context_id: str
    pattern_type: str
    pattern_description: str
    intervention_type: str
    intervention_message: str
    outcome: str  # "success", "failure", "partial"
    outcome_details: str
    duration_seconds: float
    iterations_before: int
    iterations_after: int
    timestamp: datetime


class LessonsLearnedEngine:
    """
    Engine for managing lessons learned from python.agent operations.
    
    The engine:
    1. Captures lessons from intervention outcomes
    2. Stores lessons in memory (and optionally Redis/file)
    3. Retrieves relevant lessons for new situations
    4. Updates lessons based on new occurrences
    5. Exports lessons to memory bank for persistence
    """
    
    def __init__(
        self,
        redis_client: Optional["RedisClient"] = None,
        memory_bank_path: Optional[str] = None,
        auto_persist: bool = True,
    ):
        self.redis_client = redis_client
        self.memory_bank_path = memory_bank_path or "memory-bank/lessons-learned"
        self.auto_persist = auto_persist
        
        # In-memory storage
        self._lessons: Dict[str, LessonEntry] = {}
        self._lessons_by_category: Dict[LessonCategory, List[str]] = {
            cat: [] for cat in LessonCategory
        }
        self._lessons_by_pattern: Dict[str, List[str]] = {}
        
        # Callbacks
        self._on_lesson_created: List[Callable[[LessonEntry], None]] = []
        self._on_lesson_updated: List[Callable[[LessonEntry], None]] = []

        # Register default notification callback
        self._on_lesson_created.append(self._notify_lesson_created)
        
        # Statistics
        self._stats = {
            "lessons_created": 0,
            "lessons_updated": 0,
            "lessons_applied": 0,
            "total_occurrences": 0,
        }
    
    # =========================================================================
    # Lesson Creation
    # =========================================================================
    
    async def create_lesson(
        self,
        category: LessonCategory,
        severity: LessonSeverity,
        title: str,
        description: str,
        trigger: str,
        observation: str,
        root_cause: str,
        solution: str,
        prevention: str,
        pattern_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        context_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> LessonEntry:
        """Create a new lesson entry."""
        # Generate ID from content hash
        from python.helpers.hashing import content_hash_short
        ch = content_hash_short(
            f"{category.value}:{title}:{root_cause}", length=12
        )
        lesson_id = f"LL-{ch}"
        
        # Check if similar lesson exists
        existing = self._lessons.get(lesson_id)
        if existing:
            # Update existing lesson
            return await self.update_lesson(lesson_id, observation, solution)
        
        now = datetime.now(timezone.utc)
        lesson = LessonEntry(
            id=lesson_id,
            category=category,
            severity=severity,
            title=title,
            description=description,
            pattern_id=pattern_id,
            agent_id=agent_id,
            context_id=context_id,
            trigger=trigger,
            observation=observation,
            root_cause=root_cause,
            solution=solution,
            prevention=prevention,
            created_at=now,
            updated_at=now,
            tags=tags or [],
        )
        
        # Store lesson
        self._lessons[lesson_id] = lesson
        self._lessons_by_category[category].append(lesson_id)
        if pattern_id:
            if pattern_id not in self._lessons_by_pattern:
                self._lessons_by_pattern[pattern_id] = []
            self._lessons_by_pattern[pattern_id].append(lesson_id)
        
        self._stats["lessons_created"] += 1
        self._stats["total_occurrences"] += 1
        
        # Notify callbacks
        for callback in self._on_lesson_created:
            try:
                callback(lesson)
            except Exception as e:
                logger.error(f"Error in lesson created callback: {e}")
        
        # Persist if enabled
        if self.auto_persist:
            await self._persist_lesson(lesson)
        
        logger.info(f"Created lesson: {lesson_id} - {title}")
        return lesson
    
    async def update_lesson(
        self,
        lesson_id: str,
        new_observation: Optional[str] = None,
        new_solution: Optional[str] = None,
        outcome_success: Optional[bool] = None,
    ) -> LessonEntry:
        """Update an existing lesson with new information."""
        lesson = self._lessons.get(lesson_id)
        if not lesson:
            raise ValueError(f"Lesson {lesson_id} not found")
        
        lesson.occurrence_count += 1
        lesson.updated_at = datetime.now(timezone.utc)
        
        if new_observation:
            # Append new observation if different
            if new_observation not in lesson.observation:
                lesson.observation += f"\n\n**Additional observation ({lesson.occurrence_count}):**\n{new_observation}"
        
        if new_solution:
            # Update solution if provided
            lesson.solution = new_solution
        
        if outcome_success is not None:
            # Update success rate
            total = lesson.occurrence_count
            current_successes = lesson.success_rate * (total - 1)
            new_successes = current_successes + (1 if outcome_success else 0)
            lesson.success_rate = new_successes / total
        
        self._stats["lessons_updated"] += 1
        self._stats["total_occurrences"] += 1
        
        # Notify callbacks
        for callback in self._on_lesson_updated:
            try:
                callback(lesson)
            except Exception as e:
                logger.error(f"Error in lesson updated callback: {e}")
        
        # Persist if enabled
        if self.auto_persist:
            await self._persist_lesson(lesson)
        
        logger.info(f"Updated lesson: {lesson_id} (occurrence #{lesson.occurrence_count})")
        return lesson
    
    # =========================================================================
    # Lesson Extraction from Interventions
    # =========================================================================
    
    async def extract_lesson_from_intervention(
        self,
        outcome_data: InterventionOutcomeData,
    ) -> Optional[LessonEntry]:
        """
        Extract a lesson from an intervention outcome.
        
        This is called after each intervention to capture what was learned.
        """
        # Determine category from pattern type
        category = self._pattern_to_category(outcome_data.pattern_type)
        
        # Determine severity from outcome
        if outcome_data.outcome == "failure":
            severity = LessonSeverity.WARNING
        elif outcome_data.outcome == "partial":
            severity = LessonSeverity.INFO
        else:
            severity = LessonSeverity.INFO
        
        # Generate title
        title = f"{outcome_data.pattern_type} - {outcome_data.outcome.title()} Intervention"
        
        # Generate description
        description = f"Intervention for {outcome_data.pattern_type} pattern resulted in {outcome_data.outcome}"
        
        # Generate trigger
        trigger = f"Pattern detected: {outcome_data.pattern_description}"
        
        # Generate observation
        observation = f"""
**Intervention Type**: {outcome_data.intervention_type}
**Outcome**: {outcome_data.outcome}
**Details**: {outcome_data.outcome_details}
**Duration**: {outcome_data.duration_seconds:.1f}s
**Iterations Before**: {outcome_data.iterations_before}
**Iterations After**: {outcome_data.iterations_after}
"""
        
        # Generate root cause analysis
        if outcome_data.outcome == "success":
            root_cause = f"The {outcome_data.pattern_type} pattern was successfully addressed by the intervention."
        else:
            root_cause = f"The {outcome_data.intervention_type} intervention was insufficient to resolve the {outcome_data.pattern_type} pattern. {outcome_data.outcome_details}"
        
        # Generate solution
        solution = f"Applied {outcome_data.intervention_type} intervention: {outcome_data.intervention_message[:200]}..."
        
        # Generate prevention
        prevention = f"Monitor for {outcome_data.pattern_type} patterns and apply {outcome_data.intervention_type} intervention early."
        
        # Create or update lesson
        lesson = await self.create_lesson(
            category=category,
            severity=severity,
            title=title,
            description=description,
            trigger=trigger,
            observation=observation,
            root_cause=root_cause,
            solution=solution,
            prevention=prevention,
            pattern_id=outcome_data.pattern_type,
            agent_id=outcome_data.agent_id,
            context_id=outcome_data.context_id,
            tags=[
                outcome_data.pattern_type,
                outcome_data.intervention_type,
                outcome_data.outcome,
            ],
        )
        
        # Update success rate based on outcome
        await self.update_lesson(
            lesson.id,
            outcome_success=(outcome_data.outcome == "success"),
        )
        
        return lesson
    
    async def extract_lesson_from_task_completion(
        self,
        agent_id: str,
        context_id: str,
        task_description: str,
        patterns_encountered: List[str],
        interventions_applied: int,
        escalations: int,
        total_iterations: int,
        success: bool,
        notes: Optional[str] = None,
        force_create: bool = False,
    ) -> Optional[LessonEntry]:
        """
        Extract a lesson from a completed task.
        
        This is called after each task completion to capture overall learnings.
        
        Args:
            agent_id: ID of the agent
            context_id: Context ID
            task_description: Description of the task
            patterns_encountered: List of pattern types encountered
            interventions_applied: Number of interventions applied
            escalations: Number of escalations
            total_iterations: Total iterations
            success: Whether task was successful
            notes: Optional notes
            force_create: If True, always create a lesson even if no issues
            
        Returns:
            LessonEntry if created, None otherwise
        """
        # For failed tasks, always create a lesson
        # For successful tasks with no issues, only create if force_create is True
        if not patterns_encountered and interventions_applied == 0 and success and not force_create:
            # No issues encountered and task succeeded, no lesson needed
            return None
        
        # Determine category
        category = LessonCategory.GENERAL
        if patterns_encountered:
            # Use the most common pattern category
            category = self._pattern_to_category(patterns_encountered[0])
        
        # Determine severity
        if escalations > 0:
            severity = LessonSeverity.CRITICAL
        elif interventions_applied > 2:
            severity = LessonSeverity.WARNING
        else:
            severity = LessonSeverity.INFO
        
        # Generate title
        outcome_str = "Successful" if success else "Failed"
        title = f"Task {outcome_str}: {task_description[:50]}..."
        
        # Generate description
        description = f"Task completed with {interventions_applied} interventions and {escalations} escalations"
        
        # Generate trigger
        trigger = f"Task: {task_description}"
        
        # Generate observation
        observation = f"""
**Patterns Encountered**: {', '.join(patterns_encountered) if patterns_encountered else 'None'}
**Interventions Applied**: {interventions_applied}
**Escalations**: {escalations}
**Total Iterations**: {total_iterations}
**Success**: {success}
**Notes**: {notes or 'None'}
"""
        
        # Generate root cause
        if success:
            root_cause = "Task completed successfully despite encountering issues."
        else:
            root_cause = f"Task failed after {interventions_applied} interventions. Patterns: {', '.join(patterns_encountered)}"
        
        # Generate solution
        solution = f"Applied {interventions_applied} interventions to address {len(patterns_encountered)} patterns."
        
        # Generate prevention
        if patterns_encountered:
            prevention = f"Watch for early signs of: {', '.join(patterns_encountered[:3])}"
        else:
            prevention = "No specific prevention identified."
        
        # Create lesson
        lesson = await self.create_lesson(
            category=category,
            severity=severity,
            title=title,
            description=description,
            trigger=trigger,
            observation=observation,
            root_cause=root_cause,
            solution=solution,
            prevention=prevention,
            agent_id=agent_id,
            context_id=context_id,
            tags=patterns_encountered + ["task_completion", "success" if success else "failure"],
        )
        
        return lesson
    
    def _notify_lesson_created(self, lesson: 'LessonEntry'):
        """Send a notification when a new lesson is created."""
        try:
            NotificationManager.send_notification(
                type=NotificationType.SUCCESS,
                priority=NotificationPriority.NORMAL,
                title=f"New Lesson: {lesson.title}",
                message=f"A new lesson has been extracted for {lesson.category.value}.",
                detail=f"<b>Root Cause:</b> {lesson.root_cause}<br><b>Solution:</b> {lesson.solution}",
                group="lessons_learned"
            )
        except Exception as e:
            logger.error(f"Failed to send lesson notification: {e}")

    # =========================================================================
    # Lesson Retrieval
    # =========================================================================
    
    async def get_lesson(self, lesson_id: str) -> Optional[LessonEntry]:
        """Get a specific lesson by ID."""
        return self._lessons.get(lesson_id)
    
    async def get_lessons_by_category(
        self,
        category: LessonCategory,
        limit: int = 10,
    ) -> List[LessonEntry]:
        """Get lessons by category."""
        lesson_ids = self._lessons_by_category.get(category, [])
        lessons = [self._lessons[lid] for lid in lesson_ids if lid in self._lessons]
        # Sort by occurrence count (most common first)
        lessons.sort(key=lambda l: l.occurrence_count, reverse=True)
        return lessons[:limit]
    
    async def get_lessons_by_pattern(
        self,
        pattern_id: str,
        limit: int = 10,
    ) -> List[LessonEntry]:
        """Get lessons related to a specific pattern."""
        lesson_ids = self._lessons_by_pattern.get(pattern_id, [])
        lessons = [self._lessons[lid] for lid in lesson_ids if lid in self._lessons]
        # Sort by success rate (most successful first)
        lessons.sort(key=lambda l: l.success_rate, reverse=True)
        return lessons[:limit]
    
    async def get_relevant_lessons(
        self,
        pattern_type: str,
        context: Optional[str] = None,
        limit: int = 5,
    ) -> List[LessonEntry]:
        """
        Get lessons relevant to a current situation.
        
        Used to inform intervention strategies based on past experience.
        """
        relevant = []
        
        # Get lessons for this pattern
        pattern_lessons = await self.get_lessons_by_pattern(pattern_type, limit=limit)
        relevant.extend(pattern_lessons)
        
        # Get lessons from same category
        category = self._pattern_to_category(pattern_type)
        category_lessons = await self.get_lessons_by_category(category, limit=limit)
        for lesson in category_lessons:
            if lesson not in relevant:
                relevant.append(lesson)
        
        # Sort by relevance (success rate * occurrence count)
        relevant.sort(
            key=lambda l: l.success_rate * l.occurrence_count,
            reverse=True
        )
        
        self._stats["lessons_applied"] += 1
        return relevant[:limit]
    
    async def get_all_lessons(self) -> List[LessonEntry]:
        """Get all lessons."""
        return list(self._lessons.values())
    
    # =========================================================================
    # Persistence
    # =========================================================================
    
    async def _persist_lesson(self, lesson: LessonEntry) -> None:
        """Persist a lesson to storage."""
        # Persist to Redis if available
        if self.redis_client:
            try:
                key = f"lessons:{lesson.id}"
                await self.redis_client.set(key, json.dumps(lesson.to_dict()))
            except Exception as e:
                logger.error(f"Failed to persist lesson to Redis: {e}")
        
        # Persist to file
        try:
            await self._persist_to_file(lesson)
        except Exception as e:
            logger.error(f"Failed to persist lesson to file: {e}")
    
    async def _persist_to_file(self, lesson: LessonEntry) -> None:
        """Persist a lesson to the memory bank file system."""
        # Create directory if needed
        base_path = Path(self.memory_bank_path)
        base_path.mkdir(parents=True, exist_ok=True)
        
        # Write individual lesson file
        lesson_file = base_path / f"{lesson.id}.json"
        with open(lesson_file, "w") as f:
            json.dump(lesson.to_dict(), f, indent=2)
        
        # Update category index
        await self._update_category_index(lesson)
    
    async def _update_category_index(self, lesson: LessonEntry) -> None:
        """Update the category index file."""
        base_path = Path(self.memory_bank_path)
        index_file = base_path / f"index_{lesson.category.value}.md"
        
        # Read existing index or create new
        if index_file.exists():
            with open(index_file, "r") as f:
                content = f.read()
        else:
            content = f"# Lessons Learned: {lesson.category.value.replace('_', ' ').title()}\n\n"
        
        # Check if lesson already in index
        if lesson.id not in content:
            # Add lesson to index
            content += lesson.to_markdown()
            
            with open(index_file, "w") as f:
                f.write(content)
    
    async def load_lessons_from_storage(self) -> int:
        """Load lessons from storage."""
        loaded = 0
        
        # Load from Redis if available
        if self.redis_client:
            try:
                keys = await self.redis_client.keys("lessons:*")
                for key in keys:
                    data = await self.redis_client.get(key)
                    if data:
                        lesson = LessonEntry.from_dict(json.loads(data))
                        self._lessons[lesson.id] = lesson
                        self._lessons_by_category[lesson.category].append(lesson.id)
                        if lesson.pattern_id:
                            if lesson.pattern_id not in self._lessons_by_pattern:
                                self._lessons_by_pattern[lesson.pattern_id] = []
                            self._lessons_by_pattern[lesson.pattern_id].append(lesson.id)
                        loaded += 1
            except Exception as e:
                logger.error(f"Failed to load lessons from Redis: {e}")
        
        # Load from files
        base_path = Path(self.memory_bank_path)
        if base_path.exists():
            for lesson_file in base_path.glob("LL-*.json"):
                try:
                    with open(lesson_file, "r") as f:
                        data = json.load(f)
                    lesson = LessonEntry.from_dict(data)
                    if lesson.id not in self._lessons:
                        self._lessons[lesson.id] = lesson
                        self._lessons_by_category[lesson.category].append(lesson.id)
                        if lesson.pattern_id:
                            if lesson.pattern_id not in self._lessons_by_pattern:
                                self._lessons_by_pattern[lesson.pattern_id] = []
                            self._lessons_by_pattern[lesson.pattern_id].append(lesson.id)
                        loaded += 1
                except Exception as e:
                    logger.error(f"Failed to load lesson from {lesson_file}: {e}")
        
        logger.info(f"Loaded {loaded} lessons from storage")
        return loaded
    
    async def export_to_memory_bank(self, output_path: Optional[str] = None) -> str:
        """
        Export all lessons to a consolidated memory bank file.
        
        Returns the path to the exported file.
        """
        output_path = output_path or f"{self.memory_bank_path}/lessons_learned_export.md"
        
        content = """# Lessons Learned - Master Agent Supervisor

This document contains lessons learned from python.agent interventions and task completions.
These lessons are used to improve future interventions and prevent recurring issues.

**Generated**: {timestamp}
**Total Lessons**: {total}
**Categories**: {categories}

---

""".format(
            timestamp=datetime.now(timezone.utc).isoformat(),
            total=len(self._lessons),
            categories=len([c for c in self._lessons_by_category.values() if c]),
        )
        
        # Group by category
        for category in LessonCategory:
            lessons = await self.get_lessons_by_category(category, limit=100)
            if lessons:
                content += f"\n# {category.value.replace('_', ' ').title()}\n\n"
                for lesson in lessons:
                    content += lesson.to_markdown()
        
        # Write file
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(content)
        
        logger.info(f"Exported {len(self._lessons)} lessons to {output_path}")
        return output_path
    
    # =========================================================================
    # Callbacks
    # =========================================================================
    
    def on_lesson_created(self, callback: Callable[[LessonEntry], None]) -> None:
        """Register a callback for lesson creation events."""
        self._on_lesson_created.append(callback)
    
    def on_lesson_updated(self, callback: Callable[[LessonEntry], None]) -> None:
        """Register a callback for lesson update events."""
        self._on_lesson_updated.append(callback)
    
    # =========================================================================
    # Statistics
    # =========================================================================
    
    def get_stats(self) -> Dict[str, Any]:
        """Get engine statistics."""
        return {
            **self._stats,
            "total_lessons": len(self._lessons),
            "lessons_by_category": {
                cat.value: len(ids) for cat, ids in self._lessons_by_category.items()
            },
            "lessons_by_pattern": {
                pat: len(ids) for pat, ids in self._lessons_by_pattern.items()
            },
        }
    
    # =========================================================================
    # Helpers
    # =========================================================================
    
    def _pattern_to_category(self, pattern_type: str) -> LessonCategory:
        """Map a pattern type to a lesson category."""
        pattern_upper = pattern_type.upper()
        
        if pattern_upper.startswith("CTX"):
            return LessonCategory.CONTEXT_MANAGEMENT
        elif pattern_upper.startswith("RES"):
            return LessonCategory.RESEARCH_DECISION
        elif pattern_upper.startswith("TSK"):
            return LessonCategory.TASK_COMPLETION
        elif pattern_upper.startswith("FILE"):
            return LessonCategory.FILE_OPERATIONS
        elif pattern_upper.startswith("SVC"):
            return LessonCategory.SERVICE_MANAGEMENT
        elif pattern_upper.startswith("API"):
            return LessonCategory.API_NETWORK
        elif pattern_upper.startswith("CODE"):
            return LessonCategory.CODE_GENERATION
        elif pattern_upper.startswith("STATE"):
            return LessonCategory.STATE_MANAGEMENT
        elif pattern_upper.startswith("OUT"):
            return LessonCategory.OUTPUT_QUALITY
        elif pattern_upper.startswith("COORD"):
            return LessonCategory.AGENT_COORDINATION
        else:
            return LessonCategory.GENERAL

    # =========================================================================
    # Rule Promotion Pipeline
    # =========================================================================

    def suggest_rule_promotions(
        self,
        min_occurrences: int = 3,
        min_success_rate: float = 0.8,
    ) -> List[Dict[str, Any]]:
        """Identify lessons that should be promoted to global rules.
        
        A lesson qualifies for promotion when:
        - occurrence_count >= min_occurrences (proven pattern, not a one-off)
        - success_rate >= min_success_rate (the solution actually works)
        
        Returns a list of promotion candidates with formatted rule entries.
        """
        candidates = []
        
        for lesson in self._lessons.values():
            if (
                lesson.occurrence_count >= min_occurrences
                and lesson.success_rate >= min_success_rate
            ):
                rule_entry = self._format_lesson_as_rule(lesson)
                candidates.append({
                    "lesson_id": lesson.id,
                    "title": lesson.title,
                    "category": lesson.category.value,
                    "occurrences": lesson.occurrence_count,
                    "success_rate": lesson.success_rate,
                    "rule_entry": rule_entry,
                })
        
        # Sort by (occurrences * success_rate) descending — strongest signals first
        candidates.sort(
            key=lambda c: c["occurrences"] * c["success_rate"],
            reverse=True,
        )
        
        if candidates:
            logger.info(
                f"[LESSONS] Found {len(candidates)} lessons eligible for rule promotion "
                f"(>={min_occurrences} occurrences, >={min_success_rate} success rate)"
            )
            # Notify about promotion candidates
            try:
                NotificationManager.notify(
                    message=f"📋 {len(candidates)} lesson(s) eligible for rule promotion: "
                            + ", ".join(c["title"] for c in candidates[:3]),
                    notification_type=NotificationType.INFO,
                    priority=NotificationPriority.LOW,
                    title="Rule Promotion Candidates",
                )
            except Exception as e:
                logger.debug(f"Failed to send promotion notification: {e}")
        
        return candidates
    
    def _format_lesson_as_rule(self, lesson: LessonEntry) -> str:
        """Format a lesson as a rule entry for global.md."""
        # Map lesson category to rule section
        category_map = {
            LessonCategory.CODE_GENERATION: "Code Quality",
            LessonCategory.FILE_OPERATIONS: "Code Quality",
            LessonCategory.SERVICE_MANAGEMENT: "Error Handling & Debugging",
            LessonCategory.API_NETWORK: "Error Handling & Debugging",
            LessonCategory.TASK_COMPLETION: "Verification",
            LessonCategory.OUTPUT_QUALITY: "Verification",
            LessonCategory.AGENT_COORDINATION: "Verification",
            LessonCategory.CONTEXT_MANAGEMENT: "Research & Planning",
        }
        section = category_map.get(lesson.category, "General")
        
        rule_name = lesson.title.replace(" ", " ").strip()
        
        return f"""### {rule_name}
- **Level**: enforce
- **Applies**: all
- **Description**: {lesson.prevention}
- **Rationale**: Learned from {lesson.occurrence_count} occurrences ({lesson.success_rate:.0%} success rate). Root cause: {lesson.root_cause}
- **Source**: Auto-promoted from lesson {lesson.id} on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}
"""

    async def promote_to_rules(
        self,
        min_occurrences: int = 3,
        min_success_rate: float = 0.8,
        rules_file_path: Optional[str] = None,
    ) -> int:
        """Actually promote eligible lessons to the global rules file.
        
        Returns the number of lessons promoted.
        """
        from python.helpers import files
        
        candidates = self.suggest_rule_promotions(min_occurrences, min_success_rate)
        if not candidates:
            return 0
        
        rules_path = rules_file_path or os.path.join(
            files.get_abs_path("memory-bank"), "rules", "global.md"
        )
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(rules_path), exist_ok=True)
        
        # EXT-4 fix: Atomic read-check-append with file-level locking
        # to prevent race conditions when concurrent agents promote rules.
        import fcntl

        promoted_count = 0
        try:
            with open(rules_path, 'a+') as f:
                # Lock file BEFORE reading — prevents TOCTOU race
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.seek(0)
                    existing_content = f.read()
                    
                    new_rules = []
                    for candidate in candidates:
                        # Skip if lesson ID already appears in rules (already promoted)
                        if candidate["lesson_id"] in existing_content:
                            logger.debug(f"Skipping already-promoted lesson: {candidate['lesson_id']}")
                            continue
                        new_rules.append(candidate["rule_entry"])
                        promoted_count += 1
                    
                    if new_rules:
                        f.write("\n\n---\n\n## Auto-Promoted Rules (from Lessons Learned)\n\n")
                        f.write("\n".join(new_rules))
                        f.flush()
                        os.fsync(f.fileno())
                        
                        logger.info(f"[LESSONS] Promoted {promoted_count} lessons to rules file: {rules_path}")
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as e:
            logger.error(f"Failed to write promoted rules: {e}")
            return 0
        
        # Non-critical: notification (outside lock)
        if promoted_count > 0:
            try:
                NotificationManager.notify(
                    message=f"✅ Promoted {promoted_count} lesson(s) to global rules",
                    notification_type=NotificationType.INFO,
                    priority=NotificationPriority.NORMAL,
                    title="Rules Updated",
                )
            except Exception as e:
                logger.debug(f"Failed to send promotion notification: {e}")
        
        return promoted_count


# =============================================================================
# Factory Function
# =============================================================================

def create_lessons_learned_engine(
    redis_client: Optional["RedisClient"] = None,
    memory_bank_path: Optional[str] = None,
    auto_persist: bool = True,
) -> LessonsLearnedEngine:
    """Create a lessons learned engine with optional configuration."""
    return LessonsLearnedEngine(
        redis_client=redis_client,
        memory_bank_path=memory_bank_path,
        auto_persist=auto_persist,
    )
