"""
LiteLLM Shim to resolve compatibility issues with the openai library.
This module intercepts the initialization of OpenAI and AsyncOpenAI clients
to strip the 'proxies' argument, which is no longer supported in newer versions
of the openai library but is still sometimes passed by LiteLLM.

Also provides resilience hardening against known litellm 1.82.6 bugs:
- ValueError: task_done() called too many times (internal logging worker queue)
- Proxy argument incompatibility with newer openai SDK

IMPORTANT: litellm is pinned to 1.82.6 due to supply chain concerns in later versions.
"""
import logging
import threading
import litellm
import openai
from openai import OpenAI, AsyncOpenAI
from functools import wraps

logger = logging.getLogger(__name__)

# =============================================================================
# Resilience: Suppress litellm internal logging worker queue errors
# The ValueError "task_done() called too many times" comes from Python's
# threading.Queue.task_done() in litellm's internal callback processing thread.
# This is a litellm bug triggered under high concurrent load — non-fatal but noisy.
# =============================================================================

def _install_litellm_thread_exception_handler():
    """
    Install a threading excepthook that suppresses the known 'task_done()'
    ValueError from litellm's internal logging worker. All other exceptions
    are still logged normally.
    """
    _original_excepthook = getattr(threading, 'excepthook', None)

    def _litellm_resilient_excepthook(args):
        exc_type = args.exc_type
        exc_value = args.exc_value
        thread = args.thread

        # Suppress the known litellm queue bug
        if exc_type is ValueError and "task_done()" in str(exc_value):
            logger.debug(
                f"Suppressed litellm internal queue error in thread "
                f"'{thread.name if thread else 'unknown'}': {exc_value}"
            )
            return  # Swallow it

        # For all other thread exceptions, use the original handler
        if _original_excepthook:
            _original_excepthook(args)
        else:
            # Default behavior: print to stderr
            import traceback
            import sys
            traceback.print_exception(exc_type, exc_value, args.exc_traceback, file=sys.stderr)

    threading.excepthook = _litellm_resilient_excepthook
    logger.info("Shim: Installed litellm thread exception handler (suppresses task_done ValueError)")


# Install the handler immediately on module load
_install_litellm_thread_exception_handler()

# =============================================================================
# Resilience: Disable litellm internal logging to reduce queue contention
# =============================================================================

# Disable litellm's internal usage telemetry/analytics (reduces queue pressure)
litellm.telemetry = False
if hasattr(litellm, '_async_success_callback'):
    litellm._async_success_callback = []
if hasattr(litellm, '_async_failure_callback'):
    litellm._async_failure_callback = []

# =============================================================================
# OpenAI Client Monkey-Patch (proxies arg stripping)
# =============================================================================

def patch_openai_client(cls):
    """
    Monkey-patches the __init__ method of the given OpenAI client class
    to remove 'proxies' and 'proxy' from the keyword arguments.
    """
    original_init = cls.__init__
    
    @wraps(original_init)
    def patched_init(self, *args, **kwargs):
        if "proxies" in kwargs:
            logger.debug(f"Shim: Stripping 'proxies' from {cls.__name__} initialization")
            kwargs.pop("proxies")
        if "proxy" in kwargs:
            logger.debug(f"Shim: Stripping 'proxy' from {cls.__name__} initialization")
            kwargs.pop("proxy")
        return original_init(self, *args, **kwargs)
    
    cls.__init__ = patched_init

# Apply monkey-patches to OpenAI and AsyncOpenAI classes
try:
    patch_openai_client(OpenAI)
    patch_openai_client(AsyncOpenAI)
    logger.info("Shim: Successfully patched OpenAI and AsyncOpenAI client constructors.")
except Exception as e:
    logger.error(f"Shim: Failed to patch OpenAI clients: {e}")

# Clear global litellm proxy settings if any
if hasattr(litellm, "proxies"):
    litellm.proxies = None

# =============================================================================
# Wrap completion, acompletion, and embedding with resilience
# =============================================================================

_original_completion = litellm.completion
_original_acompletion = litellm.acompletion
_original_embedding = litellm.embedding

@wraps(_original_completion)
def completion(*args, **kwargs):
    kwargs.pop("proxies", None)
    kwargs.pop("proxy", None)
    try:
        return _original_completion(*args, **kwargs)
    except ValueError as e:
        if "task_done()" in str(e):
            logger.debug(f"Suppressed litellm task_done error in sync completion: {e}")
            # Re-call without the error — this was a side-effect, not a primary error
            return _original_completion(*args, **kwargs)
        raise

@wraps(_original_acompletion)
async def acompletion(*args, **kwargs):
    kwargs.pop("proxies", None)
    kwargs.pop("proxy", None)
    # Hard timeout via asyncio.wait_for — bypasses litellm bug #16394 where
    # the timeout kwarg is silently ignored for OpenRouter (aiohttp transport).
    # asyncio.wait_for is enforced by the Python event loop, not by litellm.
    #
    # SAFETY NET: If neither timeout kwarg nor litellm.request_timeout is set,
    # we enforce a 300s (5 min) ceiling. This prevents indefinite stalls when
    # OpenRouter drops the connection silently (observed in smoke test LP_1776618219).
    import asyncio
    _MAX_SAFETY_TIMEOUT = 300  # 5 minutes — absolute ceiling
    hard_timeout = kwargs.get("timeout", litellm.request_timeout) or _MAX_SAFETY_TIMEOUT
    try:
        return await asyncio.wait_for(
            _original_acompletion(*args, **kwargs),
            timeout=float(hard_timeout),
        )
    except asyncio.TimeoutError:
        model = kwargs.get("model", args[0] if args else "unknown")
        logger.error(
            f"[LLM_HARD_TIMEOUT] acompletion exceeded {hard_timeout}s "
            f"for model={model} — asyncio.wait_for killed the coroutine"
        )
        raise
    except ValueError as e:
        if "task_done()" in str(e):
            logger.debug(f"Suppressed litellm task_done error in async completion: {e}")
            return await _original_acompletion(*args, **kwargs)
        raise

@wraps(_original_embedding)
def embedding(*args, **kwargs):
    kwargs.pop("proxies", None)
    kwargs.pop("proxy", None)
    try:
        return _original_embedding(*args, **kwargs)
    except ValueError as e:
        if "task_done()" in str(e):
            logger.debug(f"Suppressed litellm task_done error in embedding: {e}")
            return _original_embedding(*args, **kwargs)
        raise

# Inject patched functions back into litellm just in case something calls them via litellm module
litellm.completion = completion
litellm.acompletion = acompletion
litellm.embedding = embedding

# Re-export common names and the patched functions
# This allows 'from python.helpers.litellm_shim import completion, acompletion, embedding'
__all__ = ["litellm", "completion", "acompletion", "embedding", "patch_openai_client"]

