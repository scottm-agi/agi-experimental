"""
Deep-Dive Root Cause Analysis for Supervisor (Forgejo #1174).

Provides structured 5-Why RCA analysis when the supervisor detects an agent
is stuck. Unlike nudge_agent (which reads only the last 30 messages), this
module reads the full conversation HEAD + TAIL + ALL errors, classifies the
failure into one of 8 categories, and composes a targeted recovery plan.

Architecture doc: agix-devdocs/docs/architecture/pfr_supervisor_deep_dive_rca.md
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass  # Agent type is duck-typed via mock in tests


# ── Failure Classification Constants ──

FAILURE_CLASSES = [
    "WRONG_APPROACH",
    "DEPENDENCY_ERROR",
    "CONFIGURATION_ERROR",
    "TOOL_MISUSE",
    "CONTEXT_LOSS",
    "LOOP_PATTERN",
    "EXTERNAL_FAILURE",
    "SCOPE_CREEP",
]

DEFAULT_CLASSIFICATION = "WRONG_APPROACH"

# Error patterns to scan for in history
ERROR_PATTERNS = [
    "Error:", "ENOENT", "EACCES", "permission denied",
    "failed", "404", "500", "connection refused",
    "ModuleNotFoundError", "ImportError", "TypeError",
    "EADDRINUSE", "timeout", "ENOMEM", "SyntaxError",
    "Cannot find module", "npm ERR!", "Build error",
]


# ── Stage 1: Full Context Reader ──

def extract_analysis_context(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract HEAD, TAIL, errors, and tool stats from full conversation history.
    
    Returns a dict with keys:
        - head: First 10 messages (original task + early work)
        - tail: Last 20 messages (current state)
        - errors: Deduped list of error strings found across ALL messages
        - tool_stats: Dict with total_tool_calls, unique_tools, error_rate
    """
    if not history:
        return {
            "head": [],
            "tail": [],
            "errors": [],
            "tool_stats": {"total_tool_calls": 0, "unique_tools": 0, "error_rate": 0.0},
        }
    
    # Head: first 10 messages
    head = history[:10]
    
    # Tail: last 20 messages
    tail = history[-20:] if len(history) > 20 else history[:]
    
    # Errors: scan ALL messages for error patterns (deduped)
    seen_errors = set()
    errors = []
    for msg in history:
        content = str(msg.get("content", ""))
        for pattern in ERROR_PATTERNS:
            if pattern.lower() in content.lower():
                # Extract a short error signature for dedup
                error_sig = content[:200].strip()
                if error_sig not in seen_errors:
                    seen_errors.add(error_sig)
                    errors.append(error_sig)
                break  # One match per message is enough
    
    # Tool call stats
    tool_calls = {}
    total_tool_calls = 0
    error_tool_calls = 0
    for msg in history:
        content = str(msg.get("content", ""))
        role = msg.get("role", "")
        
        # Count tool usage from assistant messages
        if role == "assistant" or msg.get("ai", False):
            tool_matches = re.findall(r'"tool_name":\s*"([^"]+)"', content)
            for tool in tool_matches:
                tool_calls[tool] = tool_calls.get(tool, 0) + 1
                total_tool_calls += 1
        
        # Count error results from tool responses
        if role == "tool":
            for pattern in ERROR_PATTERNS:
                if pattern.lower() in content.lower():
                    error_tool_calls += 1
                    break
    
    error_rate = error_tool_calls / max(total_tool_calls, 1)
    
    return {
        "head": head,
        "tail": tail,
        "errors": errors,
        "tool_stats": {
            "total_tool_calls": total_tool_calls,
            "unique_tools": len(tool_calls),
            "error_rate": round(error_rate, 3),
            "tool_distribution": dict(sorted(tool_calls.items(), key=lambda x: -x[1])[:10]),
        },
    }


# ── Stage 2: Failure Classification ──

def classify_failure(rca_text: str) -> str:
    """Classify failure mode from RCA output text.
    
    Searches for one of the 8 known failure classes in the RCA text.
    Falls back to WRONG_APPROACH if none found.
    """
    rca_upper = rca_text.upper()
    for cls in FAILURE_CLASSES:
        if cls in rca_upper:
            return cls
    return DEFAULT_CLASSIFICATION


# ── Stage 3: RCA Prompt Composition ──

def compose_rca_prompt(context: Dict[str, Any], reason: str) -> str:
    """Compose the 5-Why Root Cause Analysis prompt for the utility LLM.
    
    Args:
        context: Output from extract_analysis_context()
        reason: Why the deep dive was triggered
    
    Returns:
        Formatted prompt string for the LLM
    """
    # Format head messages
    head_text = ""
    for msg in context.get("head", []):
        role = msg.get("role", "unknown")
        content = str(msg.get("content", ""))[:500]
        head_text += f"\n[{role}]: {content}"
    
    if not head_text:
        head_text = "(No history available)"
    
    # Format tail messages
    tail_text = ""
    for msg in context.get("tail", []):
        role = msg.get("role", "unknown")
        content = str(msg.get("content", ""))[:300]
        tail_text += f"\n[{role}]: {content}"
    
    if not tail_text:
        tail_text = "(No recent history)"
    
    # Format errors
    errors = context.get("errors", [])
    error_text = "\n".join(f"- {e[:150]}" for e in errors[:15]) if errors else "(No errors found)"
    
    # Tool stats
    stats = context.get("tool_stats", {})
    
    return f"""You are a senior engineering mentor performing Root Cause Analysis on a stuck agent.

## Trigger Reason
{reason}

## Conversation Start (First 10 Messages — ORIGINAL TASK)
{head_text}

## Recent Activity (Last 20 Messages — CURRENT STATE)
{tail_text}

## Error Summary (Deduped from Full History)
{error_text}

## Tool Usage Stats
- Total tool calls: {stats.get('total_tool_calls', 0)}
- Unique tools used: {stats.get('unique_tools', 0)}
- Error rate: {stats.get('error_rate', 0):.1%}
- Distribution: {json.dumps(stats.get('tool_distribution', {}), indent=2)}

## Your Task — 5-Why Root Cause Analysis

Respond ONLY with valid JSON matching this schema:

{{
    "original_goal": "What was the agent's original task?",
    "current_state": "What is the agent doing right now?",
    "divergence_point": "Where did the agent diverge from the goal?",
    "why_chain": [
        "1st Why: Why is the agent stuck?",
        "2nd Why: Why did that cause happen?",
        "3rd Why: Why was that possible?",
        "4th Why: Why wasn't it caught earlier?",
        "5th Why: What is the SYSTEMIC root cause?"
    ],
    "classification": "ONE OF: WRONG_APPROACH | DEPENDENCY_ERROR | CONFIGURATION_ERROR | TOOL_MISUSE | CONTEXT_LOSS | LOOP_PATTERN | EXTERNAL_FAILURE | SCOPE_CREEP",
    "recovery_plan": [
        "Step 1: Specific concrete action",
        "Step 2: Specific concrete action",
        "Step 3: Specific concrete action"
    ]
}}

Be specific and actionable. Reference exact tools, files, or commands when possible.
"""


# ── Stage 4: Full Deep Dive Analysis ──

async def deep_dive_analysis(
    agent: Any,
    reason: str = "Agent appears stuck",
) -> Dict[str, Any]:
    """Perform full deep-dive RCA analysis on a stuck agent.
    
    This is the main entry point, called by the supervisor's tool executor.
    
    Args:
        agent: The stuck agent (with .history, .call_utility_model)
        reason: Why the analysis was triggered
    
    Returns:
        Structured RCA report dict with classification, why_chain, recovery_plan
    """
    # 1. Extract full context
    history_messages = []
    if hasattr(agent, 'history') and agent.history:
        try:
            history_messages = agent.history.output()
        except Exception:
            pass
    
    context = extract_analysis_context(history_messages)
    
    # 2. Compose RCA prompt
    prompt = compose_rca_prompt(context, reason)
    
    # 3. Call utility model for 5-Why analysis
    rca_report = {
        "original_goal": "Unknown",
        "current_state": "Unknown",
        "divergence_point": "Unknown",
        "why_chain": ["Could not determine root cause"],
        "classification": DEFAULT_CLASSIFICATION,
        "recovery_plan": ["Try a different approach"],
        "context_stats": context["tool_stats"],
        "error_count": len(context["errors"]),
    }
    
    try:
        if hasattr(agent, 'call_utility_model'):
            raw_response = await agent.call_utility_model(
                system="You are a senior engineering mentor. Perform structured root cause analysis. Respond ONLY with valid JSON.",
                message=prompt,
            )
            
            if isinstance(raw_response, str):
                # Try to parse as JSON
                # Strip markdown code fences if present
                cleaned = raw_response.strip()
                if cleaned.startswith("```"):
                    cleaned = re.sub(r'^```\w*\n?', '', cleaned)
                    cleaned = re.sub(r'\n?```$', '', cleaned)
                    cleaned = cleaned.strip()
                
                try:
                    parsed = json.loads(cleaned)
                    if isinstance(parsed, dict):
                        rca_report.update(parsed)
                except json.JSONDecodeError:
                    # LLM returned non-JSON — extract what we can
                    rca_report["classification"] = classify_failure(raw_response)
                    rca_report["raw_response"] = raw_response[:500]
            
            # Ensure classification is valid
            if rca_report.get("classification") not in FAILURE_CLASSES:
                rca_report["classification"] = classify_failure(
                    rca_report.get("classification", "")
                )
    
    except Exception as e:
        rca_report["error"] = str(e)
        rca_report["classification"] = DEFAULT_CLASSIFICATION
    
    return rca_report
