"""
Core pattern detectors for the AGIX Supervisor.

Contains the 9 base/foundational detectors:
1. ContextWindowOverflowDetector (CTX-003)
2. ResponseLoopDetector
3. ToolFailureLoopDetector
4. ProgressStallDetector
5. RateLimitDetector (API-001)
6. InfiniteRecursionDetector (COORD-002)
7. OutputDegradationDetector (OUT-010)
8. StuckApproachDetector (Issue #218)
9. RepetitiveActionDetector (COORD-001)
"""

import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set

from python.helpers.loop_prevention import PatternType
from .base import (
    AgentState,
    DetectedPattern,
    PatternDetector,
    RE_TIMESTAMP,
    RE_UUID,
    RE_WHITESPACE,
)


class ContextWindowOverflowDetector(PatternDetector):
    """
    CTX-003: Detects when an agent's context window is approaching its limit.
    
    Thresholds:
    - Warning: 70% of max tokens
    - High: 85% of max tokens
    - Critical: 95% of max tokens
    """
    
    def __init__(
        self,
        warning_threshold: float = 0.70,
        high_threshold: float = 0.85,
        critical_threshold: float = 0.95,
    ):
        self.warning_threshold = warning_threshold
        self.high_threshold = high_threshold
        self.critical_threshold = critical_threshold
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.CONTEXT_OVERFLOW
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        if state.max_context_tokens <= 0:
            return None
        
        usage_ratio = state.context_tokens / state.max_context_tokens
        
        if usage_ratio >= self.critical_threshold:
            return self._create_pattern(
                state,
                confidence=0.95,
                severity="critical",
                description=f"Context window at {usage_ratio:.1%} capacity ({state.context_tokens}/{state.max_context_tokens} tokens)",
                metadata={
                    "usage_ratio": usage_ratio,
                    "current_tokens": state.context_tokens,
                    "max_tokens": state.max_context_tokens,
                    "threshold": "critical",
                },
            )
        elif usage_ratio >= self.high_threshold:
            return self._create_pattern(
                state,
                confidence=0.85,
                severity="high",
                description=f"Context window at {usage_ratio:.1%} capacity",
                metadata={
                    "usage_ratio": usage_ratio,
                    "current_tokens": state.context_tokens,
                    "max_tokens": state.max_context_tokens,
                    "threshold": "high",
                },
            )
        elif usage_ratio >= self.warning_threshold:
            return self._create_pattern(
                state,
                confidence=0.70,
                severity="medium",
                description=f"Context window at {usage_ratio:.1%} capacity",
                metadata={
                    "usage_ratio": usage_ratio,
                    "current_tokens": state.context_tokens,
                    "max_tokens": state.max_context_tokens,
                    "threshold": "warning",
                },
            )
        
        return None


class ResponseLoopDetector(PatternDetector):
    """
    Detects when an agent is producing repeated or highly similar responses.
    
    Uses text similarity to detect:
    - Exact duplicates
    - Near-duplicates (high similarity)
    - Semantic loops (similar structure/content)
    """
    
    def __init__(
        self,
        similarity_threshold: float = 0.85,
        min_responses_for_detection: int = 3,
        lookback_count: int = 5,
    ):
        self.similarity_threshold = similarity_threshold
        self.min_responses_for_detection = min_responses_for_detection
        self.lookback_count = lookback_count
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.RESPONSE_LOOP
    
    @property
    def is_deep(self) -> bool:
        return False  # Response loop detection is critical safety — run every cycle
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        responses = state.recent_responses[-self.lookback_count:]
        
        if len(responses) < self.min_responses_for_detection:
            return None
        
        # Check for exact duplicates
        unique_responses = set(responses)
        if len(unique_responses) == 1 and len(responses) >= self.min_responses_for_detection:
            return self._create_pattern(
                state,
                confidence=0.98,
                severity="high",
                description=f"Agent produced {len(responses)} identical responses",
                metadata={
                    "loop_type": "exact_duplicate",
                    "response_count": len(responses),
                    "repeated_content": responses[0][:200] + "..." if len(responses[0]) > 200 else responses[0],
                },
            )
        
        # Check for high similarity between consecutive responses
        similarity_scores = []
        for i in range(len(responses) - 1):
            similarity = self._calculate_similarity(responses[i], responses[i + 1])
            similarity_scores.append(similarity)
        
        if similarity_scores:
            avg_similarity = sum(similarity_scores) / len(similarity_scores)
            high_similarity_count = sum(1 for s in similarity_scores if s >= self.similarity_threshold)
            
            if high_similarity_count >= self.min_responses_for_detection - 1:
                # Issue #181: Even if responses are highly similar, they might be productive
                # if the tool arguments are changing (e.g., paging through data).
                # BUT: After 5+ repetitions with ≥90% similarity, flag regardless — that's a stuck loop.
                arg_variety = self._check_argument_variety(responses)
                if arg_variety >= 2 and len(responses) < 5 and avg_similarity < 0.90:
                    return None

                return self._create_pattern(
                    state,
                    confidence=min(0.95, avg_similarity),
                    severity="high" if avg_similarity > 0.9 else "medium",
                    description=f"Agent responses are {avg_similarity:.1%} similar on average with static arguments",
                    metadata={
                        "loop_type": "high_similarity",
                        "average_similarity": avg_similarity,
                        "high_similarity_count": high_similarity_count,
                        "similarity_scores": similarity_scores,
                        "arg_variety": arg_variety,
                    },
                )
        
        # Check for structural patterns (same tool calls, same format)
        if self._detect_structural_loop(responses):
            return self._create_pattern(
                state,
                confidence=0.80,
                severity="medium",
                description="Agent responses follow a repeating structural pattern",
                metadata={
                    "loop_type": "structural",
                    "response_count": len(responses),
                },
            )
        
        return None
    
    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate similarity between two texts."""
        # Normalize texts
        text1 = self._normalize_text(text1)
        text2 = self._normalize_text(text2)
        
        # Use SequenceMatcher for similarity
        return SequenceMatcher(None, text1, text2).ratio()
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison."""
        # Remove timestamps, UUIDs
        text = RE_TIMESTAMP.sub('', text)
        text = RE_UUID.sub('', text)
        text = ' '.join(text.lower().split())
        return text
    
    def _detect_structural_loop(self, responses: List[str]) -> bool:
        """Detect if responses follow a structural pattern."""
        if len(responses) < 3:
            return False
        
        # Extract tool names from each response
        tool_patterns = []
        for response in responses:
            tools = re.findall(r'"tool_name"\s*:\s*"([^"]+)"', response)
            tool_patterns.append(tuple(tools))
        
        # Check if tool patterns repeat
        if len(tool_patterns) >= 3:
            # Count occurrences of each pattern
            pattern_counts: Dict[tuple, int] = {}
            for pattern in tool_patterns:
                pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
            
            # If any pattern appears 3+ times, it's a structural loop
            for count in pattern_counts.values():
                if count >= 3:
                    return True
        
        return False

    def _check_argument_variety(self, responses: List[str]) -> int:
        """Count unique tool argument sets in responses."""
        unique_args = set()
        for response in responses:
            matches = re.findall(r'"arguments"\s*:\s*({[^}]+})', response)
            for args_str in matches:
                try:
                    args = json.loads(args_str)
                    # Normalize arguments (remove ephemeral values)
                    norm_args = {k: v for k, v in args.items() if not any(x in k.lower() for x in ["time", "date", "id"])}
                    unique_args.add(json.dumps(norm_args, sort_keys=True))
                except json.JSONDecodeError:
                    unique_args.add(args_str)
        return len(unique_args)


class ToolFailureLoopDetector(PatternDetector):
    """
    Detects when an agent is repeatedly failing with the same tool.
    
    Looks for:
    - Same tool failing multiple times
    - Similar error messages
    - Retry patterns without changes
    """
    
    def __init__(
        self,
        failure_threshold: int = 3,
        lookback_count: int = 10,
    ):
        self.failure_threshold = failure_threshold
        self.lookback_count = lookback_count
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.TOOL_FAILURE_LOOP
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        tool_results = state.recent_tool_results[-self.lookback_count:]
        
        if len(tool_results) < self.failure_threshold:
            return None
        
        # Count failures by tool
        tool_failures: Dict[str, List[Dict]] = {}
        for result in tool_results:
            tool_name = result.get("tool_name")
            # Normalize empty or null tool names to catch loops like tool '' not found
            if not tool_name or str(tool_name).strip() == "":
                tool_name = "unknown_or_empty"
                
            success = result.get("success", True)
            
            if not success:
                if tool_name not in tool_failures:
                    tool_failures[tool_name] = []
                tool_failures[tool_name].append(result)
        
        # Check for repeated failures
        for tool_name, failures in tool_failures.items():
            if len(failures) >= self.failure_threshold:
                # Check if errors are similar
                error_messages = [f.get("error", "") for f in failures]
                unique_errors = set(error_messages)
                
                if len(unique_errors) == 1:
                    # Same error repeated
                    return self._create_pattern(
                        state,
                        confidence=0.95,
                        severity="high",
                        description=f"Tool '{tool_name}' failed {len(failures)} times with same error",
                        metadata={
                            "tool_name": tool_name,
                            "failure_count": len(failures),
                            "error_pattern": error_messages[0][:200] if error_messages[0] else "Unknown error",
                            "loop_type": "same_error",
                        },
                    )
                else:
                    # Different errors but same tool
                    return self._create_pattern(
                        state,
                        confidence=0.80,
                        severity="medium",
                        description=f"Tool '{tool_name}' failed {len(failures)} times",
                        metadata={
                            "tool_name": tool_name,
                            "failure_count": len(failures),
                            "unique_errors": len(unique_errors),
                            "loop_type": "multiple_errors",
                        },
                    )
        
        return None


class ProgressStallDetector(PatternDetector):
    """
    Detects when an agent is not making meaningful progress.
    
    Indicators:
    - High iteration count without task completion
    - No new information being generated
    - Circular reasoning patterns
    """
    
    def __init__(
        self,
        max_iterations_without_progress: int = 10,
        stall_time_seconds: float = 300.0,  # 5 minutes
    ):
        self.max_iterations_without_progress = max_iterations_without_progress
        self.stall_time_seconds = stall_time_seconds
        self._progress_markers: Dict[str, Set[str]] = {}  # agent_id -> seen content hashes
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.PROGRESS_STALL
    
    @property
    def is_deep(self) -> bool:
        return True  # Involves history hashing and comparison

    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        # Issue #SupervisorPatience: Skip detection if agent is retrying
        if state.is_retrying:
            return None

        # Issue #168 & Supervisor Refinement: Monitoring tasks have high iteration counts by design.
        max_iters = self.max_iterations_without_progress
        if state.is_monitoring_task:
            max_iters *= 2  # Give monitoring tasks more breadth
            
        # Check if agent is making progress despite high iteration count
        if state.iteration >= max_iters:
            # Analyze if tool arguments are changing
            unique_args = set()
            for call in state.recent_tool_calls[-5:]:
                args = call.get("arguments", {})
                if isinstance(args, dict):
                    val_str = json.dumps({k: v for k, v in args.items() if not any(x in k.lower() for x in ["time", "id"])}, sort_keys=True)
                    unique_args.add(val_str)
                else:
                    unique_args.add(str(args))
            
            # If we see high variety in tool arguments (>= 3 unique in last 5), 
            # the agent is likely progressing through a batch task.
            if len(unique_args) >= 3:
                return None

            # Check if we're seeing new content in response
            content_hash = self._hash_content(state.last_response)
            
            if state.agent_id not in self._progress_markers:
                self._progress_markers[state.agent_id] = set()
            
            seen_hashes = self._progress_markers[state.agent_id]
            
            if content_hash in seen_hashes:
                # We've seen this content before and arguments aren't varying
                return self._create_pattern(
                    state,
                    confidence=0.85,
                    severity="high",
                    description=f"Agent at iteration {state.iteration} with no progress and repeated response content",
                    metadata={
                        "iteration": state.iteration,
                        "unique_responses": len(seen_hashes),
                        "unique_args_count": len(unique_args),
                        "stall_type": "repeated_content",
                    },
                )
            
            seen_hashes.add(content_hash)
            
            # Check if iteration count is very high
            if state.iteration >= self.max_iterations_without_progress * 2:
                return self._create_pattern(
                    state,
                    confidence=0.75,
                    severity="medium",
                    description=f"Agent at high iteration count ({state.iteration})",
                    metadata={
                        "iteration": state.iteration,
                        "unique_responses": len(seen_hashes),
                        "stall_type": "high_iteration",
                    },
                )
        
        return None
    
    def _hash_content(self, content: str) -> str:
        """Create a hash of content for comparison."""
        # Normalize content
        normalized = RE_WHITESPACE.sub(' ', content.lower().strip())
        from python.helpers.hashing import content_hash
        return content_hash(normalized)
    
    def clear_agent_markers(self, agent_id: str) -> None:
        """Clear progress markers for an agent."""
        if agent_id in self._progress_markers:
            del self._progress_markers[agent_id]


class RateLimitDetector(PatternDetector):
    """
    API-001: Detects when an agent is hitting rate limits frequently.
    
    Monitors:
    - Rate limit errors in recent history
    - Frequency of rate limit encounters
    """
    
    def __init__(
        self,
        rate_limit_threshold: int = 3,
        lookback_count: int = 10,
    ):
        self.rate_limit_threshold = rate_limit_threshold
        self.lookback_count = lookback_count
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.RATE_LIMIT
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        # Issue #SupervisorPatience: Skip detection if agent is retrying
        if state.is_retrying:
            return None

        recent_errors = state.recent_errors[-self.lookback_count:]
        
        # Count rate limit errors
        rate_limit_patterns = [
            "rate limit", "rate_limit", "429", "too many requests",
            "throttl", "quota", "exceeded"
        ]
        
        rate_limit_count = 0
        for error in recent_errors:
            error_str = str(error).lower()
            if any(pattern in error_str for pattern in rate_limit_patterns):
                rate_limit_count += 1
        
        if rate_limit_count >= self.rate_limit_threshold:
            return self._create_pattern(
                state,
                confidence=0.90,
                severity="medium",
                description=f"Agent encountered {rate_limit_count} rate limit errors",
                metadata={
                    "rate_limit_count": rate_limit_count,
                    "recent_errors": recent_errors,
                },
            )
        
        return None


class InfiniteRecursionDetector(PatternDetector):
    """
    COORD-002: Detects when agents are calling subordinates recursively without bound.
    
    Monitors:
    - Subordinate depth
    - Circular task delegation
    """
    
    def __init__(
        self,
        max_depth: int = 5,
        max_subordinates: int = 10,
    ):
        self.max_depth = max_depth
        self.max_subordinates = max_subordinates
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.INFINITE_RECURSION
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        # Check subordinate depth
        if state.subordinate_depth >= self.max_depth:
            return self._create_pattern(
                state,
                confidence=0.90,
                severity="high",
                description=f"Agent subordinate depth ({state.subordinate_depth}) exceeds limit",
                metadata={
                    "subordinate_depth": state.subordinate_depth,
                    "max_depth": self.max_depth,
                    "recursion_type": "depth",
                },
            )
        
        # Check total subordinate count
        if state.subordinate_count >= self.max_subordinates:
            return self._create_pattern(
                state,
                confidence=0.85,
                severity="medium",
                description=f"Agent has spawned {state.subordinate_count} subordinates",
                metadata={
                    "subordinate_count": state.subordinate_count,
                    "max_subordinates": self.max_subordinates,
                    "recursion_type": "count",
                },
            )
        
        return None


class OutputDegradationDetector(PatternDetector):
    """
    OUT-010: Detects when agent output quality is degrading.
    
    Monitors:
    - Response length trends
    - Content quality indicators
    - Error message frequency in responses
    """
    
    def __init__(
        self,
        min_response_length: int = 50,
        degradation_threshold: float = 0.5,
    ):
        self.min_response_length = min_response_length
        self.degradation_threshold = degradation_threshold
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.OUTPUT_DEGRADATION
    
    @property
    def is_deep(self) -> bool:
        return True  # Analyzes multiple response trends

    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        responses = state.recent_responses
        
        if len(responses) < 3:
            return None
        
        # Check for decreasing response lengths
        lengths = [len(r) for r in responses]
        if len(lengths) >= 3:
            # Check if lengths are consistently decreasing
            decreasing_count = sum(
                1 for i in range(len(lengths) - 1)
                if lengths[i + 1] < lengths[i] * self.degradation_threshold
            )
            
            if decreasing_count >= len(lengths) - 2:
                return self._create_pattern(
                    state,
                    confidence=0.75,
                    severity="medium",
                    description="Agent response quality appears to be degrading",
                    metadata={
                        "response_lengths": lengths,
                        "degradation_type": "length_decrease",
                    },
                )
        
        # Check for very short responses
        if state.last_response and len(state.last_response) < self.min_response_length:
            # Check if this is a pattern
            short_count = sum(1 for r in responses[-5:] if len(r) < self.min_response_length)
            if short_count >= 3:
                return self._create_pattern(
                    state,
                    confidence=0.70,
                    severity="low",
                    description=f"Agent producing very short responses ({short_count} recent)",
                    metadata={
                        "short_response_count": short_count,
                        "min_length": self.min_response_length,
                        "degradation_type": "short_responses",
                    },
                )
        
        return None


class StuckApproachDetector(PatternDetector):
    """
    Issue #218: Detects when an agent is stuck using the same failing approach repeatedly.
    
    This is different from RepetitiveActionDetector because it specifically looks for:
    1. Same tool being called repeatedly (3+ times)
    2. Same or similar arguments (approach not changing)
    3. Results indicating failure/truncation/no progress
    
    The key insight is that some repetition is VALID (pagination, polling), but
    repetition with consistent FAILURE indicates the agent should try a different approach.
    """
    
    # Signals that indicate the approach is failing
    FAILURE_SIGNALS = [
        "truncat", "unable to", "failed", "error", "cannot",
        "not found", "timeout", "limit", "too large", "exceeded",
        "same result", "no change", "still", "again",
    ]
    
    # Signals that indicate productive progress (exemptions)
    PROGRESS_SIGNALS = [
        "page", "offset", "cursor", "next", "item", "row",
        "processing", "completed", "success", "found",
    ]
    
    def __init__(
        self,
        min_repeats: int = 4,  # Slightly higher threshold than RepetitiveActionDetector
        lookback_count: int = 10,
    ):
        self.min_repeats = min_repeats
        self.lookback_count = lookback_count
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.RESPONSE_LOOP
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        tool_calls = state.recent_tool_calls[-self.lookback_count:]
        tool_results = state.recent_tool_results[-self.lookback_count:]
        
        if len(tool_calls) < self.min_repeats:
            return None
        
        # Group tool calls by tool name
        tool_groups: Dict[str, List[int]] = {}
        for i, tc in enumerate(tool_calls):
            tool_name = tc.get("tool_name", "unknown")
            if tool_name not in tool_groups:
                tool_groups[tool_name] = []
            tool_groups[tool_name].append(i)
        
        # Check each tool group for stuck patterns
        for tool_name, indices in tool_groups.items():
            if len(indices) < self.min_repeats:
                continue
            
            # Get arguments for this tool's calls
            args_list = []
            for i in indices:
                args = tool_calls[i].get("tool_args", tool_calls[i].get("arguments", {}))
                args_list.append(json.dumps(args, sort_keys=True) if isinstance(args, dict) else str(args))
            
            # Check argument variety (are they trying different things?)
            unique_args = len(set(args_list))
            
            # Low variety = same approach being repeated
            if unique_args <= 2 and len(args_list) >= self.min_repeats:
                # Now check if results indicate failure or stagnation
                failure_count = 0
                progress_count = 0
                
                for i in indices:
                    if i < len(tool_results):
                        result_str = str(tool_results[i]).lower()
                        
                        # Check for failure signals
                        if any(sig in result_str for sig in self.FAILURE_SIGNALS):
                            failure_count += 1
                        
                        # Check for progress signals (exemptions)
                        if any(sig in result_str for sig in self.PROGRESS_SIGNALS):
                            progress_count += 1
                
                # If more failures than progress, this is a stuck approach
                if failure_count >= 2 and failure_count > progress_count:
                    return self._create_pattern(
                        state,
                        confidence=0.90,
                        severity="high",
                        description=f"Agent stuck using same failing approach: '{tool_name}' called {len(indices)} times with same arguments, {failure_count} failures detected",
                        metadata={
                            "pattern_id": "LOOP-218",
                            "tool_name": tool_name,
                            "repeat_count": len(indices),
                            "unique_args": unique_args,
                            "failure_count": failure_count,
                            "progress_count": progress_count,
                            "suggestion": "Agent should try a DIFFERENT approach or tool instead of repeating the same failing method",
                        },
                    )
                
                # Even without explicit failures, no unique args = loop
                if unique_args == 1 and len(indices) >= self.min_repeats + 1:
                    return self._create_pattern(
                        state,
                        confidence=0.85,
                        severity="medium",
                        description=f"Agent repeating exact same call: '{tool_name}' with identical arguments {len(indices)} times",
                        metadata={
                            "pattern_id": "LOOP-218",
                            "tool_name": tool_name,
                            "repeat_count": len(indices),
                            "unique_args": unique_args,
                            "suggestion": "If the approach isn't working, try a different method",
                        },
                    )
        
        return None


class RepetitiveActionDetector(PatternDetector):
    """
    COORD-001: Detects when agents perform repetitive successful actions.
    
    Monitors:
    - Repeated tool calls with same parameters
    - Sequences of identical tool calls
    - Redundant state verification (e.g., ls -> ls -> ls)
    """
    
    def __init__(
        self,
        min_repeats: int = 3,
        lookback_count: int = 10,
    ):
        self.min_repeats = min_repeats
        self.lookback_count = lookback_count
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.REPETITIVE_ACTION
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        # Need at least more than min_repeats to detect a loop
        tool_calls = state.recent_tool_calls[-self.lookback_count:]
        if len(tool_calls) < self.min_repeats:
            return None
            
        # Extract call signatures (tool_name + normalized arguments)
        signatures = []
        for call in tool_calls:
            tool_name = call.get("tool_name", "unknown")
            args = call.get("args", {})
            # Normalize args: sort keys and convert to string
            normalized_args = json.dumps(args, sort_keys=True)
            signatures.append(f"{tool_name}({normalized_args})")
            
        # 1. Check for exact consecutive repeats
        current_sig = signatures[-1]
        repeat_count = 1
        for i in range(len(signatures) - 2, -1, -1):
            if signatures[i] == current_sig:
                repeat_count += 1
            else:
                break
                
        if repeat_count >= self.min_repeats:
            # For monitoring tasks, verify if results are stagnant
            if state.is_monitoring_task:
                recent_results = state.recent_tool_results[-repeat_count:]
                # Hash results to check for stagnation
                from python.helpers.hashing import content_hash
                result_hashes = [content_hash(str(r.get("output", ""))) for r in recent_results if isinstance(r, dict)]
                if len(set(result_hashes)) > 1:
                    # Results are varying, this is productive repetition
                    return None

            return self._create_pattern(
                state,
                confidence=0.95,
                severity="high",
                description=f"Agent performed the same action '{current_sig}' {repeat_count} times consecutively",
                metadata={
                    "repeated_action": current_sig,
                    "repeat_count": repeat_count,
                    "repeat_type": "exact_consecutive",
                    "is_stagnant": True if state.is_monitoring_task else None
                },
            )
            
        # 2. Check for repeating sequences (e.g., A-B-A-B-A-B)
        for seq_len in range(2, len(signatures) // 2 + 1):
            sequence = signatures[-seq_len:]
            seq_repeats = 1
            for i in range(len(signatures) - seq_len*2, -1, -seq_len):
                if signatures[i:i+seq_len] == sequence:
                    seq_repeats += 1
                else:
                    break
                    
            if seq_repeats >= self.min_repeats:
                return self._create_pattern(
                    state,
                    confidence=0.90,
                    severity="high",
                    description=f"Agent repeated a sequence of {seq_len} actions {seq_repeats} times",
                    metadata={
                        "repeated_sequence": sequence,
                        "repeat_count": seq_repeats,
                        "sequence_length": seq_len,
                        "repeat_type": "sequence",
                    },
                )
                
        return None


class MisroutedToolDetector(PatternDetector):
    """
    Detects when agent uses code_execution (terminal grep/cat) to access
    content that should be queried via MCP tools.

    Issue #791: Agent used 'grep' on local google_chat/ exported markdown
    files instead of using google-chat MCP tools, leading to a hallucination
    loop (empty grep results + fabricated citations).

    Detection logic:
    - Scans recent tool calls for code_execution_tool commands
    - Checks if the command references MCP-data paths/keywords
    - Triggers when count >= min_hits (default 2)
    """

    # Patterns that indicate agent is grepping MCP-data locally
    MCP_DATA_PATTERNS = [
        re.compile(r"google[_\-]?chat", re.IGNORECASE),
        re.compile(r"google[_\-]?drive", re.IGNORECASE),
    ]

    # Terminal commands that indicate file-based search instead of MCP
    TERMINAL_SEARCH_CMDS = re.compile(
        r"\b(grep|cat|head|tail|awk|sed|find|rg|ag)\b", re.IGNORECASE
    )

    def __init__(self, min_hits: int = 2):
        self.min_hits = min_hits

    @property
    def pattern_type(self) -> PatternType:
        return PatternType.REPETITIVE_ACTION

    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        if not state.recent_tool_calls:
            return None

        misrouted_count = 0
        matched_commands: List[str] = []

        for call in state.recent_tool_calls:
            tool_name = call.get("tool_name", "")
            if tool_name != "code_execution_tool":
                continue

            # Extract command text from tool_args or arguments
            code = (
                call.get("tool_args", {}).get("code", "")
                or call.get("arguments", {}).get("code", "")
            )
            if not code:
                continue

            # Check if command is a terminal search command
            if not self.TERMINAL_SEARCH_CMDS.search(code):
                continue

            # Check if command references MCP-data paths
            for pattern in self.MCP_DATA_PATTERNS:
                if pattern.search(code):
                    misrouted_count += 1
                    matched_commands.append(code[:120])
                    break

        if misrouted_count < self.min_hits:
            return None

        severity = "high" if misrouted_count >= 3 else "medium"
        confidence = min(0.95, 0.80 + (misrouted_count * 0.05))

        return self._create_pattern(
            state,
            confidence=confidence,
            severity=severity,
            description=(
                f"Agent used terminal grep/cat {misrouted_count}x on MCP-data "
                f"(google_chat/) instead of using Google Chat MCP tools. "
                f"This leads to hallucinated results from empty grep output."
            ),
            metadata={
                "misrouted_count": misrouted_count,
                "matched_commands": matched_commands,
                "suggestion": (
                    "Use google-chat MCP tools instead: "
                    "google_chat_search_all_spaces, google_chat_search_messages, "
                    "or google_chat_list_messages"
                ),
                "anti_pattern": "terminal_grep_on_mcp_data",
            },
        )


class VerdictPatternDetector(PatternDetector):
    """
    Issue #1093: Detects repeated verdict patterns in tool results.
    
    Browser agents and E2E test loops often produce tool results with unique
    content (timestamps, GUIDs, screenshots) but a consistent verdict string
    like 'QUALITY: FAIL' or 'VERDICT: FAIL'. ResponseLoopDetector misses these
    because the overall similarity is low. This detector extracts verdict
    keywords and triggers when the same verdict appears 3+ consecutive times.
    
    Detects patterns like:
    - QUALITY: FAIL repeated 3+ times
    - VERDICT: FAIL repeated 3+ times
    - RESULT: PASS/FAIL cycles that indicate retesting without fixing
    """
    
    # Verdict extraction patterns
    VERDICT_PATTERNS = [
        re.compile(r"QUALITY\s*:\s*(FAIL|PASS|ERROR)", re.IGNORECASE),
        re.compile(r"VERDICT\s*:\s*(FAIL|PASS|ERROR)", re.IGNORECASE),
        re.compile(r"RESULT\s*:\s*(FAIL|PASS|ERROR)", re.IGNORECASE),
        re.compile(r"STATUS\s*:\s*(FAIL|PASS|ERROR|FAILED|PASSED)", re.IGNORECASE),
        re.compile(r"TEST\s+(?:RESULT|STATUS)\s*:\s*(FAIL|PASS|ERROR)", re.IGNORECASE),
    ]
    
    def __init__(
        self,
        consecutive_threshold: int = 3,
        lookback_count: int = 10,
    ):
        self.consecutive_threshold = consecutive_threshold
        self.lookback_count = lookback_count
    
    @property
    def pattern_type(self) -> PatternType:
        return PatternType.VERDICT_PATTERN
    
    async def detect(self, state: AgentState) -> Optional[DetectedPattern]:
        tool_calls = state.recent_tool_calls[-self.lookback_count:]
        if len(tool_calls) < self.consecutive_threshold:
            return None
        
        # Extract verdicts from tool results
        verdicts: List[str] = []
        for call in tool_calls:
            result = str(call.get("result", ""))
            verdict = self._extract_verdict(result)
            if verdict:
                verdicts.append(verdict)
        
        if len(verdicts) < self.consecutive_threshold:
            return None
        
        # Check for consecutive same-verdict (must be FAIL-type)
        consecutive_count = 1
        last_verdict = verdicts[-1]
        for i in range(len(verdicts) - 2, -1, -1):
            if verdicts[i] == last_verdict:
                consecutive_count += 1
            else:
                break
        
        if consecutive_count >= self.consecutive_threshold and "FAIL" in last_verdict.upper():
            severity = "critical" if consecutive_count >= 5 else "high"
            confidence = min(0.95, 0.80 + (consecutive_count * 0.03))
            
            return self._create_pattern(
                state,
                confidence=confidence,
                severity=severity,
                description=(
                    f"Verdict pattern loop detected: '{last_verdict}' appeared "
                    f"{consecutive_count} consecutive times. Agent is re-testing "
                    f"without fixing the underlying issue."
                ),
                metadata={
                    "pattern_id": "LOOP-1093",
                    "verdict": last_verdict,
                    "consecutive_count": consecutive_count,
                    "threshold": self.consecutive_threshold,
                    "suggestion": (
                        "Stop re-testing and delegate to a fix agent. "
                        "The same verdict repeating means the test will keep "
                        "failing until the root cause is addressed."
                    ),
                },
            )
        
        return None
    
    def _extract_verdict(self, text: str) -> Optional[str]:
        """Extract a verdict string from tool result text."""
        for pattern in self.VERDICT_PATTERNS:
            match = pattern.search(text)
            if match:
                # Return the full matched verdict line
                return match.group(0).upper()
        return None
