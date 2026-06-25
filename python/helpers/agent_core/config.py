"""
Configuration dataclasses for agent_core package.

Contains AgentConfig, UserMessage, and LoopData dataclasses used
for agent configuration and message loop state management.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    import python.history as history
    from python.models import ModelConfig


# ==============================================================================
# AGENT CONFIGURATION
# ==============================================================================

@dataclass
class AgentConfig:
    """
    Configuration for an Agent instance.
    
    Contains model configurations, profile settings, and various
    operational parameters for the agent.
    """
    chat_model: "ModelConfig"
    utility_model: "ModelConfig"
    embeddings_model: "ModelConfig"
    browser_model: "ModelConfig"
    mcp_servers: str
    profile: str = ""
    memory_subdir: str = ""
    knowledge_subdirs: list[str] = field(default_factory=lambda: ["default", "custom"])
    browser_http_headers: dict[str, str] = field(default_factory=dict)  # Custom HTTP headers for browser requests
    code_exec_ssh_enabled: bool = True
    code_exec_ssh_addr: str = "localhost"
    code_exec_ssh_port: int = 55022
    code_exec_ssh_user: str = "root"
    code_exec_ssh_pass: str = ""
    skills: list[str] = field(default_factory=list)
    additional: Dict[str, Any] = field(default_factory=dict)


# ==============================================================================
# USER MESSAGE
# ==============================================================================

@dataclass
class UserMessage:
    """
    Represents a user message with optional attachments and system context.
    
    Used for both initial user prompts and intervention messages during
    agent execution.
    """
    message: str
    attachments: list[str] = field(default_factory=list)
    system_message: list[str] = field(default_factory=list)
    id: Optional[str] = None


# ==============================================================================
# LOOP DATA
# ==============================================================================

class LoopData:
    """
    State container for the agent's message loop.
    
    Holds iteration-specific data, history references, prompt components,
    and temporary/persistent parameters used during a single message loop
    execution.
    
    Attributes:
        iteration: Current iteration number within the message loop (-1 = not started)
        system: List of system prompt components
        user_message: The originating user message for this loop
        history_output: Rendered history messages for LLM context
        extras_temporary: Temporary extras cleared after each iteration
        extras_persistent: Persistent extras retained across iterations
        last_response: The agent's last response text
        params_temporary: Temporary parameters cleared after each iteration
        params_persistent: Persistent parameters retained across iterations
        current_tool: The tool currently being executed (if any)
        last_tool: Name of the last executed tool
        last_tool_result: Result from the last executed tool
        last_ai_message: Reference to the last AI message in history
    """
    
    def __init__(self, **kwargs):
        self.iteration = -1
        self.system: list = []
        self.user_message: Optional["history.Message"] = None
        self.history_output: list["history.OutputMessage"] = []
        self.extras_temporary: OrderedDict[str, "history.MessageContent"] = OrderedDict()
        self.extras_persistent: OrderedDict[str, "history.MessageContent"] = OrderedDict()
        self.last_response = ""
        self.params_temporary: dict = {}
        self.params_persistent: dict = {}
        self.current_tool = None
        self.last_tool: Optional[str] = None
        self.last_tool_result: Optional[str] = None
        self.last_ai_message: Optional["history.Message"] = None
        
        # Explicit loop control (Issue #696)
        self.is_done: bool = False
        self.stop_reason: Optional[str] = None
        
        # Truncation recovery tracking (Issue #1081)
        self.truncation_retries: int = 0
        self.last_successful_llm_ts: float = 0.0
        # In-flight work tracking (Gate 5, MSR_Smoke_1776891952)
        # Updated before LLM calls and tool execution to prevent
        # false-positive dead-agent detection during long operations.
        self.last_activity_ts: float = 0.0
        
        # Internal flags for recursion prevention
        self._has_condensed: bool = False

        # Override values with kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)