"""Response Truthfulness Check — verifies file paths in agent responses exist on disk.

Universal check that works for ANY project type. When the agent mentions file paths
in its response, this extension verifies they actually exist on disk. Flags fabricated
paths that could mislead the user or orchestrator.

Wired from: python/helpers/response_truthfulness.py
Architecture: message_loop_end hook — runs after each agent response.
"""

import logging
from python.helpers import files

logger = logging.getLogger("agix.ext.response_truthfulness")


async def execute(agent, **kwargs):
    """Check response truthfulness after each agent message."""
    try:
        # Get the most recent assistant message
        msgs = kwargs.get("msgs", [])
        if not msgs:
            return

        last_msg = msgs[-1] if msgs else None
        if not last_msg:
            return

        # Extract response text from the message
        response_text = ""
        if isinstance(last_msg, dict):
            response_text = last_msg.get("content", "")
        elif hasattr(last_msg, "content"):
            response_text = str(last_msg.content) if last_msg.content else ""

        if not response_text or len(response_text) < 20:
            return  # Too short to contain meaningful file paths

        # Get project directory — use canonical _active_project_dir key
        # FIX-001 (G-1): Previously used wrong dict (agent_data) and wrong
        # key ("project_dir") — both caused silent skip.
        # Canonical key is _active_project_dir (set by requirements.py).
        project_dir = ""
        data = getattr(agent, "data", {})
        if isinstance(data, dict):
            project_dir = data.get("_active_project_dir", "")

        if not project_dir:
            return

        # Run the truthfulness check
        from python.helpers.response_truthfulness import check_response_truthfulness
        result = check_response_truthfulness(response_text, project_dir)

        if not result.get("passed", True):
            fabricated = result.get("fabricated_paths", [])
            total = result.get("total_paths", 0)
            logger.warning(
                f"[RESPONSE TRUTHFULNESS] {len(fabricated)}/{total} paths "
                f"in response are fabricated: {', '.join(fabricated[:5])}"
            )
            # FIX-001 (G-1): Store signal on agent.data (canonical signal bus),
            # NOT agent.agent_data (wrong dict — signals were orphaned).
            if hasattr(agent, "data") and isinstance(agent.data, dict):
                agent.data["_response_truthfulness_failed"] = True
                agent.data["_fabricated_path_count"] = len(fabricated)

    except Exception as e:
        logger.debug(f"[RESPONSE TRUTHFULNESS] Extension error: {e}")
