from __future__ import annotations
"""
Supervisor API Endpoints for AGIX

This module provides REST API endpoints for controlling and monitoring
the LLM Supervisor.

RCA-249 Phase 7: MasterAgentSupervisor has been permanently removed.
Deterministic supervisor endpoints now return "not available" for
backward API compatibility.

Endpoints:
- GET  /api/supervisor/status - Get supervisor state and stats
- GET  /api/supervisor/health - Get combined health status
- GET  /api/supervisor/agents - List monitored agents
- GET  /api/supervisor/interventions - Get intervention history
- GET  /api/supervisor/stats - Get statistics
- GET  /api/supervisor/config - Get configuration
- GET  /api/supervisor/llm/status - Get LLM supervisor status
"""

import logging
from typing import Any, Dict, List, Optional

# RCA-249 Phase 7: MasterAgentSupervisor permanently removed.
# These no-op stubs preserve backward compatibility for any code
# that still calls get_supervisor().
def get_supervisor(): return None
def set_supervisor(s): pass
def create_and_start_supervisor(**kwargs): return None

class _DeprecatedConfig:
    """Stub for removed SupervisorConfig."""
    def to_dict(self): return {"status": "removed", "reason": "RCA-249"}
SupervisorConfig = _DeprecatedConfig

# Import LLM supervisor
try:
    from python.helpers.supervisor_agent import (
        SupervisorAgent,
        get_llm_supervisor,
        SupervisorConfig as LLMSupervisorConfig,
    )
    LLM_SUPERVISOR_AVAILABLE = True
except ImportError:
    LLM_SUPERVISOR_AVAILABLE = False
    get_llm_supervisor = lambda: None

# Import event bus for signal stats
try:
    from python.helpers.event_bus import get_event_bus, AgentSignal, SignalType
    from datetime import datetime, timezone
    EVENT_BUS_AVAILABLE = True
except ImportError:
    EVENT_BUS_AVAILABLE = False
    get_event_bus = lambda: None
    AgentSignal = None
    SignalType = None

logger = logging.getLogger(__name__)


# =============================================================================
# API Handler Functions
# =============================================================================

async def get_supervisor_status() -> Dict[str, Any]:
    """
    Get the current supervisor status (combined from both supervisor types).
    
    Returns:
        Dict with success flag and status information for both supervisors
    """
    # Get deterministic supervisor status
    det_supervisor = get_supervisor()
    det_status = None
    if det_supervisor is not None:
        det_status = det_supervisor.get_status()
    
    # Get LLM supervisor status
    llm_supervisor = get_llm_supervisor() if LLM_SUPERVISOR_AVAILABLE else None
    llm_status = None
    if llm_supervisor is not None:
        llm_status = {
            "state": "running" if llm_supervisor._running else "stopped",
            "monitored_agents": llm_supervisor.get_registered_agents(),
            "stats": llm_supervisor.get_stats(),
            "config": llm_supervisor.config.to_dict() if llm_supervisor.config else None,
        }
    
    # Determine overall state
    if det_status is not None or llm_status is not None:
        overall_state = "running"
    else:
        overall_state = "not_initialized"
    
    return {
        "success": True,
        "status": {
            "state": overall_state,
            "deterministic_supervisor": det_status,
            "llm_supervisor": llm_status,
            # For backward compatibility, include top-level fields
            "config": det_status.get("config") if det_status else None,
            "stats": det_status.get("stats") if det_status else None,
            "monitored_agents": (det_status.get("monitored_agents", []) if det_status else []),
        }
    }


async def get_combined_health() -> Dict[str, Any]:
    """
    Get combined health status of all supervisor components.
    
    Returns:
        Dict with health status for each component
    """
    health = {
        "healthy": True,
        "components": {},
        "summary": {},
    }
    
    # Check deterministic supervisor
    det_supervisor = get_supervisor()
    if det_supervisor is not None:
        det_running = det_supervisor.is_running
        det_agents = len(det_supervisor.get_monitored_agents())
        det_stats = det_supervisor.get_stats()
        health["components"]["deterministic_supervisor"] = {
            "status": "healthy" if det_running else "stopped",
            "running": det_running,
            "monitored_agents": det_agents,
            "patterns_detected": det_stats.get("patterns_detected", 0),
            "interventions_executed": det_stats.get("interventions_executed", 0),
        }
    else:
        health["components"]["deterministic_supervisor"] = {
            "status": "not_initialized",
            "running": False,
            "monitored_agents": 0,
        }
    
    # Check LLM supervisor
    llm_supervisor = get_llm_supervisor() if LLM_SUPERVISOR_AVAILABLE else None
    if llm_supervisor is not None:
        llm_running = llm_supervisor._running
        llm_agents = len(llm_supervisor.get_registered_agents())
        llm_stats = llm_supervisor.get_stats()
        health["components"]["llm_supervisor"] = {
            "status": "healthy" if llm_running else "stopped",
            "running": llm_running,
            "monitored_agents": llm_agents,
            "signals_received": llm_stats.get("signals_received", 0),
            "check_ins_performed": llm_stats.get("check_ins_performed", 0),
            "interventions_executed": llm_stats.get("interventions_executed", 0),
            "model": llm_stats.get("model", "unknown"),
        }
    else:
        health["components"]["llm_supervisor"] = {
            "status": "not_available" if not LLM_SUPERVISOR_AVAILABLE else "not_initialized",
            "running": False,
            "monitored_agents": 0,
        }
    
    # Check event bus
    event_bus = get_event_bus() if EVENT_BUS_AVAILABLE else None
    if event_bus is not None:
        bus_stats = event_bus.get_stats()
        health["components"]["event_bus"] = {
            "status": "healthy",
            "signals_published": bus_stats.get("signals_published", 0),
            "history_size": bus_stats.get("history_size", 0),
            "subscriber_count": bus_stats.get("subscriber_count", 0),
        }
    else:
        health["components"]["event_bus"] = {
            "status": "not_available" if not EVENT_BUS_AVAILABLE else "not_initialized",
        }
    
    # Calculate summary
    total_agents = (
        health["components"]["deterministic_supervisor"].get("monitored_agents", 0) +
        health["components"]["llm_supervisor"].get("monitored_agents", 0)
    )
    
    any_running = (
        health["components"]["deterministic_supervisor"].get("running", False) or
        health["components"]["llm_supervisor"].get("running", False)
    )
    
    health["summary"] = {
        "any_supervisor_running": any_running,
        "total_monitored_agents": total_agents,
        "deterministic_running": health["components"]["deterministic_supervisor"].get("running", False),
        "llm_running": health["components"]["llm_supervisor"].get("running", False),
    }
    
    health["healthy"] = any_running
    
    return {
        "success": True,
        "health": health,
    }


async def get_llm_supervisor_status() -> Dict[str, Any]:
    """
    Get the LLM supervisor status specifically.
    
    Returns:
        Dict with LLM supervisor status
    """
    if not LLM_SUPERVISOR_AVAILABLE:
        return {
            "success": False,
            "message": "LLM supervisor module not available",
            "status": None,
        }
    
    llm_supervisor = get_llm_supervisor()
    
    if llm_supervisor is None:
        return {
            "success": True,
            "status": {
                "state": "not_initialized",
                "running": False,
                "monitored_agents": [],
                "stats": None,
            }
        }
    
    return {
        "success": True,
        "status": {
            "state": "running" if llm_supervisor._running else "stopped",
            "running": llm_supervisor._running,
            "monitored_agents": llm_supervisor.get_registered_agents(),
            "stats": llm_supervisor.get_stats(),
            "config": llm_supervisor.config.to_dict() if llm_supervisor.config else None,
        }
    }


async def start_supervisor(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Start the supervisor.
    
    Args:
        config: Optional configuration overrides
        
    Returns:
        Dict with success flag and message
    """
    supervisor = get_supervisor()
    
    if supervisor is not None and supervisor.is_running:
        return {
            "success": False,
            "message": "Supervisor is already running",
        }
    
    try:
        supervisor = await create_and_start_supervisor(config=config)
        return {
            "success": True,
            "message": "Supervisor started",
            "status": supervisor.get_status(),
        }
    except Exception as e:
        logger.error(f"Failed to start supervisor: {e}")
        return {
            "success": False,
            "message": f"Failed to start supervisor: {str(e)}",
        }


async def stop_supervisor() -> Dict[str, Any]:
    """
    Stop the supervisor.
    
    Returns:
        Dict with success flag and message
    """
    supervisor = get_supervisor()
    
    if supervisor is None:
        return {
            "success": False,
            "message": "Supervisor is not running",
        }
    
    try:
        await supervisor.stop()
        set_supervisor(None)
        return {
            "success": True,
            "message": "Supervisor stopped",
        }
    except Exception as e:
        logger.error(f"Failed to stop supervisor: {e}")
        return {
            "success": False,
            "message": f"Failed to stop supervisor: {str(e)}",
        }


async def pause_supervisor() -> Dict[str, Any]:
    """
    Pause the supervisor monitoring.
    
    Returns:
        Dict with success flag and message
    """
    supervisor = get_supervisor()
    
    if supervisor is None:
        return {
            "success": False,
            "message": "Supervisor is not running",
        }
    
    try:
        await supervisor.pause()
        return {
            "success": True,
            "message": "Supervisor paused",
        }
    except Exception as e:
        logger.error(f"Failed to pause supervisor: {e}")
        return {
            "success": False,
            "message": f"Failed to pause supervisor: {str(e)}",
        }


async def resume_supervisor() -> Dict[str, Any]:
    """
    Resume the supervisor monitoring.
    
    Returns:
        Dict with success flag and message
    """
    supervisor = get_supervisor()
    
    if supervisor is None:
        return {
            "success": False,
            "message": "Supervisor is not running",
        }
    
    try:
        await supervisor.resume()
        return {
            "success": True,
            "message": "Supervisor resumed",
        }
    except Exception as e:
        logger.error(f"Failed to resume supervisor: {e}")
        return {
            "success": False,
            "message": f"Failed to resume supervisor: {str(e)}",
        }


async def get_monitored_agents() -> Dict[str, Any]:
    """
    Get list of monitored agents from both supervisor types.
    
    Returns:
        Dict with success flag and list of agents
    """
    all_agents = []
    
    # Get agents from deterministic supervisor
    det_supervisor = get_supervisor()
    if det_supervisor is not None:
        det_agents = det_supervisor.get_monitored_agents()
        for a in det_agents:
            agent_dict = a.to_dict()
            agent_dict["supervisor_type"] = "deterministic"
            all_agents.append(agent_dict)
    
    # Get agents from LLM supervisor
    llm_supervisor = get_llm_supervisor() if LLM_SUPERVISOR_AVAILABLE else None
    if llm_supervisor is not None:
        llm_agent_ids = llm_supervisor.get_registered_agents()
        for agent_id in llm_agent_ids:
            # Check if already in list (avoid duplicates)
            if not any(a.get("agent_id") == agent_id for a in all_agents):
                all_agents.append({
                    "agent_id": agent_id,
                    "supervisor_type": "llm",
                    "context_id": None,  # LLM supervisor doesn't track this
                })
    
    return {
        "success": True,
        "agents": all_agents,
        "count": len(all_agents),
    }


async def get_interventions(agent_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get intervention history.
    
    Args:
        agent_id: Optional filter by agent ID
        
    Returns:
        Dict with success flag and list of interventions
    """
    supervisor = get_supervisor()
    
    if supervisor is None:
        return {
            "success": True,
            "interventions": [],
        }
    
    try:
        if agent_id:
            records = await supervisor.loop_prevention.get_intervention_history(agent_id)
        else:
            records = await supervisor.loop_prevention.get_all_interventions()
        
        return {
            "success": True,
            "interventions": [r.to_dict() for r in records],
        }
    except Exception as e:
        logger.error(f"Failed to get interventions: {e}")
        return {
            "success": False,
            "message": f"Failed to get interventions: {str(e)}",
            "interventions": [],
        }


async def get_supervisor_stats() -> Dict[str, Any]:
    """
    Get supervisor statistics.
    
    Returns:
        Dict with success flag and statistics
    """
    supervisor = get_supervisor()
    
    if supervisor is None:
        return {
            "success": True,
            "stats": {
                "total_checks": 0,
                "patterns_detected": 0,
                "interventions_executed": 0,
                "interventions_successful": 0,
                "escalations": 0,
                "uptime_seconds": None,
            },
        }
    
    return {
        "success": True,
        "stats": supervisor.get_stats(),
    }


async def get_supervisor_config() -> Dict[str, Any]:
    """
    Get supervisor configuration.
    
    Returns:
        Dict with success flag and configuration
    """
    supervisor = get_supervisor()
    
    if supervisor is None:
        return {
            "success": True,
            "config": SupervisorConfig().to_dict(),
        }
    
    return {
        "success": True,
        "config": supervisor.config.to_dict(),
    }


async def update_supervisor_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update supervisor configuration.
    
    Args:
        updates: Configuration updates to apply
        
    Returns:
        Dict with success flag and updated configuration
    """
    supervisor = get_supervisor()
    
    if supervisor is None:
        return {
            "success": False,
            "message": "Supervisor is not running",
        }
    
    try:
        # Apply updates to config
        for key, value in updates.items():
            if hasattr(supervisor.config, key):
                setattr(supervisor.config, key, value)
        
        return {
            "success": True,
            "message": "Configuration updated",
            "config": supervisor.config.to_dict(),
        }
    except Exception as e:
        logger.error(f"Failed to update config: {e}")
        return {
            "success": False,
            "message": f"Failed to update config: {str(e)}",
        }


async def clear_agent_history(agent_id: str) -> Dict[str, Any]:
    """
    Clear intervention history for a specific agent.
    
    Args:
        agent_id: Agent ID to clear history for
        
    Returns:
        Dict with success flag and message
    """
    supervisor = get_supervisor()
    
    if supervisor is None:
        return {
            "success": False,
            "message": "Supervisor is not running",
        }
    
    try:
        await supervisor.loop_prevention.clear_agent_history(agent_id)
        return {
            "success": True,
            "message": f"History cleared for agent {agent_id}",
        }
    except Exception as e:
        logger.error(f"Failed to clear history: {e}")
        return {
            "success": False,
            "message": f"Failed to clear history: {str(e)}",
        }


async def clear_all_history() -> Dict[str, Any]:
    """
    Clear all intervention history.
    
    Returns:
        Dict with success flag and message
    """
    supervisor = get_supervisor()
    
    if supervisor is None:
        return {
            "success": False,
            "message": "Supervisor is not running",
        }
    
    try:
        await supervisor.loop_prevention.clear_all_history()
        return {
            "success": True,
            "message": "All history cleared",
        }
    except Exception as e:
        logger.error(f"Failed to clear all history: {e}")
        return {
            "success": False,
            "message": f"Failed to clear all history: {str(e)}",
        }


# =============================================================================
# Lessons Learned API Handlers
# =============================================================================

async def get_lessons_stats() -> Dict[str, Any]:
    """
    Get lessons learned statistics.
    
    Returns:
        Dict with success flag and statistics
    """
    supervisor = get_supervisor()
    
    if supervisor is None:
        return {
            "success": True,
            "stats": {"available": False, "message": "Supervisor not running"},
        }
    
    return {
        "success": True,
        "stats": supervisor.get_lessons_stats(),
    }


async def get_relevant_lessons(pattern_type: str, limit: int = 5) -> Dict[str, Any]:
    """
    Get lessons relevant to a pattern type.
    
    Args:
        pattern_type: The pattern type to get lessons for
        limit: Maximum number of lessons to return
        
    Returns:
        Dict with success flag and lessons
    """
    supervisor = get_supervisor()
    
    if supervisor is None:
        return {
            "success": False,
            "message": "Supervisor is not running",
            "lessons": [],
        }
    
    try:
        lessons = await supervisor.get_relevant_lessons(pattern_type, limit)
        return {
            "success": True,
            "lessons": lessons,
        }
    except Exception as e:
        logger.error(f"Failed to get relevant lessons: {e}")
        return {
            "success": False,
            "message": f"Failed to get relevant lessons: {str(e)}",
            "lessons": [],
        }


async def export_lessons(output_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Export all lessons to a memory bank file.
    
    Args:
        output_path: Optional path for the export file
        
    Returns:
        Dict with success flag and export path
    """
    supervisor = get_supervisor()
    
    if supervisor is None:
        return {
            "success": False,
            "message": "Supervisor is not running",
        }
    
    try:
        path = await supervisor.export_lessons(output_path)
        if path:
            return {
                "success": True,
                "message": f"Lessons exported to {path}",
                "path": path,
            }
        else:
            return {
                "success": False,
                "message": "Failed to export lessons",
            }
    except Exception as e:
        logger.error(f"Failed to export lessons: {e}")
        return {
            "success": False,
            "message": f"Failed to export lessons: {str(e)}",
        }


async def record_task_completion(
    agent_id: str,
    task_description: str,
    success: bool,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Record a task completion and extract lessons.
    
    Args:
        agent_id: ID of the agent that completed the task
        task_description: Description of the task
        success: Whether the task was successful
        notes: Optional notes about the completion
        
    Returns:
        Dict with success flag, message, and lesson info if created
    """
    supervisor = get_supervisor()
    
    if supervisor is None:
        return {
            "success": False,
            "message": "Supervisor is not running",
        }
    
    try:
        lesson_info = await supervisor.record_task_completion(
            agent_id=agent_id,
            task_description=task_description,
            success=success,
            notes=notes,
        )
        
        result = {
            "success": True,
            "message": f"Task completion recorded for agent {agent_id}",
        }
        
        if lesson_info:
            result["lesson_created"] = True
            result["lesson"] = lesson_info
        else:
            result["lesson_created"] = False
            result["lesson"] = None
        
        return result
    except Exception as e:
        logger.error(f"Failed to record task completion: {e}")
        return {
            "success": False,
            "message": f"Failed to record task completion: {str(e)}",
        }


# =============================================================================
# Route Registration
# =============================================================================

def get_supervisor_routes() -> List[Dict[str, Any]]:
    """
    Get list of supervisor routes for registration.
    
    Returns:
        List of route definitions
    """
    return [
        {"path": "/api/supervisor/status", "methods": ["GET"], "handler": "get_supervisor_status"},
        {"path": "/api/supervisor/start", "methods": ["POST"], "handler": "start_supervisor"},
        {"path": "/api/supervisor/stop", "methods": ["POST"], "handler": "stop_supervisor"},
        {"path": "/api/supervisor/pause", "methods": ["POST"], "handler": "pause_supervisor"},
        {"path": "/api/supervisor/resume", "methods": ["POST"], "handler": "resume_supervisor"},
        {"path": "/api/supervisor/agents", "methods": ["GET"], "handler": "get_monitored_agents"},
        {"path": "/api/supervisor/interventions", "methods": ["GET"], "handler": "get_interventions"},
        {"path": "/api/supervisor/stats", "methods": ["GET"], "handler": "get_supervisor_stats"},
        {"path": "/api/supervisor/config", "methods": ["GET"], "handler": "get_supervisor_config"},
        {"path": "/api/supervisor/config", "methods": ["POST"], "handler": "update_supervisor_config"},
        {"path": "/api/supervisor/clear_history", "methods": ["POST"], "handler": "clear_agent_history"},
    ]


def register_supervisor_routes(app):
    """
    Register supervisor routes with a Flask app.
    
    Args:
        app: Flask application instance
    """
    from flask import request, jsonify
    
    @app.route("/api/supervisor/status", methods=["GET"])
    async def api_supervisor_status():
        result = await get_supervisor_status()
        return jsonify(result)
    
    @app.route("/api/supervisor/start", methods=["POST"])
    async def api_supervisor_start():
        data = request.get_json() or {}
        result = await start_supervisor(config=data.get("config"))
        return jsonify(result)
    
    @app.route("/api/supervisor/stop", methods=["POST"])
    async def api_supervisor_stop():
        result = await stop_supervisor()
        return jsonify(result)
    
    @app.route("/api/supervisor/pause", methods=["POST"])
    async def api_supervisor_pause():
        result = await pause_supervisor()
        return jsonify(result)
    
    @app.route("/api/supervisor/resume", methods=["POST"])
    async def api_supervisor_resume():
        result = await resume_supervisor()
        return jsonify(result)
    
    @app.route("/api/supervisor/agents", methods=["GET"])
    async def api_supervisor_agents():
        result = await get_monitored_agents()
        return jsonify(result)
    
    @app.route("/api/supervisor/interventions", methods=["GET"])
    async def api_supervisor_interventions():
        agent_id = request.args.get("agent_id")
        result = await get_interventions(agent_id=agent_id)
        return jsonify(result)
    
    @app.route("/api/supervisor/stats", methods=["GET"])
    async def api_supervisor_stats():
        result = await get_supervisor_stats()
        return jsonify(result)
    
    @app.route("/api/supervisor/config", methods=["GET"])
    async def api_supervisor_config_get():
        result = await get_supervisor_config()
        return jsonify(result)
    
    @app.route("/api/supervisor/config", methods=["POST"])
    async def api_supervisor_config_post():
        data = request.get_json() or {}
        result = await update_supervisor_config(data)
        return jsonify(result)
    
    @app.route("/api/supervisor/clear_history", methods=["POST"])
    async def api_supervisor_clear_history():
        data = request.get_json() or {}
        agent_id = data.get("agent_id")
        if agent_id:
            result = await clear_agent_history(agent_id)
        else:
            result = await clear_all_history()
        return jsonify(result)
    
    # Lessons Learned Routes
    @app.route("/api/supervisor/lessons/stats", methods=["GET"])
    async def api_supervisor_lessons_stats():
        result = await get_lessons_stats()
        return jsonify(result)
    
    @app.route("/api/supervisor/lessons", methods=["GET"])
    async def api_supervisor_lessons():
        pattern_type = request.args.get("pattern_type", "")
        limit = int(request.args.get("limit", 5))
        result = await get_relevant_lessons(pattern_type=pattern_type, limit=limit)
        return jsonify(result)
    
    @app.route("/api/supervisor/lessons/export", methods=["POST"])
    async def api_supervisor_lessons_export():
        data = request.get_json() or {}
        output_path = data.get("output_path")
        result = await export_lessons(output_path=output_path)
        return jsonify(result)
    
    @app.route("/api/supervisor/task_completion", methods=["POST"])
    async def api_supervisor_task_completion():
        data = request.get_json() or {}
        agent_id = data.get("agent_id", "")
        task_description = data.get("task_description", "")
        success = data.get("success", True)
        notes = data.get("notes")
        result = await record_task_completion(
            agent_id=agent_id,
            task_description=task_description,
            success=success,
            notes=notes,
        )
        return jsonify(result)
    
    # New combined health and LLM supervisor routes
    @app.route("/api/supervisor/health", methods=["GET"])
    async def api_supervisor_health():
        result = await get_combined_health()
        return jsonify(result)
    
    @app.route("/api/supervisor/llm/status", methods=["GET"])
    async def api_supervisor_llm_status():
        result = await get_llm_supervisor_status()
        return jsonify(result)
    
    # Test signal injection endpoint (for testing supervisor intervention)
    @app.route("/api/supervisor/test/inject_signal", methods=["POST"])
    async def api_supervisor_inject_signal():
        """
        Inject a test signal into the event bus to trigger supervisor intervention.
        
        Request body:
        {
            "signal_type": "CONTEXT_WARNING" | "CONTEXT_CRITICAL" | "RESPONSE_LOOP" | 
                          "TOOL_FAILURE_LOOP" | "PROGRESS_STALL" | "RATE_LIMITED" | "RECURSION_DEPTH",
            "agent_id": "test_agent",
            "context_id": "test_context",
            "severity": "low" | "medium" | "high" | "critical",
            "details": {...},  # Optional additional details
            "iteration": 5  # Optional iteration number
        }
        """
        if not EVENT_BUS_AVAILABLE:
            return jsonify({
                "success": False,
                "message": "Event bus not available"
            })
        
        data = request.get_json() or {}
        
        # Validate required fields
        signal_type_str = data.get("signal_type", "").upper()
        agent_id = data.get("agent_id", "test_agent")
        context_id = data.get("context_id", "test_context")
        severity = data.get("severity", "high")
        details = data.get("details", {})
        iteration = data.get("iteration")
        
        # Map string to SignalType enum
        signal_type_map = {
            "CONTEXT_WARNING": SignalType.CONTEXT_WARNING,
            "CONTEXT_CRITICAL": SignalType.CONTEXT_CRITICAL,
            "RESPONSE_LOOP": SignalType.RESPONSE_LOOP,
            "TOOL_FAILURE_LOOP": SignalType.TOOL_FAILURE_LOOP,
            "PROGRESS_STALL": SignalType.PROGRESS_STALL,
            "RATE_LIMITED": SignalType.RATE_LIMITED,
            "RECURSION_DEPTH": SignalType.RECURSION_DEPTH,
            "AGENT_ERROR": SignalType.AGENT_ERROR,
            "AGENT_STUCK": SignalType.AGENT_STUCK,
            "INTERVENTION_NEEDED": SignalType.INTERVENTION_NEEDED,
        }
        
        if signal_type_str not in signal_type_map:
            return jsonify({
                "success": False,
                "message": f"Invalid signal_type: {signal_type_str}. Valid types: {list(signal_type_map.keys())}"
            })
        
        signal_type = signal_type_map[signal_type_str]
        
        # Add default details based on signal type
        if signal_type == SignalType.CONTEXT_WARNING and "context_usage" not in details:
            details["context_usage"] = 0.78
        elif signal_type == SignalType.CONTEXT_CRITICAL and "context_usage" not in details:
            details["context_usage"] = 0.92
        elif signal_type == SignalType.RESPONSE_LOOP and "identical_responses" not in details:
            details["identical_responses"] = 4
        elif signal_type == SignalType.TOOL_FAILURE_LOOP and "consecutive_failures" not in details:
            details["consecutive_failures"] = 5
        elif signal_type == SignalType.PROGRESS_STALL and "iterations_without_progress" not in details:
            details["iterations_without_progress"] = 7
        elif signal_type == SignalType.RATE_LIMITED and "retry_after" not in details:
            details["retry_after"] = 60
        elif signal_type == SignalType.RECURSION_DEPTH and "depth" not in details:
            details["depth"] = 6
        
        # Create and publish signal
        try:
            signal = AgentSignal(
                signal_type=signal_type,
                agent_id=agent_id,
                context_id=context_id,
                timestamp=datetime.now(timezone.utc),
                severity=severity,
                details=details,
                iteration=iteration,
            )
            
            event_bus = get_event_bus()
            await event_bus.publish(signal)
            
            # Get updated stats
            bus_stats = event_bus.get_stats()
            
            # Check LLM supervisor stats if available
            llm_stats = None
            llm_supervisor = get_llm_supervisor() if LLM_SUPERVISOR_AVAILABLE else None
            if llm_supervisor is not None:
                llm_stats = llm_supervisor.get_stats()
            
            return jsonify({
                "success": True,
                "message": f"Signal {signal_type_str} injected successfully",
                "signal": signal.to_dict(),
                "event_bus_stats": bus_stats,
                "llm_supervisor_stats": llm_stats,
            })
        except Exception as e:
            logger.error(f"Failed to inject signal: {e}")
            return jsonify({
                "success": False,
                "message": f"Failed to inject signal: {str(e)}"
            })
    
    # Get event bus signals history
    @app.route("/api/supervisor/signals", methods=["GET"])
    async def api_supervisor_signals():
        """Get recent signals from the event bus."""
        if not EVENT_BUS_AVAILABLE:
            return jsonify({
                "success": False,
                "message": "Event bus not available",
                "signals": []
            })
        
        event_bus = get_event_bus()
        agent_id = request.args.get("agent_id")
        signal_type_str = request.args.get("signal_type")
        severity = request.args.get("severity")
        limit = int(request.args.get("limit", 50))
        
        # Convert signal type string to enum if provided
        signal_type = None
        if signal_type_str:
            try:
                signal_type = SignalType(signal_type_str.lower())
            except ValueError:
                pass
        
        signals = event_bus.get_recent_signals(
            agent_id=agent_id,
            signal_type=signal_type,
            severity=severity,
            limit=limit,
        )
        
        return jsonify({
            "success": True,
            "signals": [s.to_dict() for s in signals],
            "count": len(signals),
            "stats": event_bus.get_stats(),
        })
    
    logger.info("Supervisor API routes registered (including lessons learned, health, LLM supervisor, and test signal injection)")
