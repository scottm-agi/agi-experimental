"""
Service Context Bridge — propagates active service state across delegation.

Root Cause (LP_Smoke_1776731913 — RCA-2):
    Browser agents are delegated via call_subordinate_batch but never told which
    port the dev server is running on. They default to port 3000 (from package.json)
    while the actual service is on port 5139 (assigned by PortManager).

Architecture:
    call_subordinate.py already propagates _dev_server_port at lines 353-364
    (downward) and 492-503 (upward). This module extracts the pattern into
    reusable functions shared by BOTH call_subordinate.py and
    call_subordinate_batch.py.

Functions:
    propagate_service_context_down: parent → subordinate before monologue
    propagate_service_context_up:   subordinate → parent after monologue
    inject_service_context_message: adds port info for browser-profile tasks
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger("agix.service_bridge")

# Profiles that need service port information in their task message
BROWSER_PROFILES = {"browser", "e2e"}

# Keys propagated downward (parent → subordinate)
_DOWNWARD_KEYS = [
    ("_dev_server_started", False),
    ("_dev_server_port", ""),
    ("_services_mgt_dev_server", False),
]

# Keys propagated upward (subordinate → parent)
_UPWARD_BOOL_KEYS = [
    "_dev_server_started",
    "_services_mgt_dev_server",  # RCA-ITR32-C: specific services_mgt tool path flag
    "_verification_delegated",
    "_lit_tests_executed",
    "_quality_audit_done",
]
_UPWARD_VALUE_KEYS = [
    "_dev_server_port",
]
_UPWARD_LIST_KEYS = [
    "_code_execution_commands",
    "_browser_screenshots",
]
# Keys that should be SUMMED across subordinates (not replaced)
_UPWARD_SUM_KEYS = [
    "_browser_agent_calls",
]
# Keys that should be MERGED (dict/object) from subordinate to parent
# Only set on parent if parent doesn't already have a value
_UPWARD_DICT_KEYS = [
    "_quality_evaluation",
    "_server_health_evidence",  # RCA-233: health gate results for proof verification
]


def propagate_service_context_down(
    parent: "Agent",
    subordinate: "Agent",
) -> None:
    """Propagate dev server flags from parent → subordinate.
    
    Called BEFORE the subordinate monologue starts. Ensures the subordinate
    knows about any active dev server started by a sibling or the parent.
    
    Mirrors call_subordinate.py lines 353-364.
    """
    if not parent.data.get("_dev_server_started", False):
        return

    for key, default in _DOWNWARD_KEYS:
        subordinate.data[key] = parent.data.get(key, default)

    logger.info(
        f"[SERVICE BRIDGE ↓] Propagated service context to "
        f"{getattr(subordinate, 'agent_name', '?')} "
        f"(port={subordinate.data.get('_dev_server_port', '?')})"
    )


def propagate_service_context_up(
    subordinate: "Agent",
    parent: "Agent",
) -> None:
    """Propagate dev server flags from subordinate → parent.
    
    Called AFTER the subordinate monologue completes. Ensures the parent
    (and siblings delegated later) can see ports started by this subordinate.
    
    Mirrors call_subordinate.py lines 482-516.
    """
    # Bool keys: set True on parent if subordinate has them True
    for key in _UPWARD_BOOL_KEYS:
        if subordinate.data.get(key, False):
            parent.data[key] = True
            logger.info(
                f"[SERVICE BRIDGE ↑] Propagated {key}=True from "
                f"{getattr(subordinate, 'agent_name', '?')} to parent"
            )

    # Value keys: overwrite parent if subordinate has a truthy value
    for key in _UPWARD_VALUE_KEYS:
        val = subordinate.data.get(key, "")
        if val:
            parent.data[key] = val
            logger.info(
                f"[SERVICE BRIDGE ↑] Propagated {key}={val} from "
                f"{getattr(subordinate, 'agent_name', '?')} to parent"
            )

    # List keys: append subordinate's items to parent's list
    for key in _UPWARD_LIST_KEYS:
        sub_items = subordinate.data.get(key, [])
        if sub_items:
            parent_items = parent.data.get(key, [])
            parent.data[key] = parent_items + sub_items
            logger.info(
                f"[SERVICE BRIDGE ↑] Appended {len(sub_items)} {key} items from "
                f"{getattr(subordinate, 'agent_name', '?')} to parent "
                f"(total: {len(parent.data[key])})"
            )

    # Sum keys: accumulate numeric values across subordinates
    for key in _UPWARD_SUM_KEYS:
        sub_val = subordinate.data.get(key, 0)
        if sub_val:
            parent_val = parent.data.get(key, 0)
            parent.data[key] = parent_val + sub_val
            logger.info(
                f"[SERVICE BRIDGE ↑] Accumulated {key}: +{sub_val} from "
                f"{getattr(subordinate, 'agent_name', '?')} "
                f"(total: {parent.data[key]})"
            )

    # Dict keys: set on parent if subordinate has a value and parent doesn't
    for key in _UPWARD_DICT_KEYS:
        sub_val = subordinate.data.get(key)
        if sub_val and not parent.data.get(key):
            parent.data[key] = sub_val
            logger.info(
                f"[SERVICE BRIDGE ↑] Propagated {key} from "
                f"{getattr(subordinate, 'agent_name', '?')} to parent"
            )


def inject_service_context_message(
    task_message: str,
    parent: "Agent",
    task_profile: Optional[str],
) -> str:
    """Inject active service port info into task messages for browser-profile tasks.
    
    Root cause: Browser agents are told "test the app at localhost:3000" but
    the actual dev server is on a dynamic port (e.g., 5139). This injection
    tells them the real port.
    
    Only injects for browser/e2e profiles. Returns the message unchanged for
    other profiles or when no port is active.
    
    Args:
        task_message: The original task message
        parent: The parent agent (has _dev_server_port in data)
        task_profile: The profile of the subordinate task
    
    Returns:
        Task message with service context prepended, or original message
    """
    if not task_profile or task_profile.lower() not in BROWSER_PROFILES:
        return task_message

    port = parent.data.get("_dev_server_port", "")
    if not port:
        return task_message

    service_context = (
        f"## 🔌 Active Dev Server\n"
        f"A dev server is running on **port {port}**. "
        f"Use `http://localhost:{port}` for all browser testing.\n"
        f"Do NOT use port 3000 unless you have confirmed it is correct.\n\n---\n\n"
    )
    return service_context + task_message
