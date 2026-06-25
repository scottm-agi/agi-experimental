from __future__ import annotations
import asyncio
import os
import time
from typing import Optional, Tuple
from python.helpers import strings


class LocalInteractiveSession:
    """Full PTY session — used when interactive=True.
    
    Provides full interactive stdin via pty.openpty().
    Used for: database CLIs, git auth, interactive REPLs, input tool.
    """
    def __init__(self, cwd: str|None = None):
        from python.helpers import tty_session
        self.session: tty_session.TTYSession|None = None
        self.full_output = ''
        self.cwd = cwd

    async def connect(self):
        from python.helpers import tty_session, runtime
        self.session = tty_session.TTYSession(runtime.get_terminal_executable(), cwd=self.cwd)
        await self.session.start()
        await self.session.read_full_until_idle(idle_timeout=1, total_timeout=1)

    async def close(self):
        if self.session:
            self.session.kill()
            # self.session.wait()

    async def send_command(self, command: str):
        if not self.session:
            raise Exception("Shell not connected")
        self.full_output = ""
        await self.session.sendline(command)
 
    async def read_output(self, timeout: float = 0, reset_full_output: bool = False) -> Tuple[str, Optional[str]]:
        if not self.session:
            raise Exception("Shell not connected")

        if reset_full_output:
            self.full_output = ""

        # get output from terminal
        partial_output = await self.session.read_full_until_idle(idle_timeout=0.01, total_timeout=timeout)
        self.full_output += partial_output

        # clean output
        partial_output = strings.clean_string(partial_output)
        clean_full_output = strings.clean_string(self.full_output)

        if not partial_output:
            return clean_full_output, None
        return clean_full_output, partial_output


class LocalNonInteractiveSession:
    """Non-interactive subprocess — stdin is /dev/null (ADR-82).
    
    DEFAULT mode for all terminal commands. Interactive prompts get EOF
    immediately — no hang, no timeout, no dialog detection needed.
    Pager env vars (PAGER=cat, etc.) prevent interactive pagers.
    
    Same interface as LocalInteractiveSession for drop-in compatibility.
    
    For interactive use cases (database CLIs, git auth, debugging),
    use LocalInteractiveSession instead by setting interactive=True.
    """

    PAGER_SUPPRESSION = {
        "PAGER": "cat",
        "GIT_PAGER": "cat",
        "SYSTEMD_PAGER": "",
        "MANPAGER": "cat",
    }

    def __init__(self, cwd: str | None = None):
        self.proc: asyncio.subprocess.Process | None = None
        self.full_output = ""
        self.cwd = cwd
        self._buf: asyncio.Queue[str] = asyncio.Queue()
        self._pump_task: asyncio.Task | None = None

    async def connect(self):
        """No-op for non-interactive — session is created per-command."""
        pass

    def is_process_done(self) -> bool:
        """Check if the subprocess has exited.
        
        Used by get_terminal_output() to detect command completion
        without relying on shell prompt detection (which doesn't exist
        in non-interactive subprocess mode).
        
        Returns True if:
        - No process has been started yet
        - The process has exited (returncode is set)
        Returns False if the process is still running.
        """
        if self.proc is None:
            return True
        return self.proc.returncode is not None

    async def close(self):
        """Terminate any running process and cancel pump task."""
        if self._pump_task and not self._pump_task.done():
            self._pump_task.cancel()
            try:
                await self._pump_task
            except asyncio.CancelledError:
                pass
            self._pump_task = None

        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.proc.kill()
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
        self.proc = None

    async def send_command(self, command: str):
        """Execute a command as a one-shot subprocess with stdin=DEVNULL.
        
        Each call creates a new subprocess. Previous process is cleaned up.
        stdin is /dev/null — ALL interactive prompts get EOF immediately.
        """
        # Clean up previous process if any
        await self.close()

        # Clear buffer
        self._buf = asyncio.Queue()
        self.full_output = ""

        env = {**os.environ, **self.PAGER_SUPPRESSION}

        self.proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.cwd,
            env=env,
        )
        self._pump_task = asyncio.create_task(self._pump_stdout())

    async def _pump_stdout(self):
        """Read stdout into buffer queue for read_output compatibility."""
        try:
            while self.proc and self.proc.stdout:
                chunk = await self.proc.stdout.read(4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                self._buf.put_nowait(text)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass  # Process died — normal for short commands

    async def read_output(self, timeout: float = 0, reset_full_output: bool = False) -> Tuple[str, Optional[str]]:
        """Read available output, compatible with LocalInteractiveSession.
        
        Returns (full_output, partial_output) where partial is None if no new data.
        """
        if reset_full_output:
            self.full_output = ""

        partial = ""
        # Drain all immediately available chunks
        try:
            while True:
                chunk = self._buf.get_nowait()
                partial += chunk
        except asyncio.QueueEmpty:
            pass

        # If no data yet, wait up to timeout for first chunk
        if not partial and timeout > 0:
            try:
                chunk = await asyncio.wait_for(self._buf.get(), timeout=timeout)
                partial = chunk
                # Drain any more that arrived
                try:
                    while True:
                        chunk = self._buf.get_nowait()
                        partial += chunk
                except asyncio.QueueEmpty:
                    pass
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        self.full_output += partial

        # Clean output (same as LocalInteractiveSession)
        clean_partial = strings.clean_string(partial) if partial else None
        clean_full = strings.clean_string(self.full_output)

        if not clean_partial:
            return clean_full, None
        return clean_full, clean_partial