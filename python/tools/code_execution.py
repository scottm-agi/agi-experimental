from __future__ import annotations
import asyncio, os
from dataclasses import dataclass
import shlex
import time
from python.helpers.tool import Tool, Response
from python.helpers import files, rfc_exchange, projects, runtime
from python.helpers.print_style import PrintStyle
from python.helpers.shell_local import LocalInteractiveSession, LocalNonInteractiveSession
from python.helpers.shell_ssh import SSHInteractiveSession
from python.helpers.command_preprocessing import should_force_non_interactive
from python.helpers.docker import DockerContainerManager
from python.helpers.strings import truncate_text as truncate_text_string
from python.helpers.messages import truncate_text as truncate_text_agent
from python.helpers.output_truncation import truncate_output_middle_out, get_thresholds_for_command
from python.helpers.isolated_tool_executor import IsolatedToolExecutor
from python.helpers.secrets_helper import get_secrets_manager
import re

# Timeouts for python, nodejs, and terminal runtimes.
CODE_EXEC_TIMEOUTS: dict[str, int] = {
    "first_output_timeout": 30,    # Issue #1093: Increased back to 30 to prevent EOF on large heredoc writes
    "between_output_timeout": 20,   # ADR rca_terminal_blocking_stall: was 8s, too aggressive for npm resolution phases
    "max_exec_timeout": 120,    # Reduced from 180
    "dialog_timeout": 3,        # Reduced from 5
}

# Timeouts for output runtime.
OUTPUT_TIMEOUTS: dict[str, int] = {
    "first_output_timeout": 90,
    "between_output_timeout": 45,
    "max_exec_timeout": 300,
    "dialog_timeout": 5,
}

# Extended timeouts for long-running commands (npm install, pip, cargo, docker).
# 3min of total silence is reasonable to kill — but agents SHOULD be using
# runtime="output" to periodically tail/check-in on progress, not waiting blind.
LONG_RUNNING_TIMEOUTS: dict[str, int] = {
    "first_output_timeout": 120,       # 2min — npm sometimes takes a while to even start
    "between_output_timeout": 180,     # 3min — if truly zero output for 3min, kill it
    "max_exec_timeout": 1800,          # 30min hard cap — accommodates large monorepos
    "dialog_timeout": 10,
}

# Regex patterns for commands that are known to be long-running
_LONG_RUNNING_PATTERNS = [
    re.compile(r'\bnpm\s+(install|ci|i)\b'),
    re.compile(r'\bnpm\s+run\s+build\b'),  # MSR OOM fix: builds are long-running, not dev servers
    re.compile(r'\bnpx\s+'),
    re.compile(r'\bpip3?\s+install\b'),
    re.compile(r'\bcargo\s+(build|install|test)\b'),
    re.compile(r'\bdocker\s+build\b'),
    re.compile(r'\byarn\s+(install|add)\b'),
    re.compile(r'\bpnpm\s+(install|add)\b'),
]

# Patterns that are NOT long-running (exclude false positives)
_NOT_LONG_RUNNING_PATTERNS = [
    re.compile(r'\bnpm\s+(run|test|start|exec)\b'),
]


def _is_long_running_command(command: str | None) -> bool:
    """Detect if a command is known to be long-running (npm install, pip, cargo, etc.).
    
    These commands have legitimate silent periods during dependency resolution
    and should use extended timeouts to prevent false timeout kills.
    
    Returns True if command matches a long-running pattern.
    """
    if not command:
        return False
    
    # Check exclusions first
    for pat in _NOT_LONG_RUNNING_PATTERNS:
        if pat.search(command):
            # But check if there's also an install in a compound command
            # e.g., "npm run build && npm install" should still be detected
            has_install = any(p.search(command) for p in _LONG_RUNNING_PATTERNS)
            if not has_install:
                return False
    
    # Check for long-running patterns
    for pat in _LONG_RUNNING_PATTERNS:
        if pat.search(command):
            return True
    
    return False


def _get_timeouts_for_command(command: str | None) -> dict[str, int]:
    """Get appropriate timeouts for a command based on whether it's long-running.
    
    Long-running commands (npm install, pip, cargo) get LONG_RUNNING_TIMEOUTS
    to prevent false timeout kills during dependency resolution.
    Regular commands get CODE_EXEC_TIMEOUTS.
    
    Returns timeout dict.
    """
    if _is_long_running_command(command):
        return dict(LONG_RUNNING_TIMEOUTS)  # Copy to prevent mutation
    return dict(CODE_EXEC_TIMEOUTS)


# ── MSR OOM Fix: NODE_OPTIONS memory cap injection ──
# Prevents uncapped V8 heap from OOM-killing the Docker container.
# Each Node.js build process is hard-capped at NODE_HEAP_CAP_MB.
# If the build exceeds this, Node throws a graceful JS error instead
# of consuming all system RAM and triggering the kernel OOM killer.
# 4096MB (4GB): Node.js defaults to ~1.4-2GB on 64-bit; medium Next.js/Vite
# builds commonly peak at 2-4GB. 1GB is too low (below V8 default).
# 4GB keeps a single build to 25% of the 16GB dev container (32GB prod).
NODE_HEAP_CAP_MB = 4096

_NODE_BUILD_PATTERNS = [
    re.compile(r'\bnpm\s+run\s+build\b'),
    re.compile(r'\bnpx\s+'),
    re.compile(r'\bnext\s+build\b'),
    re.compile(r'\bvite\s+build\b'),
    re.compile(r'\btsc\b'),
    re.compile(r'\bwebpack\b'),
]


def _inject_node_memory_cap(command: str, cap_mb: int = NODE_HEAP_CAP_MB) -> str:
    """Inject NODE_OPTIONS memory cap for Node.js build commands.

    Prevents V8 heap from growing unbounded and OOM-killing the container.
    If NODE_OPTIONS is already present in the command, the user's setting
    is preserved (no override).

    Args:
        command: Shell command to potentially wrap.
        cap_mb: Max V8 heap size in megabytes (default: 1024).

    Returns:
        Command with NODE_OPTIONS prefix if applicable, otherwise unchanged.
    """
    if not command:
        return command
    if 'NODE_OPTIONS' in command:  # Don't override explicit setting
        return command
    for pat in _NODE_BUILD_PATTERNS:
        if pat.search(command):
            return f'NODE_OPTIONS="--max-old-space-size={cap_mb}" {command}'
    return command


# ── RCA-238 + RCA-ITR49: Command preprocessing for non-interactive execution ──
# Handles: npx -y injection, CI=true for scaffolds, per-tool flag injection.
from python.helpers.command_preprocessing import preprocess_command as _preprocess_command

@dataclass
class ShellWrap:
    id: int
    session: LocalInteractiveSession | LocalNonInteractiveSession | SSHInteractiveSession
    running: bool
    loop_id: int # ID of the event loop where this session was created

@dataclass
class State:
    ssh_enabled: bool
    shells: dict[int, ShellWrap]


class CodeExecution(Tool):

    # Common shell prompt regex patterns (add more as needed)
    prompt_patterns = [
        re.compile(r"\\(venv\\).+[$#] ?$"),  # (venv) ...$ or (venv) ...#
        re.compile(r"root@[^:]+:[^#]+# ?$"),  # root@container:~#
        re.compile(r"[a-zA-Z0-9_.-]+@[^:]+:[^$#]+[$#] ?$"),  # user@host:~$
        re.compile(r"\(?.*\)?\s*PS\s+[^>]+> ?$"),  # PowerShell prompt like (base) PS C:\...>
    ]
    # potential dialog detection
    dialog_patterns = [
        re.compile(r"Y/N", re.IGNORECASE),  # Y/N anywhere in line
        re.compile(r"yes/no", re.IGNORECASE),  # yes/no anywhere in line
        re.compile(r":\s*$"),  # line ending with colon
        re.compile(r"\?\s*$"),  # line ending with question mark
    ]

    async def execute(self, **kwargs) -> Response:

        await self.agent.handle_intervention()  # wait for intervention and handle it, if paused

        runtime = self.args.get("runtime", "").lower().strip()
        session = int(self.args.get("session", 0))
        self.allow_running = bool(self.args.get("allow_running", False))
        # ADR-82: Dual-mode shell — default non-interactive
        self._interactive = bool(self.args.get("interactive", False))
        
        # Guard for missing 'code' argument - required for python/nodejs/terminal
        code = self.args.get("code", "")
        if not code and runtime in ("python", "nodejs", "terminal"):
            return Response(
                message=f"Error: Missing required 'code' argument for runtime '{runtime}'. "
                        f"Please provide the code/command to execute.",
                break_loop=False
            )

        # Expand secret placeholders in code if present
        if code and "§§secret(" in code:
            secrets_mgr = get_secrets_manager()
            code = secrets_mgr.replace_placeholders(code)

        if runtime == "python":
            response = await self.execute_python_code(
                code=code, session=session
            )
        elif runtime == "nodejs":
            response = await self.execute_nodejs_code(
                code=code, session=session
            )
        elif runtime == "terminal":
            # ── Heredoc Guard: Reject large file creation via heredoc ──
            # Agents should use write_to_file for file creation, not cat <<EOF.
            # Heredoc puts entire file content in model output tokens, causing
            # truncation on large files. Small heredocs (<50 lines) are OK.
            heredoc_rejection = self._guard_heredoc_file_creation(code)
            if heredoc_rejection:
                return Response(message=heredoc_rejection, break_loop=False)
            response = await self.execute_terminal_command(
                command=code, session=session
            )
        elif runtime == "output":
            response = await self.get_terminal_output(
                session=session, timeouts=OUTPUT_TIMEOUTS
            )
        elif runtime == "reset":
            response = await self.reset_terminal(session=session)
        elif runtime == "full_output":
            # RCA-365 F-13: Recovery path for truncated output.
            # When truncation eats important error details, agents can use
            # runtime='full_output' to retrieve the last un-truncated output
            # saved by the truncation system to /tmp/last_cmd_output.log.
            try:
                log_path = "/tmp/last_cmd_output.log"
                if os.path.isfile(log_path):
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        response = f.read()
                    if not response:
                        response = "Full output log is empty — no truncated output has been saved yet."
                else:
                    response = (
                        "No full output log found at /tmp/last_cmd_output.log. "
                        "This file is created when output truncation occurs. "
                        "Try running the command again first."
                    )
            except Exception as e:
                response = f"Error reading full output log: {e}"
        else:
            response = self.agent.read_prompt(
                "fw.code.runtime_wrong.md", runtime=runtime
            )

        if not response:
            response = self.agent.read_prompt(
                "fw.code.info.md", info=self.agent.read_prompt("fw.code.no_output.md")
            )
        # RCA-330: Unwrap if response is already a Response object.
        # execute_terminal_command() guards (lines 348, 354) return Response objects,
        # but this line wraps in another Response — causing nested Response(message=Response(...)).
        # Fix: extract the string message before wrapping.
        from python.helpers.agent_process_tools import _ensure_string_message
        response = _ensure_string_message(response)
        return Response(message=response, break_loop=False)

    def get_log_object(self):
        return self.agent.context.log.log(
            type="code_exe",
            heading=self.get_heading(),
            content="",
            kvps=self.args,
        )

    def get_heading(self, text: str = ""):
        if not text:
            text = f"{self.name} - {self.args['runtime'] if 'runtime' in self.args else 'unknown'}"
        # text = truncate_text_string(text, 60) # don't truncate here, log.py takes care of it
        session = self.args.get("session", None)
        session_text = f"[{session}] " if session or session == 0 else ""
        return f"icon://terminal {self.agent.agent_name} {session_text}{text}"

    async def after_execution(self, response, **kwargs):
        await self.agent.hist_add_tool_result(self.name, response.message, **(response.additional or {}))

        # ITR-32 Fix 5 (RC-B): Wire BuildLoopDetector into build command results.
        # If the last executed command was a build command, route the output
        # through the detector. On loop detection, append diagnostic to response.
        # At Tier 2+ (3 failures), escalate to L2 intelligent supervisor via
        # _escape_hatch → _attempt_supervisor_redirect (same path as same-message loops).
        try:
            command = getattr(self, '_current_command', None)
            if command:
                from python.helpers.build_loop_detector import (
                    is_build_command,
                    get_or_create_build_loop_detector,
                )
                if is_build_command(command):
                    project_dir = self.get_cwd() or ""
                    if project_dir:
                        detector = get_or_create_build_loop_detector(self.agent)
                        diagnostic = detector.record_failure_from_output(
                            project_dir, response.message or "", exit_code=0
                        )
                        if diagnostic:
                            response.message = (response.message or "") + "\n\n" + diagnostic
                            # Wire to L2 supervisor at Tier 2+ (3 failures)
                            tier = detector.get_escalation_tier(project_dir)
                            count = detector.get_failure_count(project_dir)
                            if tier >= 2:
                                self.agent.data["_escape_hatch"] = {
                                    "type": "build_loop",
                                    "reason": (
                                        f"Build loop detected: {count} consecutive failures "
                                        f"in {project_dir}. Tier {tier} escalation."
                                    ),
                                    "repeat_count": count,
                                    "project_dir": project_dir,
                                    "diagnostic": truncate_output_middle_out(diagnostic, max_chars=500, head_ratio=0.3),
                                }
                                import logging
                                logging.getLogger("agix.build_loop_detector").warning(
                                    f"BuildLoopDetector Tier {tier}: Setting _escape_hatch "
                                    f"for L2 supervisor redirect ({count} failures)"
                                )
        except Exception as e:
            import logging
            logging.getLogger("agix.build_loop_detector").warning(
                f"BuildLoopDetector after_execution failed (non-fatal): {e}"
            )

    async def prepare_state(self, reset=False, session: int | None = None):
        self.state: State | None = self.agent.get_data("_cet_state")
        # always reset state when ssh_enabled changes
        if not self.state or self.state.ssh_enabled != self.agent.config.code_exec_ssh_enabled:
            # initialize shells dictionary if not exists
            shells: dict[int, ShellWrap] = {}
        else:
            shells = self.state.shells.copy()

        # Check for event loop mismatch - scheduled tasks run in new threads with new loops
        # If we reuse a session from a different loop, asyncio primitives will fail
        current_loop_id = id(asyncio.get_running_loop())
        invalid_sessions = []
        for sid, wrap in shells.items():
            # If loop_id is missing (backward compat) or different, it's invalid
            if not hasattr(wrap, 'loop_id') or wrap.loop_id != current_loop_id:
                invalid_sessions.append(sid)
        
        if invalid_sessions:
            PrintStyle.warning(f"Dropping {len(invalid_sessions)} terminal sessions due to event loop change.")
            for sid in invalid_sessions:
                # We cannot safely close sessions from another loop (calling close() triggers loop checks), just drop them
                del shells[sid]
            # Update state with removed shells immediately
            self.state = State(shells=shells, ssh_enabled=self.agent.config.code_exec_ssh_enabled)
            self.agent.set_data("_cet_state", self.state)

        # Only reset the specified session if provided
        if reset and session is not None and session in shells:
            await shells[session].session.close()
            del shells[session]
        elif reset and not session:
            # Close all sessions if full reset requested
            for s in list(shells.keys()):
                await shells[s].session.close()
            shells = {}

        # ADR-82: Dual-mode shell — route to interactive or non-interactive session
        if session is not None and session not in shells:
            if self.agent.config.code_exec_ssh_enabled:
                pswd = (
                    self.agent.config.code_exec_ssh_pass
                    if self.agent.config.code_exec_ssh_pass
                    else await rfc_exchange.get_root_password()
                )
                shell = SSHInteractiveSession(
                    self.agent.context.log,
                    self.agent.config.code_exec_ssh_addr,
                    self.agent.config.code_exec_ssh_port,
                    self.agent.config.code_exec_ssh_user,
                    pswd,
                    cwd=self.get_cwd(),
                )
            elif getattr(self, '_interactive', False):
                # Agent explicitly requested interactive mode
                shell = LocalInteractiveSession(cwd=self.get_cwd())
                PrintStyle.info(f"Session {session}: interactive mode (PTY with stdin)")
            else:
                # DEFAULT: Non-interactive — stdin is /dev/null (ADR-82)
                shell = LocalNonInteractiveSession(cwd=self.get_cwd())
                PrintStyle.info(f"Session {session}: non-interactive mode (stdin closed)")

            shells[session] = ShellWrap(
                id=session, 
                session=shell, 
                running=False,
                loop_id=id(asyncio.get_running_loop())
            )
            await shell.connect()

        self.state = State(shells=shells, ssh_enabled=self.agent.config.code_exec_ssh_enabled)
        self.agent.set_data("_cet_state", self.state)
        return self.state

    async def execute_python_code(self, session: int, code: str, reset: bool = False):
        escaped_code = shlex.quote(code)
        command = f"python3 -c {escaped_code}"
        prefix = ("bash>" if not runtime.is_windows() or self.agent.config.code_exec_ssh_enabled else "PS>") + self.format_command_for_output(command) + "\n\n"
        return await self.terminal_session(session, command, reset, prefix)

    async def execute_nodejs_code(self, session: int, code: str, reset: bool = False):
        escaped_code = shlex.quote(code)
        command = f"node /exe/node_eval.js {escaped_code}"
        prefix = "node> " + self.format_command_for_output(code) + "\n\n"
        return await self.terminal_session(session, command, reset, prefix)

    async def execute_terminal_command(
        self, session: int, command: str, reset: bool = False
    ):
        # RCA-365 F-2a: Store current command for command-aware truncation
        self._current_command = command
        # P2: Intercept dev server start commands and redirect to services_mgt.
        # Agents MUST use services_mgt for dev servers (port allocation, host
        # binding, health checks, state tracking). Direct execution via
        # code_execution_tool causes the server to bind to localhost:3000 which
        # is inaccessible from the host in Docker environments.
        from python.helpers.dev_server_guard import guard_dev_server_command
        dev_server_redirect = guard_dev_server_command(command)
        if dev_server_redirect:
            return Response(message=dev_server_redirect, break_loop=False)

        # P0: Block destructive commands targeting orchestrator state files
        # RCA-232 Fix 3: requirements_ledger.json, .agix.proj/, etc.
        from python.helpers.protected_paths_guard import guard_protected_paths
        protected_block = guard_protected_paths(command)
        if protected_block:
            return Response(message=protected_block, break_loop=False)

        # P0: Block destructive commands (rm -rf on project dirs, find -delete)
        # RCA ITR-35: agents executed rm -rf tmp/ 26 times destroying workspace
        from python.helpers.destructive_command_guard import is_destructive_command
        if is_destructive_command(command):
            return Response(
                message=(
                    "❌ BLOCKED: Destructive command detected. "
                    "Do NOT use rm -rf or find -delete on project directories. "
                    "Allowed exceptions: rm -rf node_modules/.cache, rm -rf .next/cache, "
                    "rm -rf dist/, rm -rf build/, rm -rf coverage/.\n\n"
                    "To delete specific files, use targeted rm commands without -r flag, "
                    "or use write_to_file/replace_in_file to modify file contents."
                ),
                break_loop=False,
            )

        # P0: Block rm -rf on build dirs (.next, dist, build, out) while dev server is active
        from python.helpers.build_health_guard import guard_service_aware_cleanup
        block_msg = guard_service_aware_cleanup(command, self.get_cwd())
        if block_msg:
            return Response(message=block_msg, break_loop=False)

        # P0: Intercept destructive node_modules removal without reinstall
        from python.helpers.build_health_guard import guard_destructive_cleanup
        command = guard_destructive_cleanup(command) or command

        # MSR OOM Fix: Cap Node.js V8 heap to prevent container OOM kills.
        # Applied BEFORE any timeout/mutex logic so the cap is always active.
        command = _inject_node_memory_cap(command)

        # RCA-238 + RCA-ITR49: Preprocess command for non-interactive execution.
        # Injects npx -y, CI=true for scaffolds, and per-tool flags.
        command = _preprocess_command(command)

        # P0: Register file reads from terminal commands (cat, head, tail, etc.)
        # with the READ-BEFORE-WRITE guard so agents aren't blocked when they
        # subsequently try to write to files they've already read via terminal.
        try:
            from python.helpers.read_before_write_guard import record_terminal_reads
            agent_id = getattr(self.agent, 'agent_name', str(id(self.agent)))
            record_terminal_reads(agent_id, command)
        except Exception:
            pass  # Non-critical — don't block command execution

        # P1 Fix 3: Serialize npm install operations per-project to prevent
        # ENOTEMPTY race conditions when parallel subordinates both install
        # in the same project directory.
        try:
            from python.helpers.npm_mutex import is_npm_install_command, NpmMutex
            cwd = self.get_cwd()
            if cwd and is_npm_install_command(command):
                import logging as _logging
                _logging.getLogger("agix.npm_mutex").info(
                    f"Acquiring npm mutex for {cwd} (command: {command[:80]})"
                )
                with NpmMutex(cwd, timeout=60.0):
                    prefix = ("bash>" if not runtime.is_windows() or self.agent.config.code_exec_ssh_enabled else "PS>") + self.format_command_for_output(command) + "\n\n"
                    # ADR rca_terminal_blocking_stall: Auto-detect long-running commands
                    timeouts = _get_timeouts_for_command(command)
                    return await self.terminal_session(session, command, reset, prefix, timeouts=timeouts)
        except TimeoutError:
            import logging as _logging
            _logging.getLogger("agix.npm_mutex").warning(f"npm mutex timeout for {cwd}, proceeding without lock")
        except Exception as e:
            import logging as _logging
            _logging.getLogger("agix.npm_mutex").warning(f"npm mutex failed, proceeding without lock: {e}")

        prefix = ("bash>" if not runtime.is_windows() or self.agent.config.code_exec_ssh_enabled else "PS>") + self.format_command_for_output(command) + "\n\n"
        # ADR rca_terminal_blocking_stall: Auto-detect long-running commands
        timeouts = _get_timeouts_for_command(command)
        return await self.terminal_session(session, command, reset, prefix, timeouts=timeouts)

    async def terminal_session(
        self, session: int, command: str, reset: bool = False, prefix: str = "", timeouts: dict | None = None
    ):

        self.state = await self.prepare_state(reset=reset, session=session)

        await self.agent.handle_intervention()  # wait for intervention and handle it, if paused

        # Check if session is running and handle it
        if not self.allow_running:
            if response := await self.handle_running_session(session):
                return response
        
        # try again on lost connection
        for i in range(2):
            try:


                executor = IsolatedToolExecutor(self.get_cwd())
                command = await executor.wrap_command(command)
                
                self.state.shells[session].running = True
                await self.state.shells[session].session.send_command(command)

                locl = (
                    " (local-interactive)"
                    if isinstance(self.state.shells[session].session, LocalInteractiveSession)
                    else (
                        " (local-noninteractive)"
                        if isinstance(self.state.shells[session].session, LocalNonInteractiveSession)
                        else (
                            " (remote)"
                            if isinstance(self.state.shells[session].session, SSHInteractiveSession)
                            else " (unknown)"
                        )
                    )
                )

                PrintStyle(
                    background_color="white", font_color="#1B4F72", bold=True
                ).print(f"{self.agent.agent_name} code execution output{locl}")
                return await self.get_terminal_output(session=session, prefix=prefix, timeouts=(timeouts or CODE_EXEC_TIMEOUTS))

            except Exception as e:
                if i == 1:
                    # try again on lost connection
                    PrintStyle.error(str(e))
                    await self.prepare_state(reset=True, session=session)
                    continue
                else:
                    raise e

    def format_command_for_output(self, command: str):
        # truncate long commands
        short_cmd = command[:200]
        # normalize whitespace for cleaner output
        short_cmd = " ".join(short_cmd.split())
        # replace any sequence of ', ", or ` with a single '
        # short_cmd = re.sub(r"['\"`]+", "'", short_cmd) # no need anymore
        # final length
        short_cmd = truncate_text_string(short_cmd, 100)
        return f"{short_cmd}"

    async def get_terminal_output(
        self,
        session=0,
        reset_full_output=True,
        first_output_timeout=30,  # Wait up to x seconds for first output
        between_output_timeout=15,  # Wait up to x seconds between outputs
        dialog_timeout=5,  # potential dialog detection timeout
        max_exec_timeout=180,  # hard cap on total runtime
        sleep_time=0.1,
        prefix="",
        timeouts: dict | None = None,
    ):

        # if not self.state:
        self.state = await self.prepare_state(session=session)

        # Override timeouts if a dict is provided
        if timeouts:
            first_output_timeout = timeouts.get("first_output_timeout", first_output_timeout)
            between_output_timeout = timeouts.get("between_output_timeout", between_output_timeout)
            dialog_timeout = timeouts.get("dialog_timeout", dialog_timeout)
            max_exec_timeout = timeouts.get("max_exec_timeout", max_exec_timeout)

        start_time = time.time()
        last_output_time = start_time
        full_output = ""
        truncated_output = ""
        got_output = False

        # ADR rca_terminal_blocking_stall: Signal to supervisor that agent is alive
        # but blocked in a long-running tool execution. This prevents the supervisor
        # from declaring the agent "DEAD" and skipping nudge injection — nudges are
        # the ONLY mechanism to break agents out of this blocking loop.
        if hasattr(self.agent, 'data') and isinstance(self.agent.data, dict):
            self.agent.data["_blocked_in_tool"] = True

        # if prefix, log right away
        if prefix:
            self.log.update(content=prefix)

        try:
          while True:
            await asyncio.sleep(sleep_time)
            full_output, partial_output = await self.state.shells[session].session.read_output(
                timeout=1, reset_full_output=reset_full_output
            )
            reset_full_output = False  # only reset once

            await self.agent.handle_intervention()

            now = time.time()
            if partial_output:
                PrintStyle(font_color="#85C1E9").stream(partial_output)
                # full_output += partial_output # Append new output
                truncated_output = self.fix_full_output(full_output)
                self.set_progress(truncated_output)
                heading = self.get_heading_from_output(truncated_output, 0)
                self.log.update(content=prefix + truncated_output, heading=heading)
                last_output_time = now
                got_output = True

                # Gate 5 fix (MSR_Smoke_1777332729 RCA): Refresh last_activity_ts
                # whenever we receive build output. Without this, last_activity_ts
                # goes stale during 5+ minute npm builds, causing Gate 5 to pass
                # through and declare the agent dead.
                if hasattr(self.agent, 'loop_data') and self.agent.loop_data:
                    self.agent.loop_data.last_activity_ts = now

                # Check for shell prompt at the end of output
                last_lines = (
                    truncated_output.splitlines()[-3:] if truncated_output else []
                )
                last_lines.reverse()
                for idx, line in enumerate(last_lines):
                    for pat in self.prompt_patterns:
                        if pat.search(line.strip()):
                            PrintStyle.info(
                                "Detected shell prompt, returning output early."
                            )
                            last_lines.reverse()
                            heading = self.get_heading_from_output(
                                "\n".join(last_lines), idx + 1, True
                            )
                            self.log.update(heading=heading)
                            self.mark_session_idle(session)
                            return truncated_output

            # ADR-82: Non-interactive process completion detection.
            # Non-interactive sessions have no shell prompt to detect —
            # instead we check if the subprocess has exited. When it has
            # and there's no more buffered output, return immediately.
            # This prevents the 20-180s ghost delay (Bug #1 from ADR-82).
            shell_session = self.state.shells[session].session
            if hasattr(shell_session, 'is_process_done') and shell_session.is_process_done():
                # Process exited — drain any remaining output
                await asyncio.sleep(0.2)  # Brief pause for final output chunks
                full_output, final_partial = await shell_session.read_output(timeout=0.5)
                if final_partial:
                    truncated_output = self.fix_full_output(full_output)
                    self.log.update(content=prefix + truncated_output)
                if truncated_output:
                    heading = self.get_heading_from_output(truncated_output, 0, True)
                    self.log.update(content=prefix + truncated_output, heading=heading)
                PrintStyle.info("Non-interactive process completed, returning output.")
                self.mark_session_idle(session)
                return truncated_output

            # Check for max execution time
            if now - start_time > max_exec_timeout:
                sysinfo = self.agent.read_prompt(
                    "fw.code.max_time.md", timeout=max_exec_timeout
                )
                response = self.agent.read_prompt("fw.code.info.md", info=sysinfo)
                if truncated_output:
                    response = truncated_output + "\n\n" + response
                PrintStyle.warning(sysinfo)
                heading = self.get_heading_from_output(truncated_output, 0)
                self.log.update(content=prefix + response, heading=heading)
                await self._cleanup_on_timeout(session)
                return response

            # Waiting for first output
            if not got_output:
                if now - start_time > first_output_timeout:
                    sysinfo = self.agent.read_prompt(
                        "fw.code.no_out_time.md", timeout=first_output_timeout
                    )
                    response = self.agent.read_prompt("fw.code.info.md", info=sysinfo)
                    PrintStyle.warning(sysinfo)
                    self.log.update(content=prefix + response)
                    await self._cleanup_on_timeout(session)
                    return response
            else:
                # Waiting for more output after first output
                if now - last_output_time > between_output_timeout:
                    sysinfo = self.agent.read_prompt(
                        "fw.code.pause_time.md", timeout=between_output_timeout
                    )
                    response = self.agent.read_prompt("fw.code.info.md", info=sysinfo)
                    if truncated_output:
                        response = truncated_output + "\n\n" + response
                    PrintStyle.warning(sysinfo)
                    heading = self.get_heading_from_output(truncated_output, 0)
                    self.log.update(content=prefix + response, heading=heading)
                    await self._cleanup_on_timeout(session)
                    return response

                # potential dialog detection
                if now - last_output_time > dialog_timeout:
                    # Check for dialog prompt at the end of output
                    last_lines = (
                        truncated_output.splitlines()[-2:] if truncated_output else []
                    )
                    for line in last_lines:
                        for pat in self.dialog_patterns:
                            if pat.search(line.strip()):
                                PrintStyle.info(
                                    "Detected dialog prompt, returning output early."
                                )

                                sysinfo = self.agent.read_prompt(
                                    "fw.code.pause_dialog.md", timeout=dialog_timeout
                                )
                                response = self.agent.read_prompt(
                                    "fw.code.info.md", info=sysinfo
                                )
                                if truncated_output:
                                    response = truncated_output + "\n\n" + response
                                PrintStyle.warning(sysinfo)
                                heading = self.get_heading_from_output(
                                    truncated_output, 0
                                )
                                self.log.update(
                                    content=prefix + response, heading=heading
                                )
                                await self._cleanup_on_timeout(session)
                                return response
        finally:
            # ADR rca_terminal_blocking_stall: Always clear the blocked flag
            if hasattr(self.agent, 'data') and isinstance(self.agent.data, dict):
                self.agent.data["_blocked_in_tool"] = False

    async def handle_running_session(
        self,
        session=0,
        reset_full_output=True, 
        prefix=""
    ):
        if not self.state or session not in self.state.shells:
            return None
        if not self.state.shells[session].running:
            return None
        
        full_output, _ = await self.state.shells[session].session.read_output(
            timeout=1, reset_full_output=reset_full_output
        )
        truncated_output = self.fix_full_output(full_output)
        self.set_progress(truncated_output)
        heading = self.get_heading_from_output(truncated_output, 0)

        last_lines = (
            truncated_output.splitlines()[-3:] if truncated_output else []
        )
        last_lines.reverse()
        for idx, line in enumerate(last_lines):
            for pat in self.prompt_patterns:
                if pat.search(line.strip()):
                    PrintStyle.info(
                        "Detected shell prompt, returning output early."
                    )
                    self.mark_session_idle(session)
                    return None

        has_dialog = False 
        for line in last_lines:
            for pat in self.dialog_patterns:
                if pat.search(line.strip()):
                    has_dialog = True
                    break
            if has_dialog:
                break

        if has_dialog:
            sys_info = self.agent.read_prompt("fw.code.pause_dialog.md", timeout=1)       
        else:
            sys_info = self.agent.read_prompt("fw.code.running.md", session=session)

        response = self.agent.read_prompt("fw.code.info.md", info=sys_info)
        if truncated_output:
            response = truncated_output + "\n\n" + response
        PrintStyle(font_color="#FFA500", bold=True).print(response)
        self.log.update(content=prefix + response, heading=heading)
        return response
    
    def mark_session_idle(self, session: int = 0):
        # Mark session as idle - command finished
        if self.state and session in self.state.shells:
            self.state.shells[session].running = False

    async def _cleanup_on_timeout(self, session: int = 0):
        """Kill lingering process and mark session idle after a timeout.
        
        ADR-82: For interactive sessions (PTY), sends Ctrl+C (SIGINT) to kill
        the hung command. For non-interactive sessions (subprocess), calls
        close() to SIGTERM/SIGKILL the process — send_command('\x03') would
        spawn a NEW subprocess instead of killing the stuck one (Bug #2).
        """
        try:
            if self.state and session in self.state.shells:
                shell = self.state.shells[session]
                if isinstance(shell.session, LocalNonInteractiveSession):
                    # Non-interactive: close() properly terminates the process
                    await shell.session.close()
                    PrintStyle.warning(f"Terminated non-interactive process in session {session}")
                else:
                    # Interactive (PTY): send Ctrl+C to the terminal
                    await shell.session.send_command('\x03')  # SIGINT
                    PrintStyle.warning(f"Sent Ctrl+C to session {session} to kill lingering process")
        except Exception as e:
            PrintStyle.error(f"Failed to cleanup session {session}: {e}")
        finally:
            self.mark_session_idle(session)

    async def reset_terminal(self, session=0, reason: str | None = None):
        # Print the reason for the reset to the console if provided
        if reason:
            PrintStyle(font_color="#FFA500", bold=True).print(
                f"Resetting terminal session {session}... Reason: {reason}"
            )
        else:
            PrintStyle(font_color="#FFA500", bold=True).print(
                f"Resetting terminal session {session}..."
            )

        # Only reset the specified session while preserving others
        await self.prepare_state(reset=True, session=session)
        response = self.agent.read_prompt(
            "fw.code.info.md", info=self.agent.read_prompt("fw.code.reset.md")
        )
        self.log.update(content=response)
        return response

    def get_heading_from_output(self, output: str, skip_lines=0, done=False):
        done_icon = " icon://done_all" if done else ""

        if not output:
            return self.get_heading() + done_icon

        # find last non-empty line with skip
        lines = output.splitlines()
        # Start from len(lines) - skip_lines - 1 down to 0
        for i in range(len(lines) - skip_lines - 1, -1, -1):
            line = lines[i].strip()
            if not line:
                continue
            return self.get_heading(line) + done_icon

        return self.get_heading() + done_icon

    def fix_full_output(self, output: str):
        # remove any single byte \xXX escapes
        output = re.sub(r"(?<!\\)\\x[0-9A-Fa-f]{2}", "", output)
        # RCA-365 F-2a: Use command-aware thresholds for truncation
        command = getattr(self, '_current_command', None)
        max_lines, max_chars = get_thresholds_for_command(command)
        output = truncate_output_middle_out(output, max_lines=max_lines, max_chars=max_chars)
        return output

    def get_cwd(self):
        project_name = projects.get_context_project_name(self.agent.context)
        if not project_name:
            # Priority 2 (RCA-315c): Check _active_project_dir on agent.data
            # Set by propagate_data_to_subordinate() or _05_project_context_init.
            # This achieves parity with services_mgt._resolve_project_cwd().
            active_dir = self.agent.data.get("_active_project_dir", "")
            if active_dir and os.path.isdir(active_dir):
                return active_dir
            # Last resort: Use /agix as default CWD for terminal tools in Docker
            # to prevent searching / which causes hangs
            if os.path.exists("/agix"):
                return "/agix"
            return None
        project_path = projects.get_project_folder(project_name)
        normalized = files.normalize_agix_path(project_path)
        return files.fix_dev_path(normalized)

    # Threshold: heredocs below this line count are allowed (e.g., .env files)
    # H1 (RCA MSR_1777396305): Lowered from 50→5. Heredoc is BANNED for code
    # generation — only tiny env/config files (≤4 lines) are allowed.
    HEREDOC_LINE_THRESHOLD = 5

    # Pattern to detect heredoc file creation: cat/tee with << and a file path
    HEREDOC_PATTERN = re.compile(
        r"(cat|tee)\s+.*<<[-~]?\s*['\"]?(\w+)['\"]?",
        re.IGNORECASE,
    )

    def _guard_heredoc_file_creation(self, command: str) -> str | None:
        """Detect heredoc file creation and reject with write_to_file guidance.

        Only tiny heredocs (≤4 lines, e.g. .env files) are allowed.
        All code generation via heredoc is BANNED — use write_to_file instead.
        Heredoc causes shell escaping failures with JSX, backticks, and
        template literals.

        Returns rejection message string if blocked, None if allowed.
        """
        if not command:
            return None

        # Check if command contains a heredoc pattern
        if not self.HEREDOC_PATTERN.search(command):
            return None

        # Count lines in the heredoc content (everything between << marker and EOF)
        lines = command.count("\n")
        if lines < self.HEREDOC_LINE_THRESHOLD:
            return None  # Tiny heredoc — allowed (e.g., .env)

        # Heredoc detected — reject with guidance
        return (
            f"❌ HEREDOC BANNED: Do NOT use heredoc (cat << EOF) for writing code files. "
            f"Detected {lines} lines via heredoc.\n\n"
            f"USE the write_to_file TOOL INSTEAD:\n"
            f'{{"tool_name": "write_to_file", "tool_args": {{"path": "<file_path>", "content": "<file_content>"}}}}\n\n'
            f"Heredoc causes shell escaping failures with JSX, backticks, and template literals. "
            f"For files >1500 lines, break into chunks: write the first part with write_to_file, "
            f"then use replace_in_file to append remaining sections."
        )