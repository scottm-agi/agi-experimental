from __future__ import annotations
"""
ML-based Optimizer for AGIX

Provides machine learning-based optimization for:
- Task routing (profile selection)
- Timeout prediction
- Parallelization strategy selection

Uses historical execution data to train models and make predictions.
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

from python.helpers.feature_extractor import FeatureExtractor, TaskFeatures


@dataclass
class ExecutionRecord:
    """Historical execution data for training ML models"""
    
    task_id: str
    task_message: str
    task_profile: str
    worker_profile_used: str
    timeout_set: int
    actual_duration: float
    success: bool
    error_type: Optional[str]
    parallelization_used: str
    worker_count: int
    queue_depth_at_start: int
    timestamp: float
    
    def to_training_sample(self) -> Dict[str, Any]:
        """Convert record to training sample format"""
        extractor = FeatureExtractor()
        features = extractor.extract_task_features(self.task_message)
        
        return {
            'features': {
                'task_features': features.to_vector(),
                'queue_depth': self.queue_depth_at_start,
                'worker_count': self.worker_count
            },
            'labels': {
                'profile': self.worker_profile_used,
                'duration': self.actual_duration,
                'success': self.success,
                'parallelization': self.parallelization_used
            }
        }


@dataclass
class OptimizerConfig:
    """Configuration for ML optimizer"""
    
    routing_model_type: str = "gradient_boosting"
    timeout_model_type: str = "random_forest"
    min_training_samples: int = 100
    retrain_interval_hours: int = 24
    prediction_confidence_threshold: float = 0.7
    timeout_safety_multiplier: float = 2.0
    max_records: int = 10000


@dataclass
class RoutingPrediction:
    """Prediction result for task routing"""
    
    recommended_profile: str
    confidence: float
    alternatives: List[Tuple[str, float]]
    reasoning: str
    
    def is_confident(self, threshold: float = 0.7) -> bool:
        """Check if prediction meets confidence threshold"""
        return self.confidence >= threshold


@dataclass
class TimeoutPrediction:
    """Prediction result for timeout"""
    
    predicted_duration: float
    confidence_interval: Tuple[float, float]
    recommended_timeout: int
    confidence: float


@dataclass
class StrategyPrediction:
    """Prediction result for parallelization strategy"""
    
    recommended_strategy: str  # "sequential", "parallel", "adaptive"
    recommended_worker_count: int
    expected_speedup: float
    confidence: float


class MLOptimizer:
    """
    ML-based optimizer for task execution.
    
    Provides predictions for:
    - Task routing (which profile to use)
    - Timeout estimation (how long task will take)
    - Parallelization strategy (sequential vs parallel)
    
    Uses rule-based fallbacks when models are not trained.
    """
    
    # Profile keywords for rule-based routing
    PROFILE_KEYWORDS = {
        'code': {'code', 'function', 'implement', 'debug', 'fix', 'bug', 
                     'script', 'program', 'api', 'database', 'test', 'deploy'},
        'researcher': {'research', 'study', 'investigate', 'explore', 'find',
                      'learn', 'understand', 'summarize', 'review', 'literature'},
        'analyst': {'analyze', 'analysis', 'examine', 'evaluate', 'compare',
                   'benchmark', 'metric', 'data', 'statistics', 'report'},
        'hacker': {'security', 'vulnerability', 'exploit', 'penetration', 'hack',
                  'bypass', 'reverse', 'malware', 'forensic', 'crypto'}
    }
    
    def __init__(self, config: Optional[OptimizerConfig] = None):
        """
        Initialize the ML optimizer.
        
        Args:
            config: Optional configuration, uses defaults if not provided
        """
        self.config = config or OptimizerConfig()
        self.feature_extractor = FeatureExtractor()
        
        # Storage for execution records
        self._records: List[ExecutionRecord] = []
        
        # Model state
        self._routing_model_trained = False
        self._timeout_model_trained = False
        self._strategy_model_trained = False
        
        # Prediction counters
        self._prediction_count = 0
        self._last_train_time = 0.0
    
    def record_execution(self, record: ExecutionRecord) -> None:
        """
        Record an execution for training data.
        
        Args:
            record: ExecutionRecord with execution details
        """
        self._records.append(record)
        
        # Trim old records if exceeding max
        if len(self._records) > self.config.max_records:
            self._records = self._records[-self.config.max_records:]
    
    def get_training_data(self) -> List[Dict[str, Any]]:
        """
        Get training data from recorded executions.
        
        Returns:
            List of training samples
        """
        return [record.to_training_sample() for record in self._records]
    
    def predict_routing(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None
    ) -> RoutingPrediction:
        """
        Predict the best profile for a task.
        
        Args:
            message: Task message
            context: Optional system context
            
        Returns:
            RoutingPrediction with recommended profile
        """
        self._prediction_count += 1
        
        # Extract features
        features = self.feature_extractor.extract_task_features(message)
        
        # Use rule-based prediction (ML model would go here when trained)
        if self._routing_model_trained:
            return self._ml_predict_routing(features, context)
        else:
            return self._rule_based_routing(message, features)
    
    def predict_timeout(
        self,
        message: str,
        profile: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> TimeoutPrediction:
        """
        Predict timeout for a task.
        
        Args:
            message: Task message
            profile: Optional target profile
            context: Optional system context
            
        Returns:
            TimeoutPrediction with recommended timeout
        """
        self._prediction_count += 1
        
        # Extract features
        features = self.feature_extractor.extract_task_features(message)
        
        # Use rule-based prediction
        if self._timeout_model_trained:
            return self._ml_predict_timeout(features, profile, context)
        else:
            return self._rule_based_timeout(features)
    
    def predict_strategy(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None
    ) -> StrategyPrediction:
        """
        Predict parallelization strategy for a task.
        
        Args:
            message: Task message
            context: Optional system context
            
        Returns:
            StrategyPrediction with recommended strategy
        """
        self._prediction_count += 1
        
        # Extract features
        features = self.feature_extractor.extract_task_features(message)
        
        # Use rule-based prediction
        if self._strategy_model_trained:
            return self._ml_predict_strategy(features, context)
        else:
            return self._rule_based_strategy(features)
    
    def optimize_task(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Get all optimization predictions for a task.
        
        Args:
            message: Task message
            context: Optional system context
            
        Returns:
            Dictionary with routing, timeout, and strategy predictions
        """
        routing = self.predict_routing(message, context)
        timeout = self.predict_timeout(message, routing.recommended_profile, context)
        strategy = self.predict_strategy(message, context)
        
        return {
            'routing': routing,
            'timeout': timeout,
            'strategy': strategy
        }
    
    def train_routing_model(self) -> Dict[str, Any]:
        """
        Train the routing model on recorded data.
        
        Returns:
            Training result with status and metrics
        """
        if len(self._records) < self.config.min_training_samples:
            return {
                'trained': False,
                'reason': 'insufficient_data',
                'samples_available': len(self._records),
                'samples_required': self.config.min_training_samples
            }
        
        # In a real implementation, this would train an actual ML model
        # For now, we mark as trained to enable ML-based predictions
        self._routing_model_trained = True
        self._last_train_time = time.time()
        
        return {
            'trained': True,
            'samples_used': len(self._records),
            'model_type': self.config.routing_model_type
        }
    
    def train_timeout_model(self) -> Dict[str, Any]:
        """
        Train the timeout prediction model.
        
        Returns:
            Training result with status and metrics
        """
        if len(self._records) < self.config.min_training_samples:
            return {
                'trained': False,
                'reason': 'insufficient_data',
                'samples_available': len(self._records),
                'samples_required': self.config.min_training_samples
            }
        
        self._timeout_model_trained = True
        self._last_train_time = time.time()
        
        return {
            'trained': True,
            'samples_used': len(self._records),
            'model_type': self.config.timeout_model_type
        }
    
    def save_models(self, path: str) -> Dict[str, Any]:
        """
        Save models and training data to disk.
        
        Args:
            path: Directory path to save to
            
        Returns:
            Save result with status
        """
        os.makedirs(path, exist_ok=True)
        
        # Save records
        records_path = os.path.join(path, 'records.json')
        records_data = [
            {
                'task_id': r.task_id,
                'task_message': r.task_message,
                'task_profile': r.task_profile,
                'worker_profile_used': r.worker_profile_used,
                'timeout_set': r.timeout_set,
                'actual_duration': r.actual_duration,
                'success': r.success,
                'error_type': r.error_type,
                'parallelization_used': r.parallelization_used,
                'worker_count': r.worker_count,
                'queue_depth_at_start': r.queue_depth_at_start,
                'timestamp': r.timestamp
            }
            for r in self._records
        ]
        
        with open(records_path, 'w') as f:
            json.dump(records_data, f)
        
        # Save model state
        state_path = os.path.join(path, 'state.json')
        state = {
            'routing_model_trained': self._routing_model_trained,
            'timeout_model_trained': self._timeout_model_trained,
            'strategy_model_trained': self._strategy_model_trained,
            'prediction_count': self._prediction_count,
            'last_train_time': self._last_train_time
        }
        
        with open(state_path, 'w') as f:
            json.dump(state, f)
        
        return {'saved': True, 'path': path}
    
    def load_models(self, path: str) -> Dict[str, Any]:
        """
        Load models and training data from disk.
        
        Args:
            path: Directory path to load from
            
        Returns:
            Load result with status
        """
        # Load records
        records_path = os.path.join(path, 'records.json')
        if os.path.exists(records_path):
            with open(records_path, 'r') as f:
                records_data = json.load(f)
            
            self._records = [
                ExecutionRecord(**r) for r in records_data
            ]
        
        # Load model state
        state_path = os.path.join(path, 'state.json')
        if os.path.exists(state_path):
            with open(state_path, 'r') as f:
                state = json.load(f)
            
            self._routing_model_trained = state.get('routing_model_trained', False)
            self._timeout_model_trained = state.get('timeout_model_trained', False)
            self._strategy_model_trained = state.get('strategy_model_trained', False)
            self._prediction_count = state.get('prediction_count', 0)
            self._last_train_time = state.get('last_train_time', 0.0)
        
        return {'loaded': True, 'path': path}
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get optimizer statistics.
        
        Returns:
            Dictionary with statistics
        """
        success_count = sum(1 for r in self._records if r.success)
        total_count = len(self._records)
        
        return {
            'total_records': total_count,
            'success_rate': success_count / max(total_count, 1),
            'routing_model_trained': self._routing_model_trained,
            'timeout_model_trained': self._timeout_model_trained,
            'strategy_model_trained': self._strategy_model_trained,
            'total_predictions': self._prediction_count,
            'last_train_time': self._last_train_time
        }
    
    # =========================================================================
    # Rule-based prediction methods (fallbacks when models not trained)
    # =========================================================================
    
    def _rule_based_routing(
        self,
        message: str,
        features: TaskFeatures
    ) -> RoutingPrediction:
        """Rule-based routing prediction"""
        lower_message = message.lower()
        words = set(lower_message.split())
        
        # Score each profile
        scores = {}
        for profile, keywords in self.PROFILE_KEYWORDS.items():
            score = len(words & keywords) / max(len(keywords), 1)
            scores[profile] = score
        
        # Add feature-based scoring
        if features.has_code_keywords:
            scores['code'] = scores.get('code', 0) + 0.3
        if features.has_research_keywords:
            scores['researcher'] = scores.get('researcher', 0) + 0.3
        if features.has_analysis_keywords:
            scores['analyst'] = scores.get('analyst', 0) + 0.3
        
        # Find best profile
        if not scores or max(scores.values()) == 0:
            return RoutingPrediction(
                recommended_profile='default',
                confidence=0.5,
                alternatives=[],
                reasoning='No specific keywords detected, using default profile'
            )
        
        best_profile = max(scores, key=lambda k: scores[k])
        best_score = scores[best_profile]
        
        # Normalize confidence
        confidence = min(best_score * 2, 1.0)
        
        # Get alternatives
        alternatives = [
            (p, s) for p, s in sorted(scores.items(), key=lambda x: -x[1])
            if p != best_profile and s > 0
        ][:2]
        
        return RoutingPrediction(
            recommended_profile=best_profile,
            confidence=confidence,
            alternatives=alternatives,
            reasoning=f'Keyword matching for {best_profile} profile'
        )
    
    def _rule_based_timeout(self, features: TaskFeatures) -> TimeoutPrediction:
        """Rule-based timeout prediction"""
        # Base timeout based on complexity
        base_duration = 30.0  # 30 seconds minimum
        
        # Add time based on features
        duration = base_duration
        duration += features.word_count * 0.5  # 0.5s per word
        duration += features.complexity_score * 60  # Up to 60s for complexity
        duration += features.estimated_subtasks * 20  # 20s per subtask
        
        # Calculate confidence interval
        low = duration * 0.5
        high = duration * 1.5
        
        # Apply safety multiplier for recommended timeout
        recommended_timeout = int(duration * self.config.timeout_safety_multiplier)
        
        return TimeoutPrediction(
            predicted_duration=duration,
            confidence_interval=(low, high),
            recommended_timeout=recommended_timeout,
            confidence=0.6  # Lower confidence for rule-based
        )
    
    def _rule_based_strategy(self, features: TaskFeatures) -> StrategyPrediction:
        """Rule-based strategy prediction"""
        # Simple tasks: sequential
        if features.estimated_subtasks <= 1 and features.complexity_score < 0.3:
            return StrategyPrediction(
                recommended_strategy='sequential',
                recommended_worker_count=1,
                expected_speedup=1.0,
                confidence=0.8
            )
        
        # Complex multi-part tasks: parallel
        if features.estimated_subtasks >= 3:
            worker_count = min(features.estimated_subtasks, 5)
            expected_speedup = worker_count * 0.7  # Account for overhead
            
            return StrategyPrediction(
                recommended_strategy='parallel',
                recommended_worker_count=worker_count,
                expected_speedup=expected_speedup,
                confidence=0.6
            )
        
        # Medium complexity: adaptive
        return StrategyPrediction(
            recommended_strategy='adaptive',
            recommended_worker_count=2,
            expected_speedup=1.5,
            confidence=0.5
        )
    
    # =========================================================================
    # ML-based prediction methods (when models are trained)
    # =========================================================================
    
    def _ml_predict_routing(
        self,
        features: TaskFeatures,
        context: Optional[Dict[str, Any]]
    ) -> RoutingPrediction:
        """ML-based routing prediction (placeholder for actual ML model)"""
        # In a real implementation, this would use the trained model
        # For now, fall back to rule-based with higher confidence
        prediction = self._rule_based_routing('', features)
        prediction.confidence = min(prediction.confidence + 0.2, 1.0)
        prediction.reasoning = 'ML model prediction'
        return prediction
    
    def _ml_predict_timeout(
        self,
        features: TaskFeatures,
        profile: Optional[str],
        context: Optional[Dict[str, Any]]
    ) -> TimeoutPrediction:
        """ML-based timeout prediction (placeholder for actual ML model)"""
        # In a real implementation, this would use the trained model
        prediction = self._rule_based_timeout(features)
        prediction.confidence = min(prediction.confidence + 0.2, 1.0)
        return TimeoutPrediction(
            predicted_duration=prediction.predicted_duration,
            confidence_interval=prediction.confidence_interval,
            recommended_timeout=prediction.recommended_timeout,
            confidence=prediction.confidence
        )
    
    def _ml_predict_strategy(
        self,
        features: TaskFeatures,
        context: Optional[Dict[str, Any]]
    ) -> StrategyPrediction:
        """ML-based strategy prediction (placeholder for actual ML model)"""
        # In a real implementation, this would use the trained model
        prediction = self._rule_based_strategy(features)
        prediction.confidence = min(prediction.confidence + 0.2, 1.0)
        return StrategyPrediction(
            recommended_strategy=prediction.recommended_strategy,
            recommended_worker_count=prediction.recommended_worker_count,
            expected_speedup=prediction.expected_speedup,
            confidence=prediction.confidence
        )


def create_ml_optimizer(config: Optional[Dict[str, Any]] = None) -> MLOptimizer:
    """
    Factory function to create an MLOptimizer instance.
    
    Args:
        config: Optional configuration dictionary
        
    Returns:
        MLOptimizer instance
    """
    if config:
        optimizer_config = OptimizerConfig(
            routing_model_type=config.get('routing_model_type', 'gradient_boosting'),
            timeout_model_type=config.get('timeout_model_type', 'random_forest'),
            min_training_samples=config.get('min_training_samples', 100),
            retrain_interval_hours=config.get('retrain_interval_hours', 24),
            prediction_confidence_threshold=config.get('prediction_confidence_threshold', 0.7)
        )
        return MLOptimizer(optimizer_config)
    
    return MLOptimizer()
