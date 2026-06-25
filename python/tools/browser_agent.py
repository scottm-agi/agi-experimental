from __future__ import annotations
import asyncio
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass
import time
from typing import Optional, cast
from python.agent import Agent, InterventionException
from pathlib import Path

from python.helpers.tool import Tool, Response
from python.helpers import files, defer, persist_chat, strings
from python.helpers.browser_use import browser_use  # type: ignore[attr-defined]
from python.helpers.print_style import PrintStyle
from python.helpers import settings
import logging
logger = logging.getLogger(__name__)
from python.helpers.playwright import ensure_playwright_binary
from python.helpers.secrets_helper import get_secrets_manager
from python.extensions.message_loop_start._10_iteration_no import get_iter_no
from pydantic import BaseModel
import uuid
from python.helpers.dirty_json import DirtyJson
from langchain_core.messages import HumanMessage, SystemMessage
from contextvars import ContextVar
import sys

# Issue #1087: Hard timeout for page.goto() pre-navigation.
# browser-use wrappers may strip Playwright's timeout kwarg, causing
# page.goto() to hang forever when the target server is unresponsive.
NAVIGATION_TIMEOUT_SECONDS = 35

# --------------------------------------------------------------------------
# Safe monkeypatch for langchain-core 0.3.x Pydantic validation errors.
# We replace class references within browser_use modules with wrapper 
# functions to handle content conversion only when needed.
# --------------------------------------------------------------------------
browser_patch_enabled = ContextVar("browser_patch_enabled", default=True)

def _apply_browser_patch():
    # Define wrapper functions
    def SafeHumanMessage(content=None, **kwargs):
        # Always convert ContentPartTextParam objects to dicts
        if content and isinstance(content, list):
            new_content = []
            for part in content:
                if hasattr(part, "model_dump"):
                    new_content.append(part.model_dump())
                elif hasattr(part, "dict"):
                    new_content.append(part.dict())
                else:
                    new_content.append(part)
            content = new_content
        elif "content" in kwargs and isinstance(kwargs["content"], list):
            new_content = []
            for part in kwargs["content"]:
                if hasattr(part, "model_dump"):
                    new_content.append(part.model_dump())
                elif hasattr(part, "dict"):
                    new_content.append(part.dict())
                else:
                    new_content.append(part)
            kwargs["content"] = new_content
        return HumanMessage(content=content, **kwargs)

    def SafeSystemMessage(content=None, **kwargs):
        # Always convert ContentPartTextParam objects to dicts
        if content and isinstance(content, list):
            content = [(part.model_dump() if hasattr(part, "model_dump") else (part.dict() if hasattr(part, "dict") else part)) for part in content]
        elif "content" in kwargs and isinstance(kwargs["content"], list):
            kwargs["content"] = [(part.model_dump() if hasattr(part, "model_dump") else (part.dict() if hasattr(part, "dict") else part)) for part in kwargs["content"]]
        return SystemMessage(content=content, **kwargs)

    # List of browser_use modules that use these message classes
    # We explicitly import them to ensure they are in sys.modules
    target_modules = [
        'browser_use.agent.prompts',
        'browser_use.agent.message_manager.service',
        'browser_use.agent.message_manager.utils',
        'browser_use.agent.message_manager.views',
        'browser_use.agent.service'
    ]
    
    for mod_name in target_modules:
        try:
            __import__(mod_name)
        except ImportError:
            pass
            
    # List of common names for these classes across modules
    patch_targets = {
        'HumanMessage': HumanMessage,
        'SystemMessage': SystemMessage
    }
    
    # We iterate over all currently loaded modules to find any that have imported 
    # these classes from langchain_core.messages and replace them with our wrappers.
    for mod_name, mod in list(sys.modules.items()):
        if not mod_name.startswith('browser_use'):
            continue
            
        for attr_name, original_class in patch_targets.items():
            if hasattr(mod, attr_name):
                current_val = getattr(mod, attr_name)
                # If the module has an attribute with the name but it's the original class, patch it
                if current_val is original_class:
                    setattr(mod, attr_name, SafeHumanMessage if attr_name == 'HumanMessage' else SafeSystemMessage)

_apply_browser_patch()
# --------------------------------------------------------------------------


class State:
    @staticmethod
    async def create(agent: Agent):
        state = State(agent)
        return state

    def __init__(self, agent: Agent):
        self.agent = agent
        self.browser_session: Optional[browser_use.BrowserSession] = None
        self.task: Optional[defer.DeferredTask] = None
        self.use_agent: Optional[browser_use.Agent] = None
        self.secrets_dict: Optional[dict[str, str]] = None
        self.iter_no = 0
        self.guid = None

    def __del__(self):
        self.kill_task()
        files.delete_dir(self.get_user_data_dir()) # cleanup user data dir

    def get_user_data_dir(self):
        return str(
            Path.home()
            / ".config"
            / "browseruse"
            / "profiles"
            / f"agent_{self.agent.context.id}"
        )

    async def _initialize(self):
        if self.browser_session:
            return

        # for some reason we need to provide exact path to headless shell, otherwise it looks for headed browser
        pw_binary = ensure_playwright_binary()
                
        self.browser_session = browser_use.BrowserSession(
            browser_profile=browser_use.BrowserProfile(
                headless=True,
                disable_security=True,
                chromium_sandbox=False,
                accept_downloads=True,
                downloads_path=files.get_abs_path("tmp/downloads"),
                allowed_domains=["*", "http://*", "https://*"],
                executable_path=pw_binary,
                keep_alive=False,
                minimum_wait_page_load_time=1.0,
                wait_for_network_idle_page_load_time=2.0,
                maximum_wait_page_load_time=10.0,
                window_size={"width": 1024, "height": 2048},
                screen={"width": 1024, "height": 2048},
                viewport={"width": 1024, "height": 2048},
                no_viewport=False,
                args=["--headless=new", "--no-sandbox", "--disable-dev-shm-usage"],
                # Use a unique user data directory to avoid conflicts
                user_data_dir=self.get_user_data_dir(),
                extra_http_headers=self.agent.config.browser_http_headers or {},
                )
        )

        await self.browser_session.start() if self.browser_session else None
        # self.override_hooks()

        # --------------------------------------------------------------------------
        # Patch to enforce vertical viewport size
        # --------------------------------------------------------------------------
        # Browser-use auto-configuration overrides viewport settings, causing wrong
        # aspect ratio. We fix this by directly setting viewport size after startup.
        # --------------------------------------------------------------------------

        if self.browser_session:
            try:
                page = await self.browser_session.get_current_page()
                if page:
                    await page.set_viewport_size({"width": 1024, "height": 2048})
            except Exception as e:
                PrintStyle().warning(f"Could not force set viewport size: {e}")

        # --------------------------------------------------------------------------    
        
        # Add init script to the browser session
        # browser-use 0.11+ uses 'context' instead of 'browser_context'
        context = getattr(self.browser_session, "context", getattr(self.browser_session, "browser_context", None))
        if self.browser_session and context:
            js_override = files.get_abs_path("lib/browser/init_override.js")
            await context.add_init_script(path=js_override)

    def start_task(self, task: str, guid: Optional[str] = None, url: Optional[str] = None):
        self.guid = guid
        if self.task and self.task.is_alive():
            self.kill_task()

        self.task = defer.DeferredTask(
            thread_name="BrowserAgent" + (guid or self.agent.context.id)
        )
        if self.agent.context.task:
            self.agent.context.task.add_child_task(self.task, terminate_thread=True)
        self.task.start_task(self._run_task, task, url) if self.task else None
        return self.task

    def kill_task(self):
        if self.task:
            self.task.kill(terminate_thread=True)
            self.task = None
        if self.browser_session:
            try:
                import asyncio

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                # Fix Forgejo #1075: browser-use 0.11+ uses stop(), older uses close()
                # Fix: Wrap in wait_for to prevent deadlock when DOM watchdog is stuck.
                # Without this timeout, browser_session.stop() hangs forever if the
                # browser's event bus has a deadlocked handler, which blocks the
                # parent agent's entire message loop and freezes the system.
                TEARDOWN_TIMEOUT = 10  # seconds
                if hasattr(self.browser_session, 'stop') and callable(self.browser_session.stop):
                    loop.run_until_complete(
                        asyncio.wait_for(self.browser_session.stop(), timeout=TEARDOWN_TIMEOUT)
                    )
                elif hasattr(self.browser_session, 'close') and callable(self.browser_session.close):
                    loop.run_until_complete(
                        asyncio.wait_for(self.browser_session.close(), timeout=TEARDOWN_TIMEOUT)
                    )
                loop.close()
            except asyncio.TimeoutError:
                PrintStyle().warning(f"Browser session teardown timed out after {TEARDOWN_TIMEOUT}s — forcing cleanup")
                # ITR-31: Escalate to pkill when graceful teardown fails.
                # Zombie Chromium processes accumulate memory across delegations
                # and eventually OOM the container. pkill is the last resort.
                self._pkill_zombie_browsers()
            except Exception as e:
                PrintStyle().error(f"Error closing browser session: {e}")
            finally:
                self.browser_session = None
        self.use_agent = None
        self.iter_no = 0

    def _pkill_zombie_browsers(self):
        """ITR-31: Kill zombie browser processes after graceful teardown fails.

        When browser_session.stop()/close() times out, the Chromium process
        is likely deadlocked. Without cleanup, these zombie processes
        accumulate across multiple browser delegations and eventually
        exhaust container memory.

        Uses pkill -f to target headless chromium processes specifically.
        Logs the result for debugging but never raises — this is best-effort.
        """
        import subprocess
        try:
            result = subprocess.run(
                ["pkill", "-f", "chromium.*--headless"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                logger.warning(
                    "[BROWSER_AGENT] pkill escalation: killed zombie chromium processes"
                )
            else:
                logger.info(
                    "[BROWSER_AGENT] pkill escalation: no matching chromium processes found"
                )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            logger.warning(f"[BROWSER_AGENT] pkill escalation failed: {e}")

    async def _run_task(self, task: str, url: Optional[str] = None):
        await self._initialize()

        # --- Issue #779 Fix: Pre-navigate to target URL before LLM control ---
        # Without this, browser starts at DuckDuckGo and the LLM may ignore
        # the URL baked into the task text, causing non-deterministic failures.
        if url and self.browser_session:
            try:
                page = await self.browser_session.get_current_page()
                if page:
                    PrintStyle().info(f"🌐 [BROWSER_AGENT] Pre-navigating to: {url}")
                    # browser-use 0.11+ wraps Page objects and may not support
                    # the `wait_until` kwarg. Try with it first (native Playwright),
                    # then fall back to timeout-only (browser-use wrapper).
                    # Issue #1087: ALL goto() calls wrapped in asyncio.wait_for()
                    # to prevent infinite hangs when server is unresponsive.
                    try:
                        await asyncio.wait_for(page.goto(url, wait_until="domcontentloaded", timeout=30000), timeout=NAVIGATION_TIMEOUT_SECONDS)
                    except TypeError:
                        # browser-use wrapper doesn't support wait_until
                        try:
                            await asyncio.wait_for(page.goto(url, timeout=30000), timeout=NAVIGATION_TIMEOUT_SECONDS)
                        except TypeError:
                            # RC-17: browser-use wrapper doesn't support ANY kwargs
                            await asyncio.wait_for(page.goto(url), timeout=NAVIGATION_TIMEOUT_SECONDS)
                    PrintStyle().info(f"🌐 [BROWSER_AGENT] Pre-navigation complete")
            except Exception as e:
                PrintStyle().warning(f"🌐 [BROWSER_AGENT] Pre-navigation to {url} failed: {e} — LLM will attempt navigation via task text")
        # --- End Issue #779 Fix ---

        class DoneResult(BaseModel):
            title: str
            response: str
            page_summary: str

        # Initialize controller
        controller = browser_use.Controller(output_model=DoneResult)

        # Register custom completion action with proper ActionResult fields
        @controller.registry.action("Complete task", param_model=DoneResult)
        async def complete_task(params: DoneResult):
            result = browser_use.ActionResult(
                is_done=True, success=True, extracted_content=params.model_dump_json()
            )
            return result
        # Get browser model from agent
        try:
            model = self.agent.get_browser_model()
            
            # If it's a lazy wrapper, unwrap it for browser-use compatibility
            if hasattr(model, "_get_model"):
                model = model._get_model()
            
            # DEBUG START: Inspect model for browser-use compatibility
            from browser_use.llm.base import BaseChatModel
            import sys
            print(f"DEBUG: Browser Agent LLM Type: {type(model)}", file=sys.stderr)
            print(f"DEBUG: Browser Agent LLM is compliant with BaseChatModel Protocol: {isinstance(model, BaseChatModel)}", file=sys.stderr)
            print(f"DEBUG: Browser Agent LLM ainvoke method: {model.ainvoke}", file=sys.stderr)
            for attr in ["model", "provider", "name", "_verified_api_keys", "generate", "ainvoke"]:
                try:
                    val = getattr(model, attr, "MISSING")
                    print(f"DEBUG:   - {attr}: {val}", file=sys.stderr)
                except Exception as e:
                    print(f"DEBUG:   - {attr}: Error: {e}", file=sys.stderr)
            # DEBUG END

            secrets_manager = get_secrets_manager(self.agent.context)
            secrets_dict = secrets_manager.load_secrets()

            use_vision = getattr(model, "vision", self.agent.config.browser_model.vision)
        except Exception as e:
            raise Exception(f"Failed to resolve browser model: {e}") from e
            
        self.iter_no = get_iter_no(self.agent)

        async def hook(agent: browser_use.Agent):
            await self.agent.wait_if_paused()
            # Debug logging
            step_count = len(agent.history.model_outputs())
            current_url = "unknown"
            try:
                # Try to get URL from browser-use structures
                if hasattr(agent, 'browser_context') and agent.browser_context:
                    pages = await agent.browser_context.get_pages()
                    if pages:
                        current_url = pages[0].url
                elif hasattr(agent, 'browser_session') and agent.browser_session and hasattr(agent.browser_session, 'context'):
                     current_url = agent.browser_session.context.pages[0].url
            except Exception:
                pass
            PrintStyle().info(f"🔍 [BROWSER_AGENT] Step {step_count}: {current_url}")
            
            if self.iter_no != get_iter_no(self.agent):
                raise InterventionException("Task cancelled")

        try:
            # Set the patch enabled flag during both initialization and run
            token = browser_patch_enabled.set(True)
            
            # Initialize agent
            self.use_agent = browser_use.Agent(
                task=task,
                browser_session=self.browser_session,
                llm=model,
                use_vision=use_vision,
                extend_system_message=self.agent.read_prompt(
                    "prompts/browser_agent.system.md"
                ),
                controller=controller,
                enable_memory=False,  # Disable memory to avoid state conflicts
                llm_timeout=300, # Increased timeout to prevent premature aborts
                sensitive_data=cast(dict[str, str | dict[str, str]] | None, secrets_dict or {}),  # Pass secrets
            )

            if self.use_agent:
                current_settings = settings.get_settings()
                max_steps = current_settings.get("browser_agent_max_steps", 50)
                PrintStyle().info(f"🚀 [BROWSER_AGENT] Starting run for GUID {self.guid} with task: {task[:100]}... (max_steps={max_steps})")
                result = await self.use_agent.run(max_steps=max_steps, on_step_start=hook)
            return result

        except Exception as e:
            if isinstance(e, InterventionException):
                raise
            raise Exception(
                f"Browser agent execution failed. Error: {e}"
            ) from e
        finally:
            # Always reset the patch status
            browser_patch_enabled.reset(token)

    async def get_page(self):
        if self.use_agent and self.browser_session:
            try:
                return await self.use_agent.browser_session.get_current_page() if self.use_agent.browser_session else None
            except Exception:
                # Browser session might be closed or invalid
                return None
        return None

    async def get_selector_map(self):
        """Get the selector map for the current page state."""
        if self.use_agent:
            await self.use_agent.browser_session.get_state_summary(cache_clickable_elements_hashes=True) if self.use_agent.browser_session else None
            return await self.use_agent.browser_session.get_selector_map() if self.use_agent.browser_session else None
            await self.use_agent.browser_session.get_state_summary(
                cache_clickable_elements_hashes=True
            )
            return await self.use_agent.browser_session.get_selector_map()
        return {}


class BrowserAgent(Tool):
    """
    Use this tool to navigate to websites, perform research, extract data, and interact with web applications. 
    This is the preferred tool for all complex browser-based tasks. 
    DO NOT use code_execution_tool to write custom Playwright or Selenium scripts. 
    Always use browser_agent for robust, screenshot-enabled browsing.
    """
    description = "Use this tool to navigate to websites, perform research, extract data, and interact with web applications. This is the preferred tool for all complex browser-based tasks."
    instructions = "DO NOT use code_execution_tool to write custom Playwright or Selenium scripts. Always use browser_agent for robust, screenshot-enabled browsing. If the task requires multiple steps (scrolling, clicking, navigating), provide a clear, comprehensive message describing the desired flow."

    async def execute(self, message="", reset="", url="", **kwargs):
        self.guid = self.agent.context.generate_id() # short random id
        reset = str(reset).lower().strip() == "true"
        await self.prepare_state(reset=reset)
        message = get_secrets_manager(self.agent.context).mask_values(message, placeholder="<secret>{key}</secret>") # mask any potential passwords passed from Alex to browser-use to browser-use format
        # Issue #779: Extract url and pass to start_task for pre-navigation
        url_arg = str(url).strip() if url else ""
        # RC-13: Auto-extract URL from message text when url param not provided.
        # LLMs frequently embed the URL in the message rather than the url param.
        if not url_arg and message:
            import re
            url_match = re.search(r'https?://[^\s\'"<>]+', message)
            if url_match:
                url_arg = url_match.group(0).rstrip(".,;:)")
                PrintStyle().info(f"🌐 [BROWSER_AGENT] Auto-extracted URL from message: {url_arg}")

        # ── ITR-29e: Defense-in-depth curl pre-flight ─────────────────
        # Before launching the full browser (2-5 min), do a fast curl
        # check to verify the target is actually reachable. If it's
        # dead (000, 5xx, connection refused), fail fast with actionable
        # guidance instead of wasting browser steps.
        if url_arg and ("localhost" in url_arg or "0.0.0.0" in url_arg or "127.0.0.1" in url_arg):
            import subprocess
            try:
                curl_cmd = f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 5 {url_arg}"
                proc = subprocess.run(curl_cmd, shell=True, capture_output=True, text=True, timeout=8)
                http_code = proc.stdout.strip().strip("'")
                try:
                    code_int = int(http_code)
                except ValueError:
                    code_int = 0

                if code_int == 0 or code_int >= 500:
                    logger.warning(
                        f"[BROWSER_AGENT] Pre-flight curl FAILED: {url_arg} → HTTP {http_code}. "
                        f"Service is not running. Aborting browser launch."
                    )
                    return Response(
                        message=(
                            f"## ⛔ Browser Pre-flight FAILED — Service is DOWN\n\n"
                            f"Curl check: `{url_arg}` → HTTP {http_code}\n\n"
                            f"The dev server is not running or not responding. "
                            f"**Do NOT retry browser_agent** — the service must be started first.\n\n"
                            f"### Next steps:\n"
                            f"1. Use `services_mgt` action='start_service' to start the dev server\n"
                            f"2. Or report back to the orchestrator that the service is down\n"
                            f"3. Only use browser_agent AFTER confirming the service returns HTTP 200"
                        ),
                        break_loop=False,
                    )
                else:
                    logger.info(f"[BROWSER_AGENT] Pre-flight curl OK: {url_arg} → HTTP {http_code}")
            except (subprocess.TimeoutExpired, Exception) as e:
                logger.warning(f"[BROWSER_AGENT] Pre-flight curl error: {e} — proceeding anyway")
        # ── End ITR-29e ───────────────────────────────────────────────

        task = self.state.start_task(message, guid=self.guid, url=url_arg or None) if self.state else None

        # wait for browser agent to finish and update progress with timeout
        current_settings = settings.get_settings()
        timeout_seconds = current_settings.get("browser_agent_timeout_seconds", 300)
        start_time = time.time()
        _last_heartbeat = time.time()
        _HEARTBEAT_INTERVAL = 30  # seconds between heartbeat stamps

        # RCA-FIX: Set _blocked_in_tool to prevent idle timeout during browser execution.
        # browser_agent runs for 2-5+ minutes internally. Without this flag, the 120s
        # idle timer in subordinate_timeout.py fires during legitimate browser work,
        # killing E2E agents mid-test. This mirrors the pattern in code_execution.py.
        try:
            self.agent.data["_blocked_in_tool"] = {
                "tool": "browser_agent",
                "started": time.time(),
                "guid": self.guid,
            }
        except Exception:
            pass  # Never crash on flag set

        fail_counter = 0
        try:  # try/finally to guarantee _blocked_in_tool cleanup
          while not task.is_ready() if task else False:
            # Check for timeout to prevent infinite waiting
            if time.time() - start_time > timeout_seconds:
                PrintStyle().warning(
                    self._mask(f"Browser agent task timeout after {timeout_seconds} seconds, forcing completion")
                )
                break

            await self.agent.handle_intervention()
            await asyncio.sleep(1)

            # RCA-FIX: Stamp heartbeat periodically during the polling loop.
            # The idle timeout checks _last_tool_activity — without periodic
            # stamps, it thinks the agent is idle after 120s even though
            # browser_agent is actively running browser steps.
            now = time.time()
            if now - _last_heartbeat >= _HEARTBEAT_INTERVAL:
                try:
                    from python.helpers.subordinate_timeout import stamp_tool_activity_heartbeat
                    stamp_tool_activity_heartbeat(self.agent)
                    _last_heartbeat = now
                except Exception:
                    pass  # Heartbeat is best-effort

            try:
                if task and task.is_ready():  # otherwise get_update hangs
                    break
                try:
                    logger.debug(f"[BROWSER_AGENT] Requesting update for GUID {self.guid}...")
                    update = await asyncio.wait_for(self.get_update(), timeout=15)
                    fail_counter = 0  # reset on success
                except asyncio.TimeoutError:
                    fail_counter += 1
                    logger.warning(f"[BROWSER_AGENT] get_update timed out ({fail_counter}/3) for GUID {self.guid}")
                    PrintStyle().warning(
                        self._mask(f"browser_agent.get_update timed out ({fail_counter}/3)")
                    )
                    if fail_counter >= 3:
                        logger.error(f"[BROWSER_AGENT] 3 consecutive timeouts, aborting update loop for GUID {self.guid}")
                        PrintStyle().warning(
                            self._mask("3 consecutive browser_agent.get_update timeouts, breaking loop")
                        )
                        break
                    continue
                update_log = update.get("log", get_use_agent_log(None))
                self.update_progress("\n".join(update_log))
                screenshot = update.get("screenshot", None)
                if screenshot:
                    self.log.update(screenshot=screenshot)
            except Exception as e:
                PrintStyle().error(self._mask(f"Error getting update: {str(e)}"))

        finally:
          # RCA-FIX: Always clear _blocked_in_tool, even on timeout/error.
          # This ensures the idle timer resumes normal operation after browser_agent completes.
          try:
              self.agent.data["_blocked_in_tool"] = False
          except Exception:
              pass

        if task and not task.is_ready():
            PrintStyle().warning(self._mask("browser_agent.get_update timed out, killing the task"))
            self.state.kill_task() if self.state else None
            return Response(
                message=self._mask("Browser agent task timed out, not output provided."),
                break_loop=False,
            )

        # final progress update
        if self.state and self.state.use_agent:
            log_final = get_use_agent_log(self.state.use_agent)
            self.update_progress("\n".join(log_final))

        # collect result with error handling
        try:
            result = await task.result() if task else None
        except Exception as e:
            PrintStyle().error(self._mask(f"Error getting browser agent task result: {str(e)}"))
            # Return a timeout response if task.result() fails
            answer_text = self._mask(f"Browser agent task failed to return result: {str(e)}")
            self.log.update(answer=answer_text)
            return Response(message=answer_text, break_loop=False)
        # finally:
        #     # Stop any further browser access after task completion
        #     # self.state.kill_task()
        #     pass

        # Check if task completed successfully
        if result and result.is_done():
            answer = result.final_result()
            try:
                if answer and isinstance(answer, str) and answer.strip():
                    answer_data = DirtyJson.parse_string(answer)
                    answer_text = strings.dict_to_text(answer_data)  # type: ignore
                else:
                    answer_text = (
                        str(answer) if answer else "Task completed successfully"
                    )
            except Exception as e:
                answer_text = (
                    str(answer)
                    if answer
                    else f"Task completed with parse error: {str(e)}"
                )
        else:
            # Task hit max_steps without calling done()
            urls = result.urls() if result else []
            current_url = urls[-1] if urls else "unknown"
            
            # Get detailed action summary
            history_summary = _get_history_summary_text(result)
            
            answer_text = (
                f"Task reached step limit without completion. Last page: {current_url}.\n\n"
                f"### Diagnostic Summary of Steps:\n"
                f"{history_summary}\n\n"
                f"The browser agent may need clearer instructions on when to finish."
            )

        # Mask answer for logs and response
        answer_text = self._mask(answer_text)

        # update the log (without screenshot path here, user can click)
        self.log.update(answer=answer_text)

        # add screenshot to the answer if we have it
        if (
            self.log.kvps
            and "screenshot" in self.log.kvps
            and self.log.kvps["screenshot"]
        ):
            path = self.log.kvps["screenshot"].split("//", 1)[-1].split("&", 1)[0]
            answer_text += f"\n\nScreenshot: {path}"

            # ── Track screenshot paths for parent propagation (#1042) ──
            # Store actual screenshot file paths in agent.data so they can be
            # propagated to parent orchestrators via call_subordinate.
            # The completion gate uses this to verify screenshots actually exist.
            screenshots = self.agent.data.get("_browser_screenshots", [])
            screenshots.append(path)
            self.agent.data["_browser_screenshots"] = screenshots

        # ── Parse quality evaluation verdict from E2E response ──
        # The browser_agent system prompt instructs the E2E agent to include
        # "QUALITY: PASS" or "QUALITY: FAIL" in its response after UAT.
        # We parse this into structured data for propagation to the orchestrator.
        answer_upper = answer_text.upper()
        if "QUALITY: PASS" in answer_upper:
            self.agent.data["_quality_evaluation"] = {
                "passed": True,
                "source": "browser_agent_e2e",
                "response": answer_text,
            }
        elif "QUALITY: FAIL" in answer_upper:
            self.agent.data["_quality_evaluation"] = {
                "passed": False,
                "source": "browser_agent_e2e",
                "response": answer_text,
            }

        # cleanup state
        if self.state:
            self.state.kill_task()

        # respond (with screenshot path)
        return Response(message=answer_text, break_loop=False)

    def get_log_object(self):
        return self.agent.context.log.log(
            type="browser",
            heading=f"icon://captive_portal {self.agent.agent_name}: Calling Browser Agent",
            content="",
            kvps=self.args,
        )

    async def get_update(self):
        await self.prepare_state()

        result = {}
        agent = self.agent
        ua = self.state.use_agent if self.state else None
        page = await self.state.get_page() if self.state else None

        if ua and page:
            try:

                async def _get_update():
                    try:
                        logger.debug(f"[BROWSER_AGENT] Building log for {self.guid}")
                        # Build short activity log
                        result["log"] = get_use_agent_log(ua)

                        path = files.get_abs_path(
                            persist_chat.get_chat_folder_path(agent.context.id),
                            "browser",
                            "screenshots",
                            f"{self.guid}.png",
                        )
                        files.make_dirs(path)
                        
                        current_settings = settings.get_settings()
                        screenshot_timeout = current_settings.get("browser_agent_screenshot_timeout", 25000)
                        logger.debug(f"[BROWSER_AGENT] Capturing screenshot to {path} (timeout {screenshot_timeout}ms)")
                        # Use browser_use actor.Page API: screenshot(format='png') returns base64
                        try:
                            import base64
                            b64_data = await page.screenshot(format='png')
                            img_bytes = base64.b64decode(b64_data)
                            with open(path, "wb") as f:
                                f.write(img_bytes)
                            result["screenshot"] = f"img://{path}&t={str(time.time())}"
                            logger.debug(f"[BROWSER_AGENT] Screenshot captured successfully")
                        except asyncio.TimeoutError:
                            logger.warning(f"[BROWSER_AGENT] Screenshot timed out for {self.guid}")
                        except Exception as capture_error:
                            logger.error(f"[BROWSER_AGENT] Screenshot capture failed: {capture_error}")
                    except asyncio.TimeoutError:
                        logger.warning(f"[BROWSER_AGENT] Screenshot timed out for {self.guid}")
                    except Exception as e:
                        logger.error(f"[BROWSER_AGENT] Error in _get_update: {e}")

                if self.state and self.state.task and not self.state.task.is_ready():
                    await self.state.task.execute_inside(_get_update)

            except Exception as e:
                logger.error(f"[BROWSER_AGENT] Error in get_update outer: {e}")

        return result

    async def prepare_state(self, reset=False):
        self.state = self.agent.get_data("_browser_agent_state")
        if reset and self.state:
            self.state.kill_task()
        if not self.state or reset:
            self.state = await State.create(self.agent)
        self.agent.set_data("_browser_agent_state", self.state)

    def update_progress(self, text):
        text = self._mask(text)
        short = text.split("\n")[-1]
        if len(short) > 50:
            short = short[:50] + "..."
        progress = f"Browser: {short}"

        self.log.update(progress=text)
        self.agent.context.log.set_progress(progress)

    def _mask(self, text: str) -> str:
        try:
            return get_secrets_manager(self.agent.context).mask_values(text or "")
        except Exception as e:
            return text or ""

    # def __del__(self):
    #     if self.state:
    #         self.state.kill_task()


def _get_history_summary_text(result: Any) -> str:
    """Extract a human-readable summary of the last 5 actions from agent history."""
    if not result:
        return "No history available."
    
    try:
        action_results = result.action_results() or []
        if not action_results:
            return "No actions performed."
            
        summary = []
        # Take up to last 5 actions for brevity but context
        for i, item in enumerate(action_results[-5:]):
            step_no = len(action_results) - len(action_results[-5:]) + i + 1
            status = "✅" if item.success else "❌"
            
            content = ""
            if item.extracted_content:
                # Take first 100 chars of extracted content
                content = item.extracted_content.split("\n", 1)[0][:100]
                if len(item.extracted_content) > 100:
                    content += "..."
            
            err = f" (Error: {item.error})" if item.error else ""
            summary.append(f"{step_no}. {status} {content}{err}")
            
        return "\n".join(summary)
    except Exception as e:
        return f"Error gathering history summary: {str(e)}"


def get_use_agent_log(use_agent: browser_use.Agent | None):
    result = ["🚦 Starting task"]
    if use_agent:
        try:
            action_results = use_agent.history.action_results() or []
            model_actions = use_agent.history.model_actions() or []
            short_log = []
            for i, item in enumerate(action_results):
                action_desc = ""
                # Attempt to get the corresponding model action for context
                if i < len(model_actions):
                    action_data = model_actions[i]
                    if isinstance(action_data, dict):
                         for k, v in action_data.items():
                             action_desc = f"👉 Action: {k}"
                             if isinstance(v, dict) and v:
                                 # Format params concisely: key=val
                                 params = ", ".join([f"{pk}={pv}" for pk, pv in v.items()])
                                 action_desc += f" ({params})"
                             break
                
                if action_desc:
                    short_log.append(action_desc)

                # Error reporting for both final and intermediate steps
                if item.error:
                     short_log.append(f"❌ Error: {item.error}")

                # final results
                if item.is_done:
                    if item.success:
                        short_log.append("✅ Done")
                    elif not item.error: # Don't duplicate if already added
                        short_log.append(
                            f"❌ Error: {item.extracted_content or 'Unknown error'}"
                        )

                # progress messages (only if no error was already reported for this step)
                elif not item.error:
                    text = item.extracted_content
                    if text:
                        # Indent the result under the action
                        first_line = text.split("\n", 1)[0][:300]
                        short_log.append(f"  └ {first_line}")


            result.extend(short_log)
        except Exception:
            result.append("... rendering history")
    return result

