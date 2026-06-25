from __future__ import annotations
"""
Mode Management API Endpoints

Provides REST API endpoints for managing agent modes in the MultiAgentDev system.
"""

from flask import Blueprint, request, jsonify
import logging

logger = logging.getLogger(__name__)

mode_bp = Blueprint('mode', __name__)


def get_mode_manager():
    """Get the mode manager instance, handling import errors gracefully."""
    try:
        from python.helpers.mode_manager import get_mode_manager as _get_mode_manager
        return _get_mode_manager()
    except ImportError:
        logger.warning("Mode manager not available")
        return None
    except Exception as e:
        logger.error(f"Error getting mode manager: {e}")
        return None


@mode_bp.route('/mode/list', methods=['GET', 'POST'])
def list_modes():
    """
    List all available modes.
    
    Returns:
        JSON with list of modes and their configurations.
    """
    manager = get_mode_manager()
    
    if not manager:
        return jsonify({
            "ok": False,
            "error": "Mode manager not available",
            "modes": []
        }), 503
    
    modes = []
    for mode in manager.list_modes():
        modes.append({
            "slug": mode.slug,
            "name": mode.name,
            "display_name": mode.display_name,
            "description": mode.description,
            "tool_groups": mode.tool_groups,
        })
    
    return jsonify({
        "ok": True,
        "modes": modes,
        "current_mode": manager.current_mode,
        "default_mode": manager.default_mode,
    })


@mode_bp.route('/mode/current', methods=['GET', 'POST'])
def get_current_mode():
    """
    Get the current mode.
    
    Returns:
        JSON with current mode information.
    """
    manager = get_mode_manager()
    
    if not manager:
        return jsonify({
            "ok": False,
            "error": "Mode manager not available",
            "mode": "code"
        }), 503
    
    mode_config = manager.current_mode_config
    
    if mode_config:
        return jsonify({
            "ok": True,
            "mode": {
                "slug": mode_config.slug,
                "name": mode_config.name,
                "display_name": mode_config.display_name,
                "description": mode_config.description,
                "tool_groups": mode_config.tool_groups,
                "role_definition": mode_config.role_definition,
                "custom_instructions": mode_config.custom_instructions,
            }
        })
    else:
        return jsonify({
            "ok": True,
            "mode": {
                "slug": manager.current_mode,
                "name": manager.current_mode,
                "display_name": manager.current_mode.title(),
                "description": "",
                "tool_groups": [],
            }
        })


@mode_bp.route('/mode/switch', methods=['POST'])
def switch_mode():
    """
    Switch to a different mode.
    
    Request body:
        {
            "mode": "code|architect|ask|debug|review",
            "force": false  // Optional: bypass transition rules
        }
    
    Returns:
        JSON with success status and new mode info.
    """
    manager = get_mode_manager()
    
    if not manager:
        return jsonify({
            "ok": False,
            "error": "Mode manager not available"
        }), 503
    
    data = request.get_json() or {}
    new_mode = data.get("mode")
    force = data.get("force", False)
    
    if not new_mode:
        return jsonify({
            "ok": False,
            "error": "Mode parameter is required"
        }), 400
    
    # Check if mode exists
    if new_mode not in manager.list_mode_slugs():
        return jsonify({
            "ok": False,
            "error": f"Unknown mode: {new_mode}",
            "available_modes": manager.list_mode_slugs()
        }), 400
    
    # Attempt to switch
    old_mode = manager.current_mode
    success = manager.switch_mode(new_mode, force=force)
    
    if success:
        mode_config = manager.current_mode_config
        return jsonify({
            "ok": True,
            "previous_mode": old_mode,
            "current_mode": manager.current_mode,
            "mode": {
                "slug": mode_config.slug,
                "name": mode_config.name,
                "display_name": mode_config.display_name,
                "description": mode_config.description,
                "tool_groups": mode_config.tool_groups,
            } if mode_config else None
        })
    else:
        return jsonify({
            "ok": False,
            "error": f"Cannot transition from {old_mode} to {new_mode}",
            "hint": "Use force=true to bypass transition rules"
        }), 400


@mode_bp.route('/mode/suggest', methods=['POST'])
def suggest_mode():
    """
    Suggest a mode based on task text.
    
    Request body:
        {
            "task": "Task description text"
        }
    
    Returns:
        JSON with suggested mode.
    """
    manager = get_mode_manager()
    
    if not manager:
        return jsonify({
            "ok": False,
            "error": "Mode manager not available",
            "suggested_mode": None
        }), 503
    
    data = request.get_json() or {}
    task_text = data.get("task", "")
    
    if not task_text:
        return jsonify({
            "ok": True,
            "suggested_mode": None,
            "reason": "No task text provided"
        })
    
    suggested = manager.suggest_mode_for_task(task_text)
    
    if suggested:
        mode_config = manager.get_mode(suggested)
        return jsonify({
            "ok": True,
            "suggested_mode": suggested,
            "mode": {
                "slug": mode_config.slug,
                "name": mode_config.name,
                "display_name": mode_config.display_name,
                "description": mode_config.description,
            } if mode_config else None,
            "reason": f"Task text matches patterns for {suggested} mode"
        })
    else:
        return jsonify({
            "ok": True,
            "suggested_mode": None,
            "reason": "No mode patterns matched the task text"
        })


@mode_bp.route('/mode/info/<mode_slug>', methods=['GET'])
def get_mode_info(mode_slug):
    """
    Get detailed information about a specific mode.
    
    Args:
        mode_slug: The mode slug (code, architect, ask, debug, review)
    
    Returns:
        JSON with mode configuration details.
    """
    manager = get_mode_manager()
    
    if not manager:
        return jsonify({
            "ok": False,
            "error": "Mode manager not available"
        }), 503
    
    mode_config = manager.get_mode(mode_slug)
    
    if not mode_config:
        return jsonify({
            "ok": False,
            "error": f"Unknown mode: {mode_slug}",
            "available_modes": manager.list_mode_slugs()
        }), 404
    
    # Get supervisor settings
    supervisor_settings = None
    if mode_config.supervisor_settings:
        supervisor_settings = {
            "max_iterations_without_progress": mode_config.supervisor_settings.max_iterations_without_progress,
            "max_consecutive_tool_failures": mode_config.supervisor_settings.max_consecutive_tool_failures,
            "response_loop_threshold": mode_config.supervisor_settings.response_loop_threshold,
            "context_warning_threshold": mode_config.supervisor_settings.context_warning_threshold,
        }
    
    # Get restrictions
    restrictions = None
    if mode_config.restrictions:
        restrictions = {
            "edit": {
                "file_patterns": mode_config.restrictions.get_allowed_file_patterns()
            } if mode_config.restrictions.edit else None
        }
    
    return jsonify({
        "ok": True,
        "mode": {
            "slug": mode_config.slug,
            "name": mode_config.name,
            "display_name": mode_config.display_name,
            "description": mode_config.description,
            "role_definition": mode_config.role_definition,
            "tool_groups": mode_config.tool_groups,
            "custom_instructions": mode_config.custom_instructions,
            "supervisor_settings": supervisor_settings,
            "restrictions": restrictions,
        }
    })


def register_mode_endpoints(app):
    """Register mode endpoints with the Flask app."""
    app.register_blueprint(mode_bp, url_prefix='/api')
    logger.info("Mode API endpoints registered")
