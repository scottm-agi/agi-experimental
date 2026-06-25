from __future__ import annotations
from abc import abstractmethod
from typing import Any
from python.helpers import extract_tools, files 
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from python.agent import Agent

class Extension:

    # Context-aware loading declarations.
    # Subclasses can override these to declare when they should fire.
    # None = no filter (fires for everything).
    PROFILES: set | None = None     # e.g. {"code"}, {"multiagentdev", "alex"}
    CATEGORIES: set | None = None   # e.g. {PhaseCategory.IMPLEMENTATION}
    TOOLS: set | None = None        # e.g. {"write_to_file", "replace_in_file"}

    def __init__(self, agent: "Agent|None", **kwargs):
        self.agent: "Agent" = agent # type: ignore < here we ignore the type check as there are currently no extensions without an agent
        self.kwargs = kwargs

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        pass


async def call_extensions(extension_point: str, agent: "Agent|None" = None, **kwargs) -> Any:

    # get default extensions
    defaults = await _get_extensions("python/extensions/" + extension_point)
    classes = defaults

    # get agent extensions
    if agent and agent.config.profile:
        agentics = await _get_extensions("agents/" + agent.config.profile + "/extensions/" + extension_point)
        if agentics:
            # merge them, agentics overwrite defaults
            unique = {}
            for cls in defaults + agentics:
                unique[_get_file_from_module(cls.__module__)] = cls

            # sort by name
            classes = sorted(unique.values(), key=lambda cls: _get_file_from_module(cls.__module__))

    # F-17d: Universal gate bypass for user stop directive.
    # When the user says "stop", NO gate can block the response tool.
    # This is the SINGLE chokepoint — every gate extension flows through here.
    # Nothing can override a user stop except another user message.
    # Covers both tool_execute_before (prevent blocking) and tool_execute_after
    # (prevent rejection/retry).
    if (
        extension_point in ("tool_execute_after", "tool_execute_before")
        and agent
        and agent.data.get("_user_stop_directive")
    ):
        # Check tool_name from kwargs (tool_execute_after) or from tool object (tool_execute_before)
        tool_name = kwargs.get("tool_name", "")
        if not tool_name:
            tool_obj = kwargs.get("tool")
            if tool_obj and hasattr(tool_obj, "name"):
                tool_name = tool_obj.name or ""
        if tool_name.lower() == "response":
            import logging
            logging.getLogger("agix.extension").info(
                f"[EXTENSION] F-17d: _user_stop_directive active + response tool — "
                f"SKIPPING ALL {extension_point} gates. Nothing blocks a user stop."
            )
            return None

    if (
        extension_point in ("tool_execute_after", "tool_execute_before")
        and agent
    ):
        tool_name = kwargs.get("tool_name", "")
        if not tool_name:
            tool_obj = kwargs.get("tool")
            if tool_obj and hasattr(tool_obj, "name"):
                tool_name = tool_obj.name or ""
        if tool_name.lower() == "response":
            if agent.data.get("_budget_expiring"):
                import logging
                logging.getLogger("agix.extension").warning(
                    f"[EXTENSION] P0-1 Universal Bypass: _budget_expiring active + "
                    f"response tool — SKIPPING ALL {extension_point} gates. "
                    f"Agent must deliver its response."
                )
                return None


    # call extensions — return first non-None result (enables blocking)
    # Context-aware: skip extensions whose PROFILES/CATEGORIES/TOOLS don't match
    tool_name_for_filter = kwargs.get("tool_name", "")
    if not tool_name_for_filter:
        tool_obj = kwargs.get("tool")
        if tool_obj and hasattr(tool_obj, "name"):
            tool_name_for_filter = tool_obj.name or ""

    for cls in classes:
        if not _should_load_extension(cls, agent, tool_name_for_filter):
            continue
        result = await cls(agent=agent).execute(**kwargs)
        if result is not None:
            return result
    return None


def _get_file_from_module(module_name: str) -> str:
    return module_name.split(".")[-1]

_cache: dict[str, list[type[Extension]]] = {}
async def _get_extensions(folder:str):
    global _cache
    folder_abs = files.get_abs_path(folder)
    if folder_abs in _cache:
        classes = _cache[folder_abs]
    else:
        if not files.exists(folder_abs):
            # print(f"[Extensions] Folder not found: {folder_abs}")
            return []
        classes = extract_tools.load_classes_from_folder(
            folder_abs, "*", Extension
        )
        # print(f"[Extensions] Loaded {len(classes)} from {folder_abs}: {[c.__name__ for c in classes]}")
        _cache[folder_abs] = classes

    return classes


def _should_load_extension(
    cls: type,
    agent: "Agent|None",
    tool_name: str = "",
) -> bool:
    """Check if an extension should fire based on its context declarations.

    Returns True if the extension's PROFILES, CATEGORIES, and TOOLS all
    match the current agent context. None means 'no filter' (always match).

    When agent is None or context data is missing, the filter is skipped
    (defaults to loading the extension).
    """
    # --- PROFILES filter ---
    profiles = getattr(cls, "PROFILES", None)
    if profiles is not None and agent is not None:
        agent_profile = getattr(getattr(agent, "config", None), "profile", "") or ""
        if agent_profile and agent_profile not in profiles:
            return False

    # --- CATEGORIES filter (uses phase_category.py) ---
    categories = getattr(cls, "CATEGORIES", None)
    if categories is not None and agent is not None:
        current_phase = agent.data.get("_current_phase") if hasattr(agent, "data") else None
        if current_phase is not None:
            try:
                from python.helpers.phase_category import get_phase_category
                phase_cat = get_phase_category(current_phase)
                if phase_cat is not None and phase_cat not in categories:
                    return False
            except ImportError:
                pass  # Can't filter by category if phase_category not available

    # --- TOOLS filter ---
    tools = getattr(cls, "TOOLS", None)
    if tools is not None and tool_name:
        if tool_name not in tools:
            return False

    return True
