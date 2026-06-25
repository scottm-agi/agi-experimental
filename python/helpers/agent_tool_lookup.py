"""
Agent tool lookup and stream handling — extracted from agent.py.

Contains the implementation for get_tool, handle_reasoning_stream,
and handle_response_stream. Delegated from Agent methods via the _impl pattern.
"""
import logging

from python.helpers import extract_tools, tool_registry
from python.helpers.print_style import PrintStyle
from python.helpers.dirty_json import DirtyJson

logger = logging.getLogger(__name__)


def get_tool_impl(
    agent, name: str, method=None, args=None, message: str = "", loop_data=None, **kwargs
):
    """Look up and instantiate a tool by name — delegated from Agent.get_tool()."""
    if args is None:
        args = {}
    from python.tools.unknown import Unknown
    from python.helpers.tool import Tool

    print(f"[DEBUG_AGENT] get_tool for: {name}")
    classes = []

    # try agent tools first
    if agent.config.profile:
        try:
            classes = extract_tools.load_classes_from_file(
                "agents/" + agent.config.profile + "/tools/" + name + ".py", Tool  # type: ignore[arg-type]
            )
        except FileNotFoundError:
            pass  # Expected — most tools don't have profile-specific overrides
        except Exception as e:
            # RCA-webhook-20260612: Log non-FileNotFoundError exceptions.
            # SyntaxError here means a profile-specific tool file is broken.
            logger.warning(f"[get_tool] Failed to load profile tool '{name}' from agents/{agent.config.profile}/tools/: {type(e).__name__}: {e}")

    # try default tools
    if not classes:
        try:
            classes = extract_tools.load_classes_from_file(
                "python/tools/" + name + ".py", Tool  # type: ignore[arg-type]
            )
        except FileNotFoundError:
            pass  # Expected — Unknown tool will handle this
        except Exception as e:
            # RCA-webhook-20260612: CRITICAL — a SyntaxError or ImportError here
            # means a tool file exists but is broken. Previously this was silently
            # swallowed (bare except: pass), causing the tool to fall through to
            # Unknown, returning "Tool not found", and triggering delegation loops.
            logger.error(f"[get_tool] CRITICAL: Failed to load tool '{name}' from python/tools/: {type(e).__name__}: {e}")

    # Track and notify on new tool creation/discovery
    if classes:
        is_new = tool_registry.register_tool(name)
        if is_new:
            try:
                # Log internally instead of sending UI notification to avoid recursion/deadlocks
                agent.log(
                    type="info",
                    heading=f"New Tool Created: {name}",
                    content=f"Agent has successfully instantiated a new tool: {name}",
                    verbose=True
                )
                PrintStyle(font_color="cyan", padding=True).print(f"New Tool Created: {name}")
            except Exception as e:
                logger.debug(f"[get_tool] Non-critical: failed to log new tool '{name}': {e}")

    tool_class = classes[0] if classes else Unknown
    return tool_class(
        agent=agent, name=name, method=method, args=args, message=message, loop_data=loop_data, **kwargs
    )


async def handle_reasoning_stream_impl(agent, stream: str):
    """Handle reasoning stream chunk — delegated from Agent.handle_reasoning_stream()."""
    await agent.handle_intervention()
    await agent.call_extensions(
        "reasoning_stream",
        loop_data=agent.loop_data,
        text=stream,
    )


async def handle_response_stream_impl(agent, stream: str):
    """Handle response stream chunk — delegated from Agent.handle_response_stream()."""
    await agent.handle_intervention()
    try:
        if len(stream) < 25:
            return  # no reason to try
        response = DirtyJson.parse_string(stream)
        if isinstance(response, dict):
            await agent.call_extensions(
                "response_stream",
                loop_data=agent.loop_data,
                text=stream,
                parsed=response,
            )

    except Exception as e:
        pass
