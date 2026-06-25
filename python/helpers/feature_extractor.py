from __future__ import annotations
"""
Feature Extractor for ML-based Optimization

Extracts features from task messages and system context for ML model predictions.
Supports task routing, timeout prediction, and parallelization strategy optimization.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from datetime import datetime


class TaskFeatureType(Enum):
    """Types of features that can be extracted"""
    TEXT = "text"
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    BOOLEAN = "boolean"


@dataclass
class TaskFeatures:
    """Features extracted from a task message"""
    
    # Basic text metrics
    message_length: int = 0
    word_count: int = 0
    sentence_count: int = 0
    avg_word_length: float = 0.0
    
    # Complexity indicators
    complexity_score: float = 0.0
    
    # Keyword presence
    has_code_keywords: bool = False
    has_research_keywords: bool = False
    has_analysis_keywords: bool = False
    has_creative_keywords: bool = False
    
    # Task structure
    question_count: int = 0
    command_indicators: int = 0
    urgency_score: float = 0.0
    technical_density: float = 0.0
    estimated_subtasks: int = 1
    
    def to_vector(self) -> List[float]:
        """Convert features to numeric vector for ML models"""
        return [
            float(self.message_length),
            float(self.word_count),
            float(self.sentence_count),
            self.avg_word_length,
            self.complexity_score,
            float(self.has_code_keywords),
            float(self.has_research_keywords),
            float(self.has_analysis_keywords),
            float(self.has_creative_keywords),
            float(self.question_count),
            float(self.command_indicators),
            self.urgency_score,
            self.technical_density,
            float(self.estimated_subtasks)
        ]


@dataclass
class ContextFeatures:
    """Features extracted from system context"""
    
    # System state
    queue_depth: int = 0
    active_workers: int = 0
    total_workers: int = 0
    utilization: float = 0.0
    recent_error_rate: float = 0.0
    avg_task_duration: float = 0.0
    
    # Temporal features
    hour_of_day: int = 0
    day_of_week: int = 0
    is_peak_hours: bool = False
    
    def to_vector(self) -> List[float]:
        """Convert features to numeric vector for ML models"""
        return [
            float(self.queue_depth),
            float(self.active_workers),
            float(self.total_workers),
            self.utilization,
            self.recent_error_rate,
            self.avg_task_duration,
            float(self.hour_of_day),
            float(self.day_of_week),
            float(self.is_peak_hours)
        ]


class FeatureExtractor:
    """
    Extracts features from task messages and system context.
    
    Features are used by ML models for:
    - Task routing (profile selection)
    - Timeout prediction
    - Parallelization strategy selection
    """
    
    # Keyword sets for classification
    CODE_KEYWORDS = {
        'code', 'function', 'class', 'method', 'variable', 'python', 'javascript',
        'java', 'typescript', 'implement', 'debug', 'fix', 'bug', 'error', 'compile',
        'script', 'program', 'algorithm', 'api', 'database', 'sql', 'query', 'test',
        'unit', 'integration', 'deploy', 'git', 'commit', 'merge', 'branch', 'refactor',
        'optimize', 'performance', 'memory', 'cpu', 'async', 'await', 'promise',
        'callback', 'loop', 'recursion', 'sort', 'search', 'binary', 'tree', 'graph',
        'array', 'list', 'dict', 'dictionary', 'hash', 'map', 'set', 'stack', 'queue',
        'import', 'export', 'module', 'package', 'library', 'framework', 'sdk'
    }
    
    RESEARCH_KEYWORDS = {
        'research', 'study', 'investigate', 'explore', 'discover', 'find', 'search',
        'learn', 'understand', 'explain', 'describe', 'summarize', 'overview',
        'background', 'history', 'trend', 'development', 'progress', 'state-of-the-art',
        'latest', 'recent', 'current', 'future', 'prediction', 'forecast', 'survey',
        'review', 'literature', 'paper', 'article', 'publication', 'journal', 'conference',
        'citation', 'reference', 'source', 'evidence', 'data', 'statistics', 'fact',
        'information', 'knowledge', 'insight', 'finding', 'conclusion', 'recommendation'
    }
    
    ANALYSIS_KEYWORDS = {
        'analyze', 'analysis', 'examine', 'evaluate', 'assess', 'measure', 'compare',
        'contrast', 'benchmark', 'metric', 'kpi', 'indicator', 'performance', 'result',
        'outcome', 'impact', 'effect', 'cause', 'correlation', 'relationship', 'pattern',
        'trend', 'anomaly', 'outlier', 'distribution', 'variance', 'deviation', 'mean',
        'median', 'average', 'percentage', 'ratio', 'rate', 'growth', 'decline',
        'increase', 'decrease', 'change', 'difference', 'similarity', 'cluster',
        'segment', 'category', 'classification', 'regression', 'prediction', 'model'
    }
    
    CREATIVE_KEYWORDS = {
        'create', 'design', 'build', 'develop', 'make', 'generate', 'produce', 'write',
        'compose', 'draft', 'author', 'craft', 'invent', 'innovate', 'imagine', 'envision',
        'concept', 'idea', 'brainstorm', 'creative', 'original', 'unique', 'novel',
        'artistic', 'aesthetic', 'visual', 'graphic', 'image', 'video', 'audio', 'music',
        'story', 'narrative', 'content', 'copy', 'marketing', 'brand', 'logo', 'slogan',
        'campaign', 'presentation', 'pitch', 'proposal', 'plan', 'strategy'
    }
    
    URGENCY_KEYWORDS = {
        'urgent', 'asap', 'immediately', 'now', 'quick', 'fast', 'hurry', 'rush',
        'priority', 'critical', 'important', 'deadline', 'today', 'tonight', 'morning',
        'emergency', 'crucial', 'vital', 'essential', 'must', 'need', 'require'
    }
    
    COMMAND_INDICATORS = {
        'please', 'can you', 'could you', 'would you', 'will you', 'do', 'make',
        'create', 'write', 'build', 'implement', 'fix', 'update', 'change', 'modify',
        'add', 'remove', 'delete', 'install', 'configure', 'setup', 'run', 'execute',
        'test', 'check', 'verify', 'validate', 'review', 'approve', 'send', 'submit'
    }
    
    SUBTASK_INDICATORS = {
        'first', 'second', 'third', 'fourth', 'fifth', 'then', 'next', 'after',
        'finally', 'lastly', 'also', 'additionally', 'furthermore', 'moreover',
        'step', 'phase', 'stage', 'part', '1.', '2.', '3.', '4.', '5.',
        'a)', 'b)', 'c)', 'd)', 'e)', '•', '-', '*'
    }
    
    def __init__(self):
        """Initialize the feature extractor"""
        pass
    
    def extract_task_features(self, message: str) -> TaskFeatures:
        """
        Extract features from a task message.
        
        Args:
            message: The task message to analyze
            
        Returns:
            TaskFeatures with extracted values
        """
        if not message:
            return TaskFeatures()
        
        # Basic text metrics
        message_length = len(message)
        words = self._tokenize(message)
        word_count = len(words)
        sentences = self._split_sentences(message)
        sentence_count = len(sentences)
        avg_word_length = sum(len(w) for w in words) / max(word_count, 1)
        
        # Keyword detection
        lower_message = message.lower()
        lower_words = set(w.lower() for w in words)
        
        has_code_keywords = bool(lower_words & self.CODE_KEYWORDS)
        has_research_keywords = bool(lower_words & self.RESEARCH_KEYWORDS)
        has_analysis_keywords = bool(lower_words & self.ANALYSIS_KEYWORDS)
        has_creative_keywords = bool(lower_words & self.CREATIVE_KEYWORDS)
        
        # Question count
        question_count = message.count('?')
        
        # Command indicators
        command_indicators = sum(1 for ind in self.COMMAND_INDICATORS if ind in lower_message)
        
        # Urgency score
        urgency_matches = sum(1 for kw in self.URGENCY_KEYWORDS if kw in lower_message)
        urgency_score = min(urgency_matches / 5.0, 1.0)
        
        # Technical density
        technical_words = lower_words & (self.CODE_KEYWORDS | self.ANALYSIS_KEYWORDS)
        technical_density = len(technical_words) / max(word_count, 1)
        
        # Complexity score (based on multiple factors)
        complexity_score = self._calculate_complexity(
            word_count, sentence_count, avg_word_length,
            technical_density, question_count
        )
        
        # Estimated subtasks
        estimated_subtasks = self._estimate_subtasks(message, lower_message)
        
        return TaskFeatures(
            message_length=message_length,
            word_count=word_count,
            sentence_count=sentence_count,
            avg_word_length=avg_word_length,
            complexity_score=complexity_score,
            has_code_keywords=has_code_keywords,
            has_research_keywords=has_research_keywords,
            has_analysis_keywords=has_analysis_keywords,
            has_creative_keywords=has_creative_keywords,
            question_count=question_count,
            command_indicators=command_indicators,
            urgency_score=urgency_score,
            technical_density=technical_density,
            estimated_subtasks=estimated_subtasks
        )
    
    def extract_context_features(self, system_state: Dict[str, Any]) -> ContextFeatures:
        """
        Extract features from system context.
        
        Args:
            system_state: Dictionary with system metrics
            
        Returns:
            ContextFeatures with extracted values
        """
        queue_depth = system_state.get('queue_depth', 0)
        active_workers = system_state.get('active_workers', 0)
        total_workers = system_state.get('total_workers', 1)
        
        # Calculate utilization
        utilization = active_workers / max(total_workers, 1)
        
        # Calculate error rate
        recent_errors = system_state.get('recent_errors', 0)
        total_recent_tasks = system_state.get('total_recent_tasks', 1)
        recent_error_rate = recent_errors / max(total_recent_tasks, 1)
        
        # Average task duration
        avg_task_duration = system_state.get('avg_task_duration', 30.0)
        
        # Temporal features
        now = datetime.now()
        hour_of_day = now.hour
        day_of_week = now.weekday()
        
        # Peak hours: 9 AM - 6 PM on weekdays
        is_peak_hours = (
            day_of_week < 5 and  # Monday-Friday
            9 <= hour_of_day <= 18
        )
        
        return ContextFeatures(
            queue_depth=queue_depth,
            active_workers=active_workers,
            total_workers=total_workers,
            utilization=utilization,
            recent_error_rate=recent_error_rate,
            avg_task_duration=avg_task_duration,
            hour_of_day=hour_of_day,
            day_of_week=day_of_week,
            is_peak_hours=is_peak_hours
        )
    
    def extract_combined_features(
        self,
        message: str,
        system_state: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Extract both task and context features.
        
        Args:
            message: The task message
            system_state: Optional system state dictionary
            
        Returns:
            Dictionary with task features, context features, and combined vector
        """
        task_features = self.extract_task_features(message)
        
        if system_state:
            context_features = self.extract_context_features(system_state)
        else:
            context_features = ContextFeatures()
        
        # Combine vectors
        combined_vector = task_features.to_vector() + context_features.to_vector()
        
        return {
            'task': task_features,
            'context': context_features,
            'combined_vector': combined_vector
        }
    
    def _tokenize(self, text: str) -> List[str]:
        """Split text into words"""
        # Simple tokenization: split on whitespace and punctuation
        words = re.findall(r'\b\w+\b', text)
        return words
    
    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences"""
        # Split on sentence-ending punctuation
        sentences = re.split(r'[.!?]+', text)
        # Filter empty strings
        sentences = [s.strip() for s in sentences if s.strip()]
        return sentences if sentences else ['']
    
    def _calculate_complexity(
        self,
        word_count: int,
        sentence_count: int,
        avg_word_length: float,
        technical_density: float,
        question_count: int
    ) -> float:
        """
        Calculate a complexity score for the task.
        
        Factors:
        - Word count (longer = more complex)
        - Sentence count (more sentences = more complex)
        - Average word length (longer words = more complex)
        - Technical density (more technical = more complex)
        - Question count (more questions = more complex)
        
        Returns:
            Complexity score between 0.0 and 1.0
        """
        # Normalize each factor to 0-1 range
        word_score = min(word_count / 200.0, 1.0)  # 200 words = max
        sentence_score = min(sentence_count / 10.0, 1.0)  # 10 sentences = max
        word_length_score = min((avg_word_length - 3) / 5.0, 1.0)  # 3-8 chars
        word_length_score = max(word_length_score, 0.0)
        question_score = min(question_count / 5.0, 1.0)  # 5 questions = max
        
        # Weighted combination
        complexity = (
            0.25 * word_score +
            0.15 * sentence_score +
            0.15 * word_length_score +
            0.30 * technical_density +
            0.15 * question_score
        )
        
        return min(max(complexity, 0.0), 1.0)
    
    def _estimate_subtasks(self, message: str, lower_message: str) -> int:
        """
        Estimate the number of subtasks in a message.
        
        Looks for:
        - Numbered lists (1., 2., 3.)
        - Sequence words (first, then, finally)
        - Bullet points
        - Multiple sentences with action verbs
        """
        subtask_count = 1  # Minimum 1 subtask
        
        # Check for numbered items
        numbered_pattern = r'\b\d+[.)]\s'
        numbered_matches = len(re.findall(numbered_pattern, message))
        if numbered_matches > 0:
            subtask_count = max(subtask_count, numbered_matches)
        
        # Check for sequence indicators
        sequence_words = ['first', 'second', 'third', 'fourth', 'fifth',
                         'then', 'next', 'after that', 'finally', 'lastly']
        sequence_count = sum(1 for word in sequence_words if word in lower_message)
        if sequence_count > 0:
            subtask_count = max(subtask_count, sequence_count)
        
        # Check for bullet points
        bullet_pattern = r'^[\s]*[-•*]\s'
        bullet_matches = len(re.findall(bullet_pattern, message, re.MULTILINE))
        if bullet_matches > 0:
            subtask_count = max(subtask_count, bullet_matches)
        
        # Check for multiple action verbs in separate sentences
        sentences = self._split_sentences(message)
        action_verbs = {'write', 'create', 'build', 'implement', 'analyze',
                       'research', 'design', 'test', 'review', 'update'}
        action_sentences = sum(
            1 for s in sentences
            if any(verb in s.lower() for verb in action_verbs)
        )
        if action_sentences > 1:
            subtask_count = max(subtask_count, action_sentences)
        
        return subtask_count


def create_feature_extractor() -> FeatureExtractor:
    """Factory function to create a FeatureExtractor instance"""
    return FeatureExtractor()
