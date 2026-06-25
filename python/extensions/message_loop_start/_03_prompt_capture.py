"""
Capture the first user prompt text into agent.data['_original_user_prompt'].

RCA-312 Fix (F-3): The original implementation had single-shot capture with
no retry. `last_raw_user_message` was None on the first loop iteration, and
the history fallback silently failed because it treated Topic/Message objects
as dicts (using `.get()`) instead of attribute access. The `except Exception:
pass` swallowed the resulting AttributeErrors.

Fixes applied:
1. Guard checks `.strip()` so empty/whitespace-only strings don't block retry
2. Retry counter `_prompt_capture_attempts` stops after 5 attempts
3. History fallback handles both object-style (Topic.messages, Message.ai,
   Message.content) and dict-style access for robustness
4. `_detect_planning_only_mode()` called on successful capture
"""

import logging
from python.helpers.extension import Extension

logger = logging.getLogger("agix.prompt_capture")

MAX_CAPTURE_ATTEMPTS = 5
MIN_PROMPT_LENGTH = 50


class PromptCapture(Extension):
    """Captures the original user prompt into agent.data on first iteration."""

    async def execute(self, loop_data=None, **kwargs):
        # Only capture once — if already stored AND non-empty, skip
        existing = self.agent.data.get("_original_user_prompt", "")
        if isinstance(existing, str) and existing.strip():
            return

        # Check retry counter — don't keep trying forever
        attempts = self.agent.data.get("_prompt_capture_attempts", 0)
        if attempts >= MAX_CAPTURE_ATTEMPTS:
            return

        # Increment attempt counter
        self.agent.data["_prompt_capture_attempts"] = attempts + 1

        # Try agent.last_raw_user_message (set by hist_add_user_message)
        raw = getattr(self.agent, "last_raw_user_message", None)
        if raw and isinstance(raw, str) and len(raw) > MIN_PROMPT_LENGTH:
            self.agent.data["_original_user_prompt"] = raw
            self._detect_planning_only_mode(raw)
            self._detect_phase_cap(raw)
            logger.info(
                f"[PROMPT CAPTURE] Captured from last_raw_user_message "
                f"({len(raw)} chars, attempt {attempts + 1})"
            )
            return

        # Fallback: walk history to find first non-AI message
        # Handles BOTH object-style (Topic/Message classes) and dict-style access
        text = self._extract_from_history()
        if text and isinstance(text, str) and len(text) > MIN_PROMPT_LENGTH:
            self.agent.data["_original_user_prompt"] = text
            self._detect_planning_only_mode(text)
            self._detect_phase_cap(text)
            logger.info(
                f"[PROMPT CAPTURE] Captured from history fallback "
                f"({len(text)} chars, attempt {attempts + 1})"
            )
            return

        logger.debug(
            f"[PROMPT CAPTURE] No prompt found (attempt {attempts + 1}/{MAX_CAPTURE_ATTEMPTS})"
        )

    def _extract_from_history(self) -> str:
        """Extract first user message text from history.

        Handles both object-style access (history.topics[i].messages[j].ai)
        and dict-style access (topic.get("messages", [])) for robustness.

        Returns the first non-AI message text longer than MIN_PROMPT_LENGTH,
        or empty string if none found.
        """
        try:
            history = self.agent.history
            if not history:
                return ""

            # Get topics list — handle both attribute and dict access
            topics = []
            if hasattr(history, "topics"):
                topics = history.topics or []
            elif isinstance(history, dict):
                topics = history.get("topics", [])

            # Also check current topic
            current = getattr(history, "current", None)
            if current:
                topics_to_scan = list(topics) + [current]
            else:
                topics_to_scan = list(topics)

            for topic in topics_to_scan:
                text = self._extract_from_topic(topic)
                if text:
                    return text

        except Exception as e:
            logger.warning(f"[PROMPT CAPTURE] History extraction error: {e}")

        return ""

    def _extract_from_topic(self, topic) -> str:
        """Extract first user message text from a single topic.

        Supports both Topic objects (with .messages attribute) and dicts.
        """
        # Get messages list — handle both object and dict style
        msgs = []
        if hasattr(topic, "messages"):
            msgs = topic.messages or []
        elif isinstance(topic, dict):
            msgs = topic.get("messages", [])

        for msg in msgs:
            # Determine if this is an AI message — handle both styles
            is_ai = False
            if hasattr(msg, "ai"):
                is_ai = msg.ai
            elif isinstance(msg, dict):
                is_ai = msg.get("ai", False)

            if is_ai:
                continue

            # Extract content — handle both styles
            content = None
            if hasattr(msg, "content"):
                content = msg.content
            elif isinstance(msg, dict):
                content = msg.get("content", "")

            if content is None:
                continue

            text = self._extract_text_from_content(content)
            if isinstance(text, str) and len(text) > MIN_PROMPT_LENGTH:
                return text

        return ""

    def _extract_text_from_content(self, content) -> str:
        """Extract plain text from various content formats.

        Handles:
        - str: return as-is
        - dict with 'user_message' key
        - list of text parts (multimodal format)
        """
        if isinstance(content, str):
            return content
        elif isinstance(content, dict):
            # Try 'user_message' key first (template-processed format)
            if "user_message" in content:
                return content.get("user_message", "")
            # Try 'content' key (nested format)
            if "content" in content:
                inner = content["content"]
                if isinstance(inner, str):
                    return inner
            # Try 'text' key
            if "text" in content:
                return content.get("text", "")
        elif isinstance(content, list):
            # Multimodal format: list of {type: "text", text: "..."} parts
            parts = []
            for p in content:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(p.get("text", ""))
                elif isinstance(p, str):
                    parts.append(p)
            return " ".join(parts)

        return ""

    def _detect_planning_only_mode(self, prompt_text: str) -> None:
        """ISS-9 Fix: Detect planning-only mode from prompt text.

        Sets agent.data['_planning_only'] = True when the prompt contains
        PLANNING_ONLY or PLANNING PHASES ONLY keywords. This creates a
        structured system flag that gates (e.g., orchestrator_completion_gate)
        can read to skip implementation-only checks.

        Fix A (3-way bridge): Also writes {"planning_only": true} to
        project.json in the active project directory so that requirements.py
        (which reads from project.json, not agent.data) can also detect
        planning-only mode. This bridges the disconnect between:
          1. agent.data["_planning_only"] (set here)
          2. project.json planning_only (read by requirements.py)
          3. call_subordinate guard (reads agent.data)

        Root cause (RCA-ITR2-ISS9): The PLANNING_ONLY_PREFIX was a
        natural-language LLM instruction only — no system infrastructure
        knew the run was planning-only. Gates applied full implementation
        checks and caused 3 rejections.
        """
        # Check first 500 chars — the planning prefix is always at the start
        prefix = prompt_text[:500].upper()
        if "PLANNING_ONLY" in prefix or "PLANNING PHASES ONLY" in prefix:
            self.agent.data["_planning_only"] = True
            logger.info("[PROMPT CAPTURE] Detected planning-only mode from prompt prefix")

            # Fix A: Bridge to project.json for requirements.py
            self._bridge_planning_only_to_project_json()

    def _detect_phase_cap(self, prompt_text: str) -> None:
        """ITR-45 Fix: Detect phase cap from prompt text.

        Sets agent.data['_phase_cap'] when the prompt contains phase
        boundary directives like:
        - "Phase 0 through 3.5"
        - "through Phase 3 complete"
        - "STOP before Phase 4"

        Also bridges to project.json for cross-module access.

        Root cause (ITR-45 RCA-3): External pause API toggle failure
        allowed agents to run past intended Phase 3 boundary. Phase cap
        provides framework-level enforcement that doesn't depend on
        external pause calls.
        """
        from python.helpers.phase_cap import extract_phase_scope

        cap = extract_phase_scope(prompt_text)
        if cap is not None:
            # FIX-5: Max-preserve — never allow a subordinate's lower
            # phase context to regress the global cap.
            existing_cap = self.agent.data.get("_phase_cap")
            if existing_cap is not None:
                effective_cap = max(float(existing_cap), float(cap))
            else:
                effective_cap = float(cap)
            self.agent.data["_phase_cap"] = effective_cap
            logger.info(
                f"[PROMPT CAPTURE] Detected phase cap: {cap} "
                f"(from prompt text, effective={effective_cap}, "
                f"previous={existing_cap})"
            )

            # Bridge to project.json
            self._bridge_phase_cap_to_project_json(cap)

    def _bridge_phase_cap_to_project_json(self, phase_cap: float) -> None:
        """Write phase_cap to project.json in the active project dir."""
        import json
        import os

        project_dir = self.agent.data.get("_active_project_dir", "")
        if not project_dir:
            return

        project_json_path = os.path.join(project_dir, "project.json")
        try:
            existing_data = {}
            if os.path.isfile(project_json_path):
                with open(project_json_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)

            # FIX-5: Max-preserve — never regress phase_cap in project.json
            existing_json_cap = existing_data.get("phase_cap")
            if existing_json_cap is not None:
                effective_cap = max(float(existing_json_cap), float(phase_cap))
            else:
                effective_cap = float(phase_cap)
            existing_data["phase_cap"] = effective_cap

            os.makedirs(os.path.dirname(project_json_path) or ".", exist_ok=True)
            with open(project_json_path, "w", encoding="utf-8") as f:
                json.dump(existing_data, f, indent=2)

            logger.info(
                f"[PROMPT CAPTURE] Bridged phase_cap={phase_cap} to "
                f"{project_json_path}"
            )
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.warning(
                f"[PROMPT CAPTURE] Failed to bridge phase_cap to "
                f"project.json: {e}"
            )


    def _bridge_planning_only_to_project_json(self) -> None:
        """Write planning_only=True to project.json in the active project dir.

        Fix A: requirements.py reads planning_only from project.json (not
        agent.data). Without this bridge, the flag set in agent.data never
        reaches requirements.py, causing a 3-way disconnect.

        Merges with existing project.json content if present, or creates
        a new one with just the planning_only key.
        """
        import json
        import os

        project_dir = self.agent.data.get("_active_project_dir", "")
        if not project_dir:
            logger.debug(
                "[PROMPT CAPTURE] No _active_project_dir set — "
                "cannot bridge planning_only to project.json yet"
            )
            return

        project_json_path = os.path.join(project_dir, "project.json")
        try:
            # Read existing project.json if it exists
            existing_data = {}
            if os.path.isfile(project_json_path):
                with open(project_json_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)

            # Merge planning_only flag
            existing_data["planning_only"] = True

            # Ensure the directory exists
            os.makedirs(os.path.dirname(project_json_path) or ".", exist_ok=True)

            # Write back
            with open(project_json_path, "w", encoding="utf-8") as f:
                json.dump(existing_data, f, indent=2)

            logger.info(
                f"[PROMPT CAPTURE] Bridged planning_only=True to "
                f"{project_json_path}"
            )
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.warning(
                f"[PROMPT CAPTURE] Failed to bridge planning_only to "
                f"project.json: {e}"
            )

