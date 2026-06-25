"""
Supervisor Tools — Re-Export Hub.
=================================

This file composes ToolsMixin from sub-mixins and re-exports all
public names for backward compatibility.

Sub-modules:
  - tools_detection.py   (dead agent detection, context filtering)
  - tools_escalation.py  (escalation ramp, request_redirect)
  - tools_execution.py   (tool definitions, tracking, execution dispatch)
  - tools_actions.py     (individual tool action methods)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

# Re-export free functions and constants from detection module
from .tools_detection import (  # noqa: F401
    _filter_active_context_agents,
    detect_dead_agents,
    DEAD_AGENT_THRESHOLD_SECONDS,
)

# Import sub-mixins for composition
from .tools_escalation import EscalationMixin
from .tools_execution import ExecutionMixin
from .tools_actions import ActionsMixin


class ToolsMixin(EscalationMixin, ExecutionMixin, ActionsMixin):
    """
    Mixin class providing tool functionality for SupervisorAgent.

    Composed from:
      - EscalationMixin: escalation ramp + request_redirect
      - ExecutionMixin:  tool definitions, tracking, execution dispatch
      - ActionsMixin:    individual tool action implementations

    This mixin handles:
    - Tool execution
    - Intervention recording
    - Verification scheduling
    - Event bus publishing
    - Lesson recording
    """
    pass