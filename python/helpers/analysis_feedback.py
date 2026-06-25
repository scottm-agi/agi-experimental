from __future__ import annotations
"""
Analysis Feedback Tracker - Enables systematic improvement of code analysis quality.

This module provides:
1. Recording feedback on analysis quality (thumbs up/down, comments)
2. Tracking missing elements identified in reviews
3. Suggesting improvements based on past feedback
4. Storage in lessons-learned.md for persistence
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, TYPE_CHECKING
from python.helpers.event_bus import get_event_bus, AgentSignal, SignalType
from python.helpers.personalization import record_personalization
if TYPE_CHECKING:
    from python.agent import AgentContext
import json
import os
import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Default path for feedback storage
DEFAULT_FEEDBACK_FILE = "tmp/analysis_feedback.json"
LESSONS_LEARNED_PATH = "memory-bank/lessons-learned/analysis_feedback.md"


@dataclass
class AnalysisFeedback:
    """Single feedback item for an analysis."""
    issue_id: str
    analysis_id: str
    quality_score: int  # 1-5 (1=bad, 5=excellent) or -1=thumbs down, 1=thumbs up
    missing_elements: List[str] = field(default_factory=list)
    user_comment: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    issue_type: str = ""  # e.g., "PFR", "Bug", "Feature"
    tech_stack: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "analysis_id": self.analysis_id,
            "quality_score": self.quality_score,
            "missing_elements": self.missing_elements,
            "user_comment": self.user_comment,
            "timestamp": self.timestamp.isoformat(),
            "issue_type": self.issue_type,
            "tech_stack": self.tech_stack
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnalysisFeedback":
        return cls(
            issue_id=data.get("issue_id", ""),
            analysis_id=data.get("analysis_id", ""),
            quality_score=data.get("quality_score", 0),
            missing_elements=data.get("missing_elements", []),
            user_comment=data.get("user_comment"),
            timestamp=datetime.fromisoformat(data["timestamp"]) if "timestamp" in data else datetime.now(),
            issue_type=data.get("issue_type", ""),
            tech_stack=data.get("tech_stack", [])
        )


class AnalysisFeedbackTracker:
    """
    Tracks analysis feedback and learns from patterns.
    
    Uses:
    - JSON file for structured data
    - lessons-learned.md for human-readable patterns
    """
    
    def __init__(self, feedback_file: str = None):
        self.feedback_file = feedback_file or DEFAULT_FEEDBACK_FILE
        self._feedback_cache: List[AnalysisFeedback] = []
        self._load_feedback()
    
    def _load_feedback(self):
        """Load feedback from JSON file."""
        try:
            if os.path.exists(self.feedback_file):
                with open(self.feedback_file, 'r') as f:
                    data = json.load(f)
                    self._feedback_cache = [
                        AnalysisFeedback.from_dict(item) 
                        for item in data.get("feedback", [])
                    ]
        except Exception as e:
            logger.warning(f"Could not load feedback: {e}")
            self._feedback_cache = []
    
    def _save_feedback(self):
        """Save feedback to JSON file."""
        try:
            os.makedirs(os.path.dirname(self.feedback_file), exist_ok=True)
            with open(self.feedback_file, 'w') as f:
                json.dump({
                    "feedback": [fb.to_dict() for fb in self._feedback_cache],
                    "updated": datetime.now().isoformat()
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save feedback: {e}")
    
    def record_feedback(self, feedback: AnalysisFeedback) -> bool:
        """
        Store feedback and update patterns.
        
        Returns True if successful.
        """
        self._feedback_cache.append(feedback)
        self._save_feedback()
        
        # If negative feedback, update lessons learned and signal supervisor
        if feedback.quality_score <= 2 or (feedback.quality_score == -1):
            self._update_lessons_learned(feedback)
            self._signal_supervisor(feedback)
        
        # If positive feedback, record personalization
        if feedback.quality_score >= 4 or feedback.quality_score == 1:
            record_personalization(feedback.user_comment or "Positive feedback received")
        
        return True

    def _signal_supervisor(self, feedback: AnalysisFeedback):
        """Emit an intervention signal to the event bus."""
        try:
            bus = get_event_bus()
            bus.publish_sync(AgentSignal(
                signal_type=SignalType.INTERVENTION_NEEDED,
                agent_id="unknown_agent", # Will be enriched by supervisor if possible
                context_id=feedback.analysis_id.split('_')[0], # Heuristic
                timestamp=datetime.now(),
                severity="medium",
                details={
                    "feedback_type": "negative",
                    "user_comment": feedback.user_comment,
                    "issue_id": feedback.issue_id
                }
            ))
        except Exception as e:
            logger.warning(f"Failed to signal supervisor: {e}")
    
    def _update_lessons_learned(self, feedback: AnalysisFeedback):
        """Append negative feedback to lessons-learned.md."""
        try:
            content = f"""
## Analysis Gap: {feedback.issue_id} ({datetime.now().strftime('%Y-%m-%d')})

**Issue Type**: {feedback.issue_type}
**Missing Elements**: {', '.join(feedback.missing_elements) if feedback.missing_elements else 'Not specified'}
**User Comment**: {feedback.user_comment or 'None'}
**Tech Stack**: {', '.join(feedback.tech_stack) if feedback.tech_stack else 'Not specified'}

### Improvement Action
- Add these patterns to default search: {feedback.missing_elements}

---
"""
            os.makedirs(os.path.dirname(LESSONS_LEARNED_PATH), exist_ok=True)
            with open(LESSONS_LEARNED_PATH, 'a') as f:
                f.write(content)
            logger.info(f"Updated lessons-learned with feedback for {feedback.issue_id}")
        except Exception as e:
            logger.error(f"Could not update lessons: {e}")
    
    def get_common_gaps(self, issue_type: str = None, limit: int = 10) -> Dict[str, int]:
        """
        Return frequently missed elements.
        
        Args:
            issue_type: Filter by issue type (e.g., "PFR")
            limit: Max patterns to return
            
        Returns:
            Dict mapping element name to frequency count
        """
        gaps = {}
        for fb in self._feedback_cache:
            if issue_type and fb.issue_type != issue_type:
                continue
            for element in fb.missing_elements:
                gaps[element] = gaps.get(element, 0) + 1
        
        # Sort by frequency
        sorted_gaps = sorted(gaps.items(), key=lambda x: x[1], reverse=True)
        return dict(sorted_gaps[:limit])
    
    def suggest_improvements(self, issue_type: str = None, tech_stack: List[str] = None) -> List[str]:
        """
        Return suggested patterns/files to search based on past feedback.
        
        Args:
            issue_type: The type of issue being analyzed
            tech_stack: Technologies involved
            
        Returns:
            List of file patterns or search terms to include
        """
        suggestions = set()
        
        # Get common gaps for this issue type
        common_gaps = self.get_common_gaps(issue_type)
        suggestions.update(common_gaps.keys())
        
        # Add tech-stack-specific patterns from past feedback
        if tech_stack:
            for fb in self._feedback_cache:
                if any(t in fb.tech_stack for t in tech_stack):
                    suggestions.update(fb.missing_elements)
        
        # Known important patterns based on accumulated learning
        important_patterns = self._get_learned_patterns()
        suggestions.update(important_patterns)
        
        return list(suggestions)
    
    def _get_learned_patterns(self) -> List[str]:
        """Extract patterns from lessons-learned.md."""
        patterns = []
        try:
            if os.path.exists(LESSONS_LEARNED_PATH):
                with open(LESSONS_LEARNED_PATH, 'r') as f:
                    content = f.read()
                    # Extract patterns from "Missing Elements:" lines
                    matches = re.findall(r'\*\*Missing Elements\*\*:\s*([^\n]+)', content)
                    for match in matches:
                        elements = [e.strip() for e in match.split(',')]
                        patterns.extend(elements)
        except Exception:
            pass
        return list(set(patterns))
    
    def get_recent_feedback(self, limit: int = 10) -> List[AnalysisFeedback]:
        """Get most recent feedback items."""
        sorted_feedback = sorted(
            self._feedback_cache, 
            key=lambda x: x.timestamp, 
            reverse=True
        )
        return sorted_feedback[:limit]
    
    def get_quality_stats(self) -> Dict[str, Any]:
        """Return overall quality statistics."""
        if not self._feedback_cache:
            return {"total": 0, "avg_score": 0, "thumbs_up": 0, "thumbs_down": 0}
        
        total = len(self._feedback_cache)
        scores = [fb.quality_score for fb in self._feedback_cache if fb.quality_score > 0]
        avg = sum(scores) / len(scores) if scores else 0
        thumbs_up = sum(1 for fb in self._feedback_cache if fb.quality_score == 5 or fb.quality_score == 1)
        thumbs_down = sum(1 for fb in self._feedback_cache if fb.quality_score <= 2 or fb.quality_score == -1)
        
        return {
            "total": total,
            "avg_score": round(avg, 2),
            "thumbs_up": thumbs_up,
            "thumbs_down": thumbs_down
        }


# Singleton instance
_tracker_instance: Optional[AnalysisFeedbackTracker] = None


def get_feedback_tracker() -> AnalysisFeedbackTracker:
    """Get or create the global feedback tracker."""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = AnalysisFeedbackTracker()
    return _tracker_instance


def record_analysis_feedback(
    issue_id: str,
    quality_score: int,
    missing_elements: List[str] = None,
    user_comment: str = None,
    issue_type: str = "",
    tech_stack: List[str] = None
) -> bool:
    """
    Convenience function to record analysis feedback.
    
    Args:
        issue_id: The issue being analyzed (e.g., "#40")
        quality_score: 1-5 or -1 for thumbs down, 1 for thumbs up
        missing_elements: Files/patterns that were missed
        user_comment: User's feedback comment
        issue_type: Type of issue (PFR, Bug, etc.)
        tech_stack: Technologies involved
        
    Returns:
        True if recorded successfully
    """
    tracker = get_feedback_tracker()
    feedback = AnalysisFeedback(
        issue_id=issue_id,
        analysis_id=f"{issue_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        quality_score=quality_score,
        missing_elements=missing_elements or [],
        user_comment=user_comment,
        issue_type=issue_type,
        tech_stack=tech_stack or []
    )
    return tracker.record_feedback(feedback)
