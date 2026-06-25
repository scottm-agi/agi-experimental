import os
import json
import logging
import re
import shutil
import subprocess
import asyncio
import time
import signal
from typing import Dict, Any, List, Optional
from python.helpers.tool import Tool, Response
from python.helpers.print_style import PrintStyle

logger = logging.getLogger("services-mgt")

# Default state file path (relative to CWD)
DEFAULT_STATE_FILE = "managed_services.json"
# Auto-prune threshold: remove dead services older than this (seconds)
AUTO_PRUNE_AGE_THRESHOLD = 3600  # 1 hour

class ServicesMgt(Tool):
    """
    Tool to manage and verify background services on reserved ports (5100-5500).
    """

    def __init__(self, agent=None, name="services_mgt", method=None, args=None, message="", loop_data=None, **kwargs):
        super().__init__(agent, name, method, args or {}, message, loop_data, **kwargs)
        self.reserved_range = range(5100, 5501)
        self.state_file = "data/managed_services.json"
        
        # Environment sensitivity for port mappings (Docker host side)
        self.env = os.environ.get("AGIX_ENV", "dev").lower()
        self.is_dev_mode = os.environ.get("AGIX_DEV_MODE", "").lower() == "true"

    async def execute(self, action: str = None, **kwargs) -> Response:
        """
        Execute an action for service management.

        ITR-29d: Wired with ServiceRetryTracker to prevent infinite
        start/restart loops. After 3 consecutive failures on the same
        port, further start/restart attempts are blocked with actionable
        guidance.
        """
        # If action not in kwargs but in args (AI typically puts it there)
        action = action or self.args.get("action")
        
        if not action:
            return Response(message="Error: Missing 'action' parameter.", break_loop=False)

        # ── ITR-29d: Retry budget check for start/restart ────────────
        target_port = None
        if action in ("start_service", "restart_service"):
            target_port = kwargs.get("port") or self.args.get("port")
            if target_port:
                try:
                    target_port = int(target_port)
                except (ValueError, TypeError):
                    target_port = None
            if target_port:
                tracker = self._get_retry_tracker()
                if tracker.should_block(target_port):
                    block_msg = tracker.get_block_message(target_port)
                    logger.error(
                        f"[SERVICES_MGT] BLOCKED: port {target_port} has exceeded "
                        f"retry budget ({tracker.get_failure_count(target_port)} failures)"
                    )
                    return Response(
                        message=block_msg or f"Port {target_port} blocked after repeated failures.",
                        break_loop=False,
                    )

        try:
            if action == "check_port":
                result = await self.check_port(kwargs.get("port") or self.args.get("port"))
            elif action == "start_service":
                result = await self.start_service(
                    command=kwargs.get("command") or self.args.get("command"),
                    port=kwargs.get("port") or self.args.get("port"),
                    name=kwargs.get("name") or self.args.get("name"),
                    project_dir=kwargs.get("project_dir") or self.args.get("project_dir")
                )
            elif action == "stop_service":
                result = await self.stop_service(kwargs.get("service_id") or self.args.get("service_id"))
            elif action == "restart_service":
                result = await self.restart_service(
                    service_id=kwargs.get("service_id") or self.args.get("service_id"),
                    port=kwargs.get("port") or self.args.get("port"),
                )
            elif action == "list_services":
                result = await self.list_services()
            elif action == "test_service":
                result = await self.test_service(kwargs.get("port") or self.args.get("port"))
            elif action == "get_service_logs":
                result = await self.get_service_logs(
                    port=kwargs.get("port") or self.args.get("port"),
                    service_id=kwargs.get("service_id") or self.args.get("service_id"),
                    lines=kwargs.get("lines") or self.args.get("lines", 30),
                    filter_mode=kwargs.get("filter_mode") or self.args.get("filter"),
                )
            elif action == "kill_port":
                result = await self.kill_port(kwargs.get("port") or self.args.get("port"))
            else:
                result = {"status": "error", "message": f"Unknown action: {action}"}

            # ── ITR-29d: Record success/failure for retry tracking ────
            if action in ("start_service", "restart_service") and target_port:
                tracker = self._get_retry_tracker()
                status = result.get("status", "error") if isinstance(result, dict) else "error"
                if status == "success":
                    tracker.record_success(target_port)
                elif status in ("error", "warning"):
                    error_msg = result.get("message", "unknown") if isinstance(result, dict) else str(result)
                    tracker.record_failure(target_port, error_msg)

            return Response(message=json.dumps(result, indent=2), break_loop=False)

        except Exception as e:
            logger.exception(f"Error in ServicesMgt.{action}: {e}")
            # Record exception as failure too
            if action in ("start_service", "restart_service") and target_port:
                tracker = self._get_retry_tracker()
                tracker.record_failure(target_port, str(e))
            return Response(message=f"Error: {str(e)}", break_loop=False)

    def _get_retry_tracker(self):
        """Get or create the ServiceRetryTracker from agent.data."""
        from python.helpers.service_retry_tracker import ServiceRetryTracker
        if self.agent:
            tracker = self.agent.data.get("_service_retry_tracker")
            if tracker is None:
                tracker = ServiceRetryTracker(max_retries=3)
                self.agent.data["_service_retry_tracker"] = tracker
            return tracker
        # No agent — use a transient tracker
        return ServiceRetryTracker(max_retries=3)

    def run_sync(self, action: str, **kwargs) -> Dict[str, Any]:
        """Wrapper for sync testing (TDD)."""
        loop = asyncio.get_event_loop()
        res = loop.run_until_complete(self.execute(action=action, **kwargs))
        return json.loads(res.message)

    async def check_port(self, port: Any) -> Dict[str, Any]:
        """Verify if a port is in range and check its status, including binding address."""
        try:
            port = int(port)
        except (ValueError, TypeError):
            return {"status": "error", "message": f"Invalid port: {port}"}
    
        if port not in self.reserved_range:
            return {
                "status": "error", 
                "message": f"Port {port} is out of the reserved range (5100-5500)."
            }
    
        # Check if something is listening and get binding info
        try:
            # -i :<port> check for port
            # -n : No DNS resolution
            # -P : No port name resolution
            cmd = f"lsof -i :{port} -n -P"
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            output = proc.stdout.strip()
            if output:
                # Parse output to find binding
                # Example: TCP *:5100 (LISTEN) or TCP 127.0.0.1:5100 (LISTEN)
                lines = output.split('\n')
                binding = "unknown"
                pid = "unknown"
                
                if len(lines) > 1:
                    # Header is usually at index 0, first process at index 1
                    parts = lines[1].split()
                    if len(parts) >= 9:
                        pid = parts[1]
                        name_col = parts[8] # e.g. *:5100 or 127.0.0.1:5100
                        if ":" in name_col:
                            binding = name_col.split(":")[0]
                
                is_external = binding in ["*", "0.0.0.0", "::"]
                host_url = self.get_host_access_url(port)
                
                res = {
                    "status": "success",
                    "state": "busy",
                    "port": port,
                    "pid": pid,
                    "binding": binding,
                    "accessible_from_host": is_external
                }
                
                if not is_external:
                    res["warning"] = f"CRITICAL: Service is bound to '{binding}' and is NOT accessible from the host. It MUST be bound to '0.0.0.0' for display."
                else:
                    res["host_url"] = host_url
                    
                return res
            else:
                return {
                    "status": "success",
                    "state": "free",
                    "port": port
                }
        except Exception as e:
            return {"status": "error", "message": f"Failed to check port: {e}"}

    def get_host_access_url(self, port: int) -> str:
        """Calculate the host-side URL based on environment port mappings."""
        # dev mode mappings: 5100-5199 -> 5100-5199
        # master mode mappings: 5300-5399 -> 5100-5199
        host_port = port
        if self.env == "master":
            # Map container 51xx to host 53xx
            if 5100 <= port <= 5199:
                host_port = port + 200
        
        return f"http://localhost:{host_port}"

    def _detect_framework(self, command: str, project_dir: str = None) -> str:
        """Auto-detect the dev server framework from the command string,
        falling back to package.json inspection if command is ambiguous.
        
        Args:
            command: The shell command string to analyze
            project_dir: Optional project directory to check package.json
        
        Returns:
            'next' for Next.js commands/projects
            'vite' for Vite commands/projects
            'nuxt' for Nuxt commands/projects
            'unknown' for anything else
        """
        cmd_lower = command.lower()
        if 'next' in cmd_lower:
            return 'next'
        if 'vite' in cmd_lower:
            return 'vite'
        if 'nuxt' in cmd_lower:
            return 'nuxt'
        # Fallback: check package.json in project directory
        if project_dir:
            return self._detect_framework_from_project(project_dir)
        return 'unknown'

    def _detect_framework_from_project(self, project_dir: str) -> str:
        """Detect framework from package.json dependencies.
        
        Checks both dependencies and devDependencies for:
        - 'next' → 'next' (uses --hostname instead of --host)
        - 'vite' → 'vite'
        - 'nuxt' → 'nuxt' (also uses --hostname)
        
        Returns 'unknown' if no framework detected or on any error.
        """
        try:
            pkg_path = os.path.join(project_dir, "package.json")
            if not os.path.exists(pkg_path):
                return "unknown"
            with open(pkg_path, "r") as f:
                pkg = json.load(f)
            all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if "next" in all_deps:
                return "next"
            if "nuxt" in all_deps:
                return "nuxt"
            if "vite" in all_deps:
                return "vite"
        except Exception as e:
            logger.warning(f"[SERVICES] Framework detection failed for {project_dir}: {e}")
        return "unknown"

    def _needs_double_dash(self, command: str) -> bool:
        """Detect npm/yarn/pnpm script wrapper commands that need '--' before flags.
        
        RCA-315b: 'npm run dev --port 5140' passes --port to npm (which ignores it)
        instead of to the underlying 'next dev' binary. The correct form is:
        'npm run dev -- --port 5140'.
        
        Applies to:
        - npm run <script>
        - yarn run <script> / yarn <script> (shorthand)
        - pnpm run <script>
        
        Does NOT apply to:
        - npm install, npm test (built-in npm commands, not script wrappers)
        - npx <cmd> (direct binary execution)
        - node <file> (direct execution)
        """
        cmd = command.strip()
        # npm run <anything> / yarn run <anything> / pnpm run <anything>
        if re.match(r'^(npm|pnpm)\s+run\s+', cmd):
            return True
        # yarn run <anything>
        if re.match(r'^yarn\s+run\s+', cmd):
            return True
        # yarn <script> (shorthand) — but NOT yarn install/add/remove/etc.
        yarn_builtins = {'install', 'add', 'remove', 'upgrade', 'cache', 'config',
                         'global', 'init', 'link', 'login', 'logout', 'pack',
                         'publish', 'test', 'version', 'workspace', 'workspaces',
                         'why', 'audit', 'autoclean', 'bin', 'create', 'dedupe',
                         'dlx', 'exec', 'explain', 'info', 'licenses', 'list',
                         'node', 'npm', 'patch', 'plugin', 'rebuild', 'set',
                         'unplug', 'up', '--version', '-v', '--help', '-h'}
        if re.match(r'^yarn\s+(\w+)', cmd):
            script_name = re.match(r'^yarn\s+(\w+)', cmd).group(1)
            if script_name not in yarn_builtins:
                return True
        return False

    def _ensure_double_dash(self, command: str, flag_str: str) -> str:
        """Append flag_str to command, inserting '--' if needed for npm scripts.
        
        If command already contains '--', appends after it (no duplicate '--').
        If command is an npm script wrapper, inserts '--' before the flag.
        Otherwise, appends flag directly.
        """
        if ' -- ' in command:
            # Already has '--', just append after it
            return f"{command} {flag_str}"
        if self._needs_double_dash(command):
            return f"{command} -- {flag_str}"
        return f"{command} {flag_str}"

    def _inject_host_flag(self, command: str, framework: str = None) -> str:
        """Inject or normalize the host binding flag per framework.
        
        Next.js uses --hostname, Vite and others use --host.
        Converts incorrect flags to framework-appropriate ones.
        
        Args:
            command: The dev server command string
            framework: 'next', 'vite', or 'unknown'. Auto-detected if None.
        """
        if framework is None:
            framework = self._detect_framework(command)
        
        if framework == 'next':
            # Next.js: convert --host to --hostname, or append --hostname
            if re.search(r'--hostname\s', command):
                return command  # Already correct
            if re.search(r'--host\s+([\d.]+)', command):
                command = re.sub(r'--host\s+([\d.]+)', r'--hostname \1', command)
                return command
            # No host flag at all — append
            command = self._ensure_double_dash(command, "--hostname 0.0.0.0")
            return command
        else:
            # Vite / unknown: use --host
            if re.search(r'--host\s', command) or re.search(r'--host$', command):
                return command  # Already has --host
            if re.search(r'--hostname\s', command):
                command = re.sub(r'--hostname\s+([\d.]+)', r'--host \1', command)
                return command
            command = self._ensure_double_dash(command, "--host 0.0.0.0")
            return command

    def _inject_port(self, command: str, port: int) -> str:
        """Inject the allocated port into the command string.
        
        Handles:
        - {PORT} placeholder replacement
        - $PORT / ${PORT} / $port shell variable replacement
        - --port XXXX flag correction  
        - --port=XXXX flag correction
        - -p XXXX short flag correction
        - Appending --port if no port flag present
        """
        port_str = str(port)
        
        # Regex fragment matching either digits or shell variable references
        # Matches: 3000, $PORT, ${PORT}, $port, ${port}
        PORT_VALUE = r'(?:\d+|\$\{?[Pp][Oo][Rr][Tt]\}?)'
        
        # 1. Replace {PORT} placeholder (uppercase, no $)
        if "{PORT}" in command:
            command = command.replace("{PORT}", port_str)
            return command
        
        # 2. Replace $PORT / ${PORT} / $port standalone or in -p / --port context
        # This must run BEFORE the digit-only checks to catch shell variables
        if re.search(r'\$\{?[Pp][Oo][Rr][Tt]\}?', command):
            command = re.sub(r'\$\{?[Pp][Oo][Rr][Tt]\}?', port_str, command)
            return command
        
        # 3. Replace --port=XXXX
        if re.search(r'--port=\d+', command):
            command = re.sub(r'--port=\d+', f'--port={port_str}', command)
            return command
        
        # 4. Replace --port XXXX (space-separated)
        if re.search(r'--port\s+\d+', command):
            command = re.sub(r'--port\s+\d+', f'--port {port_str}', command)
            return command
        
        # 5. Replace -p XXXX (short flag)
        if re.search(r'-p\s+\d+', command):
            command = re.sub(r'-p\s+\d+', f'-p {port_str}', command)
            return command

        # 6. No port flag found — append --port (with '--' for npm scripts)
        command = self._ensure_double_dash(command, f"--port {port_str}")
        return command

    async def _health_check(self, port: int, timeout: int = 90) -> bool:
        """Poll the port until the server returns a healthy HTTP response.
        
        Healthy = HTTP 200-499 (server is running and responding).
        - 2xx/3xx: Server is fully operational
        - 4xx: Server is running (route may not exist yet, but process is up)
        - 5xx: Server is broken/rebuilding — retry with backoff
        - 000: Connection refused — retry with backoff
        
        RCA-331 warm-up: When the first 500 is received, the server IS running
        but is doing on-demand compilation (e.g., Next.js first-request compile
        + Google Fonts fetch retry in Docker). Instead of rapid-fire polling
        that all get 500, send ONE blocking warm-up request with a 30s timeout
        to let the full compilation cycle complete. This single request absorbs
        the compilation time. If it returns <500, the server is healthy.
        
        Records self._last_health_code for diagnostics.
        Uses progressive polling: 1s for first 30 attempts, then 3s.
        """
        start = time.time()
        attempt = 0
        warmup_done = False  # RCA-331: only attempt warm-up once
        self._last_stderr = ""  # Capture last stderr for diagnostics
        self._last_health_code = "000"  # Track last HTTP code for diagnostics
        while time.time() - start < timeout:
            attempt += 1
            try:
                cmd = f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{port}"
                proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
                code = proc.stdout.strip().strip("'")
                if proc.stderr:
                    self._last_stderr = proc.stderr.strip()
                if code:
                    self._last_health_code = code
                    # RCA-255: Only accept codes < 500 as healthy
                    # 5xx means server is broken/rebuilding — keep retrying
                    # 000 means connection refused — keep retrying
                    try:
                        code_int = int(code)
                    except ValueError:
                        code_int = 0
                    if 200 <= code_int < 500:
                        elapsed = time.time() - start
                        logger.info(f"[SERVICES_MGT] Health check passed: port {port} returned HTTP {code} after {elapsed:.1f}s ({attempt} attempts)")
                        return True
                    elif code_int >= 500 and not warmup_done:
                        # ── RCA-331: First-request warm-up ──────────────────
                        # Server IS running (returned 500, not connection refused).
                        # Next.js dev mode compiles pages on first request. In Docker,
                        # Google Fonts fetch fails + retries, causing 3-10s of 500s.
                        # Send ONE blocking request with a long timeout to absorb
                        # the full compilation cycle instead of rapid-fire polling.
                        warmup_done = True
                        logger.info(
                            f"[SERVICES_MGT] Health check: port {port} returned HTTP {code} "
                            f"on first contact. Sending warm-up request (30s timeout) to "
                            f"absorb first-request compilation..."
                        )
                        try:
                            warmup_cmd = f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{port}"
                            warmup_proc = subprocess.run(
                                warmup_cmd, shell=True, capture_output=True,
                                text=True, timeout=30
                            )
                            warmup_code = warmup_proc.stdout.strip().strip("'")
                            if warmup_code:
                                self._last_health_code = warmup_code
                                try:
                                    warmup_code_int = int(warmup_code)
                                except ValueError:
                                    warmup_code_int = 0
                                if 200 <= warmup_code_int < 500:
                                    elapsed = time.time() - start
                                    logger.info(
                                        f"[SERVICES_MGT] Health check passed after warm-up: "
                                        f"port {port} returned HTTP {warmup_code} after "
                                        f"{elapsed:.1f}s ({attempt} attempts + warm-up)"
                                    )
                                    return True
                                else:
                                    logger.info(
                                        f"[SERVICES_MGT] Warm-up returned HTTP {warmup_code}, "
                                        f"resuming normal polling..."
                                    )
                        except subprocess.TimeoutExpired:
                            logger.info(
                                f"[SERVICES_MGT] Warm-up request timed out after 30s, "
                                f"resuming normal polling..."
                            )
                        # Warm-up didn't fix it — fall through to normal polling
                        continue
                    elif code_int >= 500:
                        logger.debug(f"[SERVICES_MGT] Health check: port {port} returned HTTP {code} (5xx, retrying...)")
            except subprocess.TimeoutExpired:
                pass
            except Exception as e:
                self._last_stderr = str(e)
            # Progressive backoff: 1s for first 30 attempts, then 3s
            sleep_time = 1 if attempt < 30 else 3
            await asyncio.sleep(sleep_time)
        
        logger.warning(f"[SERVICES_MGT] Health check FAILED: port {port} last HTTP {self._last_health_code} after {timeout}s ({attempt} attempts)")
        return False

    async def start_service(self, command: str, port: Any = None, name: str = None, project_dir: str = None) -> Dict[str, Any]:
        """Start a background service.
        
        If port is None, auto-allocates via PortManager using the project name
        for hash-based, cross-project-aware port assignment.
        """
        # Auto-allocate port if not specified
        if port is None or port == "" or port == "auto":
            port = self._auto_allocate_port(name or "main")
            if isinstance(port, dict):
                return port  # Error dict from allocation failure
            logger.info(f"[SERVICES_MGT] Auto-allocated port {port} via PortManager")
        
        check = await self.check_port(port)
        if check.get("status") == "error": return check
        if check.get("state") == "busy":
            # Port-reuse: Check if this is a service we already manage
            existing = self._find_managed_service(int(port))
            if existing:
                logger.info(
                    f"[SERVICES_MGT] Port {port} is busy with managed service "
                    f"'{existing.get('name')}' (PID {existing.get('pid')}) — reusing"
                )
                # Set dev server flags like a fresh start would
                if self.agent:
                    self.agent.data["_dev_server_started"] = True
                    self.agent.data["_services_mgt_dev_server"] = True
                return {
                    "status": "success",
                    "message": f"Service '{existing.get('name')}' already running on port {port} (reused)",
                    "service_id": existing.get("service_id", "reused"),
                    "pid": existing.get("pid"),
                    "port": port,
                    "reused": True,
                }
            # ── PORT AUTO-FAILOVER ──────────────────────────────────────
            # Port is busy but NOT managed by us. Instead of hard-failing,
            # auto-allocate the next free port via PortManager.
            # This prevents agents from wasting turns manually killing
            # processes and retrying on the same port.
            logger.warning(
                f"[SERVICES_MGT] Port {port} busy (unmanaged, PID {check.get('pid')}). "
                f"Auto-migrating to next free port."
            )
            new_port = self._auto_allocate_port(name or "main")
            if isinstance(new_port, dict):
                # Auto-allocation also failed — return actionable error
                return {
                    "status": "error",
                    "message": (
                        f"Port {port} is busy (PID {check.get('pid')}, not managed by services_mgt) "
                        f"and auto-allocation failed. Try one of these:\n"
                        f"1. Use action='stop_service' to stop a managed service first\n"
                        f"2. Specify a different port explicitly (5100-5500 range)\n"
                        f"3. Use code_execution_tool to run: fuser -k {port}/tcp"
                    ),
                }
            # Retry start_service with the new port (recursive, 1 level deep)
            logger.info(f"[SERVICES_MGT] Auto-migrated: port {port} → {new_port}")
            result = await self.start_service(command=command, port=new_port, name=name)
            result["port_migrated"] = True
            result["original_port"] = int(port)
            result["message"] = (
                f"Port {port} was busy (unmanaged) — auto-migrated to port {new_port}. "
                + result.get("message", "")
            )
            return result

        if not command:
            return {"status": "error", "message": "Missing 'command' to start service."}

        # Issue #1091: Inject allocated port into command to prevent mismatch
        original_command = command
        command = self._inject_port(command, int(port))

        # CRITICAL FIX: Auto-resolve project CWD from agent context.
        # Prevents ENOENT when npm can't find package.json at container root.
        # Must resolve BEFORE _inject_host_flag since _detect_framework needs it.
        project_cwd = self._resolve_project_cwd()
        if project_dir and project_cwd:
            if not os.path.isabs(project_dir):
                project_cwd = os.path.join(project_cwd, project_dir)
            else:
                project_cwd = project_dir
        elif project_dir and not project_cwd:
            project_cwd = project_dir
            
        if project_cwd:
            logger.info(f"[SERVICES_MGT] Auto-resolved project CWD: {project_cwd}")
        else:
            logger.warning(
                "[SERVICES_MGT] No project CWD resolved from agent context. "
                "Command will run in process CWD (may fail if no package.json)."
            )

        # Issue #1106: Normalize hostname flag per framework
        # Iteration 91: Use project_cwd for package.json-based framework detection
        # so "npm run dev" on a Next.js project gets --hostname (not --host)
        command = self._inject_host_flag(command, self._detect_framework(command, project_cwd))
        if command != original_command:
            logger.info(f"[SERVICES_MGT] Port injection: '{original_command}' → '{command}'")

        if project_cwd:
            command = self._ensure_cd_prefix(command, project_cwd)

        # Use absolute path for state persistence
        state_path = os.path.join(os.getcwd(), self.state_file)
        os.makedirs(os.path.dirname(state_path), exist_ok=True)

        try:
            # Build the command to run in background and detach
            # We want to make sure it doesn't die when the agent finish its turn
            # Issue #1104: Capture stderr to a temp file for diagnostics on failure
            stderr_log_path = f"/tmp/services_mgt_stderr_{port}.log"
            stderr_file = open(stderr_log_path, "w")
            # RCA-365 F-6: Capture stdout to a log file (not DEVNULL) so agents
            # can read server output for diagnostics. Previously stdout was
            # discarded, losing valuable error information.
            stdout_log_path = f"/tmp/services_mgt_stdout_{port}.log"
            stdout_file = open(stdout_log_path, "w")
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True, # Detach from parent
                cwd=project_cwd,  # Run in project dir, not container root
            )
            
            service_id = str(int(time.time()))
            service_entry = {
                "service_id": service_id,
                "name": name or f"service_{port}",
                "port": port,
                "pid": process.pid,
                "command": command,
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }

            services = self._load_services()
            services.append(service_entry)
            self._save_services(services)

            # Iter-51 Fix 2: Set dev server flags on agent so the
            # orchestrator gate (ESSENTIAL GATE) can see that a dev server
            # was started via services_mgt (not just code_execution_tool).
            # These flags propagate from child → parent via call_subordinate.
            if self.agent:
                self.agent.data["_dev_server_started"] = True
                self.agent.data["_services_mgt_dev_server"] = True
                self.agent.data["_dev_server_port"] = int(port)
                logger.info(
                    f"[SERVICES_MGT] Set _dev_server_started=True and "
                    f"_services_mgt_dev_server=True (port={port}) on agent "
                    f"{getattr(self.agent, 'agent_name', 'unknown')}"
                )

            # Issue #1091/#1104: Post-start health check with progressive retry (90s)
            healthy = await self._health_check(int(port), timeout=90)
            if not healthy:
                # Issue #1104: Capture stderr for diagnostics
                process_stderr = ""
                try:
                    stderr_file.flush()
                    with open(stderr_log_path, "r") as f:
                        process_stderr = f.read().strip()[-2000:]  # RCA-365 F-7: Last 2000 chars (was 500)
                except Exception as e:
                    logger.warning(f"[SERVICES] Health check stderr capture failed: {e}")
                last_stderr = getattr(self, '_last_stderr', '') or process_stderr
                logger.warning(
                    f"[SERVICES_MGT] Server started (PID {process.pid}) but port {port} "
                    f"is not responding after 90s. stderr: {last_stderr[:2000]}"
                )
                return {
                    "status": "warning",
                    "message": f"Service '{service_entry['name']}' started (PID {process.pid}) "
                               f"but port {port} is not yet responding after 90s. "
                               f"stderr: {last_stderr[:2000] if last_stderr else 'no output'}",
                    "service_id": service_id,
                    "pid": process.pid,
                    "port": port,
                    "health_check": "failed",
                    "last_stderr": last_stderr[:2000] if last_stderr else ""
                }

            return {
                "status": "success",
                "message": f"Service '{service_entry['name']}' started on port {port}",
                "service_id": service_id,
                "pid": process.pid,
                "port": int(port),
                "health_check": "passed"
            }
        except Exception as e:
            return {"status": "error", "message": f"Failed to start service: {e}"}

    async def stop_service(self, service_id: str) -> Dict[str, Any]:
        """Stop a managed service by ID."""
        services = self._load_services()
        service = next((s for s in services if s["service_id"] == service_id), None)
        
        if not service:
            return {"status": "error", "message": f"Service ID {service_id} not found in managed list."}

        pid = service["pid"]
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            # Give it a moment to die
            time.sleep(0.5)
        except ProcessLookupError:
            pass # Already dead
        except Exception as e:
            logger.warning(f"Failed to kill process group {pid}: {e}")
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass

        # Cleanup state
        services = [s for s in services if s["service_id"] != service_id]
        self._save_services(services)

        return {"status": "success", "message": f"Service {service_id} stopped."}

    async def kill_port(self, port: Any) -> Dict[str, Any]:
        """Forcefully free a port by killing whatever process occupies it.

        U-4 fix: Used by call_subordinate cleanup when a subordinate returns
        PARTIAL/CANCELLED and leaves a dev server running on an allocated port.
        Works for both managed and unmanaged processes.

        Args:
            port: The port number to free.

        Returns:
            Dict with status and details of what was killed.
        """
        try:
            port = int(port)
        except (ValueError, TypeError):
            return {"status": "error", "message": f"Invalid port: {port}"}

        # Find PID(s) listening on the port
        try:
            cmd = f"lsof -ti :{port}"
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
            pids_raw = proc.stdout.strip()
            if not pids_raw:
                return {
                    "status": "success",
                    "message": f"Port {port} is already free (no process found).",
                    "port": port,
                    "killed": [],
                }

            pids = list(set(pids_raw.split("\n")))  # Deduplicate
            killed = []
            for pid_str in pids:
                try:
                    pid = int(pid_str.strip())
                    # Try process group kill first (catches child processes)
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        os.kill(pid, signal.SIGKILL)
                    killed.append(pid)
                except (ProcessLookupError, OSError, ValueError):
                    pass  # Already dead or invalid

            # Clean up managed service state for this port
            services = self._load_services()
            cleaned = [s for s in services if s.get("port") != port and s.get("port") != str(port)]
            if len(cleaned) < len(services):
                self._save_services(cleaned)
                logger.info(f"[SERVICES_MGT] kill_port: removed managed service entry for port {port}")

            logger.info(f"[SERVICES_MGT] kill_port: freed port {port} (killed PIDs: {killed})")
            return {
                "status": "success",
                "message": f"Port {port} freed. Killed {len(killed)} process(es).",
                "port": port,
                "killed": killed,
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": f"Timeout checking port {port}"}
        except Exception as e:
            return {"status": "error", "message": f"Failed to kill port {port}: {e}"}

    async def restart_service(self, service_id: str = None, port: Any = None) -> Dict[str, Any]:
        """Restart a managed service: stop → ensure port free → clear .next cache → re-start.

        Fixes the stale webpack cache issue where file modifications after
        dev server startup cause MODULE_NOT_FOUND errors on all routes.

        RCA-FIX: The original implementation deleted .next cache BEFORE confirming
        the port was free. When stop_service failed to kill child processes (e.g.,
        next-server in a separate process group), the old server continued running
        with a deleted CSS cache — serving HTML but 404ing on CSS files. The fix:
        1. stop_service (graceful SIGTERM)
        2. kill_port as fallback (SIGKILL all listeners)
        3. Wait for port release with retry
        4. ONLY THEN delete .next cache
        5. Re-start fresh

        Can identify service by either service_id or port.
        """
        services = self._load_services()

        # Resolve by service_id or port
        service = None
        if service_id:
            service = next((s for s in services if s["service_id"] == service_id), None)
        elif port:
            try:
                port_int = int(port)
                service = next((s for s in services if s.get("port") == port_int or s.get("port") == str(port_int)), None)
            except (ValueError, TypeError):
                pass

        if not service:
            # ITR-29d: Improved error message — guide agent to start_service
            # as fallback. After Docker restart, state file is wiped so all
            # stored service_ids become stale.
            return {
                "status": "error",
                "message": (
                    f"Service not found (service_id={service_id}, port={port}). "
                    f"The service registry may have been cleared after a restart. "
                    f"Use action='start_service' to start the service fresh instead "
                    f"of restart_service. If a port is busy, use action='kill_port' first."
                ),
            }

        original_command = service.get("command", "")
        original_port = service.get("port")
        original_name = service.get("name", "service")
        original_id = service.get("service_id")

        # Step 1: Stop existing service (graceful SIGTERM)
        logger.info(f"[SERVICES_MGT] restart_service: stopping {original_name} (PID {service.get('pid')})")
        stop_result = await self.stop_service(original_id)
        if stop_result.get("status") == "error":
            logger.warning(f"[SERVICES_MGT] restart_service: stop failed: {stop_result}")
            # Continue — process may already be dead

        # Step 2: RCA-FIX — Force-kill port as fallback.
        # stop_service uses os.killpg(SIGTERM) which may not reach child processes
        # (e.g., next-server spawned by 'next dev'). kill_port uses lsof + SIGKILL
        # to ensure ALL listeners are dead.
        await asyncio.sleep(0.5)  # Brief pause for SIGTERM to take effect
        try:
            port_check = await self.check_port(original_port)
            if port_check.get("state") == "busy":
                logger.warning(
                    f"[SERVICES_MGT] restart_service: port {original_port} still busy after stop. "
                    f"Force-killing via kill_port..."
                )
                kill_result = await self.kill_port(original_port)
                logger.info(f"[SERVICES_MGT] restart_service: kill_port result: {kill_result}")
        except Exception as e:
            logger.warning(f"[SERVICES_MGT] restart_service: kill_port fallback failed: {e}")

        # Step 3: Wait for port release with retry (up to 5s)
        port_free = False
        for attempt in range(10):
            await asyncio.sleep(0.5)
            try:
                check = await self.check_port(original_port)
                if check.get("state") != "busy":
                    port_free = True
                    logger.info(
                        f"[SERVICES_MGT] restart_service: port {original_port} "
                        f"confirmed free after {(attempt + 1) * 0.5:.1f}s"
                    )
                    break
            except Exception:
                pass

        if not port_free:
            logger.error(
                f"[SERVICES_MGT] restart_service: port {original_port} still busy "
                f"after 5s of retries. Restart may fail with EADDRINUSE."
            )

        # Step 4: Clear .next cache ONLY AFTER port is confirmed free.
        # RCA-FIX: Previously this happened before port verification, causing
        # the old server (with deleted CSS) to continue serving unstyled pages.
        project_cwd = self._resolve_project_cwd()
        if project_cwd:
            next_cache = os.path.join(project_cwd, ".next")
            if os.path.isdir(next_cache):
                try:
                    shutil.rmtree(next_cache)
                    logger.info(f"[SERVICES_MGT] restart_service: cleared .next cache at {next_cache}")
                except Exception as e:
                    logger.warning(f"[SERVICES_MGT] restart_service: failed to clear .next: {e}")

        # Step 5: Re-start with the original command and port
        logger.info(f"[SERVICES_MGT] restart_service: re-starting {original_name} on port {original_port}")
        start_result = await self.start_service(
            command=original_command,
            port=original_port,
            name=original_name,
        )

        # Reset file write counter (used by _14_dev_server_restart_hook)
        if self.agent:
            self.agent.data["_file_writes_since_restart"] = 0

        return {
            "status": start_result.get("status", "success"),
            "message": f"Service '{original_name}' restarted on port {original_port}. "
                       f".next cache cleared. Health: {start_result.get('health_check', 'unknown')}",
            "service_id": start_result.get("service_id", original_id),
            "pid": start_result.get("pid"),
            "port": original_port,
            "cache_cleared": True,
            "port_freed": port_free,
            "health_check": start_result.get("health_check", "unknown"),
        }

    # ── Error-pattern regexes for 'errors' filter mode ──
    _ERROR_PATTERNS = [
        re.compile(r"Error", re.IGNORECASE),
        re.compile(r"Exception", re.IGNORECASE),
        re.compile(r"FATAL", re.IGNORECASE),
        re.compile(r"\bfailed\b", re.IGNORECASE),
        re.compile(r"ENOENT", re.IGNORECASE),
        re.compile(r"Cannot find module", re.IGNORECASE),
        re.compile(r"MODULE_NOT_FOUND", re.IGNORECASE),
        re.compile(r"\b500\b"),
        re.compile(r"ECONNREFUSED"),
        re.compile(r"SIGTERM|SIGKILL|SIGSEGV"),
    ]

    async def get_service_logs(
        self,
        port: Any = None,
        service_id: str = None,
        lines: int = 30,
        filter_mode: str = None,
        log_path: str = None,
    ) -> Dict[str, Any]:
        """Read dev server logs with middle-out strategy or error grep.

        Default (middle-out): Returns first N + last N lines, omitting the
        middle (webpack/HMR noise). This captures startup errors AND recent
        request errors in a single call.

        filter_mode='errors': Grep the entire log for error patterns
        (Error, Exception, FATAL, etc.) and return matching lines with
        ±3 lines of context.

        Args:
            port: Service port (resolves to /tmp/services_mgt_stderr_{port}.log)
            service_id: Alternative to port for service lookup
            lines: Number of lines to show from head/tail (default 30)
            filter_mode: None for middle-out, 'errors' for grep mode
            log_path: Direct path to log file (for testing)
        """
        try:
            lines = int(lines)
        except (ValueError, TypeError):
            lines = 30

        # Resolve log path
        resolved_port = None
        if not log_path:
            resolved_port = None
            if port:
                try:
                    resolved_port = int(port)
                except (ValueError, TypeError):
                    pass
            elif service_id:
                services = self._load_services()
                svc = next((s for s in services if s["service_id"] == service_id), None)
                if svc:
                    resolved_port = svc.get("port")

            if not resolved_port:
                return {
                    "status": "error",
                    "message": "Cannot resolve log path: provide port or service_id.",
                }
            log_path = f"/tmp/services_mgt_stderr_{resolved_port}.log"

        if not os.path.isfile(log_path):
            return {
                "status": "error",
                "message": f"Log file not found: {log_path}. "
                           f"Service may not have been started via services_mgt.",
            }

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except Exception as e:
            return {"status": "error", "message": f"Failed to read log: {e}"}

        if not all_lines:
            return {
                "status": "success",
                "message": "Log file is empty — service may not have produced output yet.",
                "log_output": "",
                "total_lines": 0,
            }

        total = len(all_lines)

        # ── Errors grep mode ──
        if filter_mode == "errors":
            return self._grep_errors(all_lines, total, log_path)

        # ── Default: middle-out ──
        result = self._middle_out(all_lines, total, lines, log_path)

        # RCA-365 F-6: Also surface stdout log path if it exists.
        # stdout was previously discarded (DEVNULL); now captured to a file.
        if resolved_port:
            stdout_log_path = f"/tmp/services_mgt_stdout_{resolved_port}.log"
            if os.path.isfile(stdout_log_path):
                result["stdout_log_path"] = stdout_log_path
                result["message"] = (
                    result.get("message", "")
                    + f" (stdout also available at {stdout_log_path})"
                )
        return result

    def _middle_out(self, all_lines, total, lines, log_path):
        """Return first N + last N lines, omitting the middle."""
        if total <= lines * 2:
            # Short log — return everything
            output = "".join(all_lines)
            return {
                "status": "success",
                "message": f"Full log ({total} lines) from {log_path}",
                "log_output": output.strip(),
                "total_lines": total,
            }

        head = "".join(all_lines[:lines])
        tail = "".join(all_lines[-lines:])
        omitted = total - (lines * 2)
        output = (
            f"=== First {lines} lines (startup) ===\n"
            f"{head}"
            f"\n=== ... {omitted} lines omitted ... ===\n\n"
            f"=== Last {lines} lines (recent) ===\n"
            f"{tail}"
        )
        return {
            "status": "success",
            "message": f"Middle-out log ({total} lines, {omitted} omitted) from {log_path}",
            "log_output": output.strip(),
            "total_lines": total,
            "omitted_lines": omitted,
        }

    def _grep_errors(self, all_lines, total, log_path):
        """Grep log for error patterns, return matches with ±3 context."""
        context_radius = 3
        matched_indices = set()

        for i, line in enumerate(all_lines):
            for pattern in self._ERROR_PATTERNS:
                if pattern.search(line):
                    # Add this line + context
                    for j in range(max(0, i - context_radius),
                                   min(total, i + context_radius + 1)):
                        matched_indices.add(j)
                    break

        if not matched_indices:
            return {
                "status": "success",
                "message": f"No error patterns found in {total} lines from {log_path}",
                "log_output": "(no errors detected)",
                "total_lines": total,
                "error_count": 0,
            }

        sorted_indices = sorted(matched_indices)
        output_parts = []
        prev_idx = -2
        for idx in sorted_indices:
            if idx > prev_idx + 1:
                output_parts.append("---")
            output_parts.append(f"{idx + 1:4d} | {all_lines[idx].rstrip()}")
            prev_idx = idx

        output = "\n".join(output_parts)
        error_lines = sum(
            1 for i in sorted_indices
            if any(p.search(all_lines[i]) for p in self._ERROR_PATTERNS)
        )
        return {
            "status": "success",
            "message": f"Found {error_lines} error lines in {total} total lines from {log_path}",
            "log_output": output,
            "total_lines": total,
            "error_count": error_lines,
        }

    async def list_services(self) -> Dict[str, Any]:
        """List all managed services and their liveness.
        
        Auto-prunes dead services older than AUTO_PRUNE_AGE_THRESHOLD
        to prevent unbounded registry growth.
        """
        services = self._load_services()
        active_services = []
        pruned_count = 0
        now = time.time()
        
        for s in services:
            # Check if PID still exists
            try:
                os.kill(s["pid"], 0)
                s["status"] = "running"
                active_services.append(s)
            except (ProcessLookupError, OSError):
                s["status"] = "stopped"
                # Auto-prune: remove dead entries older than threshold
                started_at = s.get("started_at", "")
                try:
                    started_ts = time.mktime(time.strptime(started_at, "%Y-%m-%d %H:%M:%S"))
                    age = now - started_ts
                except (ValueError, OverflowError):
                    age = float('inf')  # Can't parse → treat as old
                
                if age > AUTO_PRUNE_AGE_THRESHOLD:
                    pruned_count += 1
                    logger.info(
                        f"[SERVICES_MGT] Auto-pruned dead service '{s.get('name')}' "
                        f"(PID {s.get('pid')}, age {age:.0f}s)"
                    )
                else:
                    # Keep recently-stopped services for visibility
                    active_services.append(s)

        if pruned_count > 0:
            self._save_services(active_services)
            logger.info(f"[SERVICES_MGT] Auto-pruned {pruned_count} dead services from registry")

        return {"status": "success", "services": active_services, "pruned_count": pruned_count}

    async def test_service(self, port: Any) -> Dict[str, Any]:
        """Test a service using curl (within agix environment).
        
        Uses a 10s timeout to prevent hanging the agent processing loop
        when a broken dev server accepts TCP but never responds.
        (Iteration 6 RCA — P0 fix)
        """
        try:
            port = int(port)
            # Use curl with --max-time to prevent curl-level hangs,
            # plus subprocess timeout as a safety net
            cmd = f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 8 http://localhost:{port}"
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            http_code = proc.stdout.strip()
            
            if http_code and http_code != "000":
                return {
                    "status": "success",
                    "http_code": int(http_code),
                    "message": f"Service on port {port} returned HTTP {http_code}"
                }
            else:
                return {
                    "status": "error",
                    "message": f"Service on port {port} is not reachable or returned no output."
                }
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "message": f"Service on port {port} timed out after 10s. Server may be hanging."
            }
        except Exception as e:
            return {"status": "error", "message": f"Test failed: {e}"}

    def _resolve_project_cwd(self) -> Optional[str]:
        """Resolve the active project directory from the agent's context.
        
        Uses the same resolution chain as the orchestrator gate:
        1. Explicit _active_project_dir
        2. active_project dict with 'path'
        3. active_project string name → constructed path
        
        Returns:
            Absolute path to the project directory, or None.
        """
        if not self.agent:
            return None
        
        agent_data = getattr(self.agent, 'data', {})
        
        # Priority 1: Explicitly set _active_project_dir
        explicit = agent_data.get("_active_project_dir", "")
        if explicit:
            return explicit
        
        # Priority 2: active_project dict with 'path' key
        active_project = agent_data.get("active_project")
        if isinstance(active_project, dict) and active_project.get("path"):
            return active_project["path"]
        
        # Priority 3: active_project as string name
        if isinstance(active_project, str) and active_project:
            return os.path.join("/agix/usr/projects", active_project)
        
        # Priority 4: Try context-based resolution
        try:
            from python.helpers import projects as projects_helper
            context = getattr(self.agent, 'context', None)
            if context:
                project_name = projects_helper.get_context_project_name(context)
                if project_name:
                    project_dir = projects_helper.get_project_folder(project_name)
                    if os.path.isdir(project_dir):
                        return project_dir
        except Exception as e:
            logger.warning(f"[SERVICES] Project resolution failed: {e}")
        
        return None
    
    def _ensure_cd_prefix(self, command: str, project_dir: str) -> str:
        """Ensure the command starts with cd <project_dir> if needed.
        
        If the command already cd's into the project dir (or a subdirectory),
        leave it alone. If it cd's to the wrong directory, replace it.
        If no cd is present, prepend one.
        
        Args:
            command: The shell command string
            project_dir: The target project directory path
            
        Returns:
            The command with a correct cd prefix.
        """
        import re
        
        # Check if command already has a cd to the right place
        cd_pattern = r'^cd\s+([^\s;&|]+)'
        cd_match = re.match(cd_pattern, command.strip())
        
        if cd_match:
            cd_target = cd_match.group(1).strip("'\"")
            # If cd target is inside the project dir, leave it alone
            if cd_target.startswith(project_dir):
                return command
            # If cd target is wrong, replace it
            rest = command[cd_match.end():].lstrip()
            # Strip the && or ; separator
            rest = re.sub(r'^[;&|]+\s*', '', rest)
            return f"cd {project_dir} && {rest}"
        
        # No cd present — prepend
        return f"cd {project_dir} && {command}"
    
    def _auto_allocate_port(self, service_name: str = "main") -> Any:
        """Auto-allocate a port via PortManager.
        
        Uses the agent's project context to derive a project name for
        hash-based, cross-project-aware port allocation.
        
        Returns:
            int: allocated port number
            dict: error dict if allocation fails
        """
        try:
            from python.helpers.port_manager import PortManager
            
            # Derive project name from agent context
            project_name = "unknown"
            if self.agent:
                # Try active_project from agent data
                active_project = getattr(self.agent, 'data', {}).get('active_project')
                if isinstance(active_project, dict) and active_project.get('name'):
                    project_name = active_project['name']
                elif isinstance(active_project, str) and active_project:
                    project_name = active_project
                else:
                    # Try context-based resolution
                    try:
                        from python.helpers import projects as projects_helper
                        context = getattr(self.agent, 'context', None)
                        if context:
                            ctx_name = projects_helper.get_context_project_name(context)
                            if ctx_name:
                                project_name = ctx_name
                    except Exception as e:
                        logger.warning(f"[SERVICES] Project name resolution failed: {e}")
            
            # Determine service type from name
            service_type = "frontend"
            if any(kw in service_name.lower() for kw in ["backend", "api", "server"]):
                service_type = "backend"
            
            pm = PortManager()
            # Issue #1098: Clean up stale entries (dead PIDs) before allocating.
            # After context compression, the agent forgets which port was used
            # and tries to allocate a new one. Cleanup ensures dead processes
            # don't hold phantom port registrations.
            try:
                cleaned = pm.cleanup_stale_services()
                if cleaned:
                    logger.info(
                        f"[SERVICES_MGT] Cleaned {len(cleaned)} stale service(s) "
                        f"before allocation: {cleaned}"
                    )
            except Exception as cleanup_err:
                logger.warning(f"[SERVICES_MGT] Stale cleanup failed: {cleanup_err}")
            
            # Check if this project already has an existing port allocation
            existing_port = pm.get_port(project_name, service_name)
            if existing_port is not None:
                # Re-use the existing allocation instead of getting a new port
                logger.info(
                    f"[SERVICES_MGT] Reusing existing port {existing_port} for "
                    f"project={project_name}, service={service_name} (from ports.json)"
                )
                return existing_port
            
            port = pm.allocate_port(
                project_name=project_name,
                service_name=service_name,
                service_type=service_type,
            )
            logger.info(
                f"[SERVICES_MGT] PortManager allocated port {port} for "
                f"project={project_name}, service={service_name}, type={service_type}"
            )
            return port
            
        except Exception as e:
            logger.error(f"[SERVICES_MGT] PortManager allocation failed: {e}")
            return {
                "status": "error",
                "message": f"Port auto-allocation failed: {e}. "
                           f"Please specify a port explicitly."
            }

    def _find_managed_service(self, port: int) -> Optional[Dict[str, Any]]:
        """Check if a port is occupied by a service we previously started.
        
        Returns the service entry if found, None otherwise.
        Used for port-reuse logic to prevent duplicate server spawns
        after context compression.
        """
        services = self._load_services()
        for svc in services:
            if int(svc.get("port", 0)) == port:
                # Verify the PID is still alive
                pid = svc.get("pid")
                if pid:
                    try:
                        os.kill(int(pid), 0)  # Signal 0 = check existence
                        return svc
                    except (OSError, ProcessLookupError):
                        # PID is dead — service entry is stale
                        logger.debug(
                            f"[SERVICES_MGT] Stale service entry for port {port} "
                            f"(PID {pid} is dead)"
                        )
                        continue
        return None

    def _load_services(self) -> List[Dict[str, Any]]:
        state_path = os.path.join(os.getcwd(), self.state_file)
        if os.path.exists(state_path):
            try:
                with open(state_path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[SERVICES] State file corrupted, resetting: {e}")
                return []
        return []

    def _save_services(self, services: List[Dict[str, Any]]):
        state_path = os.path.join(os.getcwd(), self.state_file)
        try:
            with open(state_path, "w") as f:
                json.dump(services, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save services state: {e}")

    async def prune_stopped_services(self, state_path: str = None) -> Dict[str, Any]:
        """Explicitly prune all stopped (dead PID) services from the registry.
        
        Args:
            state_path: Override state file path (for testing). Defaults to
                        managed_services.json in CWD.
        
        Returns:
            dict with pruned_count and remaining services.
        """
        if state_path is None:
            state_path = os.path.join(os.getcwd(), self.state_file)
        
        if not os.path.exists(state_path):
            return {"status": "success", "pruned_count": 0, "remaining": 0}
        
        try:
            with open(state_path, "r") as f:
                services = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[SERVICES] State file corrupted, resetting: {e}")
            return {"status": "success", "pruned_count": 0, "remaining": 0}
        
        alive = []
        pruned = 0
        for s in services:
            try:
                os.kill(s["pid"], 0)
                alive.append(s)  # Still running
            except (ProcessLookupError, OSError):
                pruned += 1
                logger.info(
                    f"[SERVICES_MGT] Pruned dead service '{s.get('name')}' "
                    f"(PID {s.get('pid')}, port {s.get('port')})"
                )
        
        try:
            with open(state_path, "w") as f:
                json.dump(alive, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save pruned services state: {e}")
        
        return {
            "status": "success",
            "pruned_count": pruned,
            "remaining": len(alive),
            "message": f"Pruned {pruned} stopped services, {len(alive)} still running.",
        }


def cleanup_services_for_project(project_dir: str, state_path: str = None) -> int:
    """Remove all service registry entries whose command references the given project directory.
    
    Called by LifecycleService.delete_project() to prevent orphaned service
    entries from accumulating after project deletion.
    
    Also kills any running processes for those services.
    
    Args:
        project_dir: Absolute path to the project directory being deleted.
        state_path: Override state file path (for testing).
    
    Returns:
        Number of entries removed.
    """
    if state_path is None:
        state_path = os.path.join(os.getcwd(), DEFAULT_STATE_FILE)
    
    if not os.path.exists(state_path):
        return 0
    
    try:
        with open(state_path, "r") as f:
            services = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[SERVICES] State file corrupted, resetting: {e}")
        return 0
    
    # Normalize for matching
    project_dir_normalized = project_dir.rstrip("/")
    
    keep = []
    removed = 0
    for s in services:
        cmd = s.get("command", "")
        if project_dir_normalized in cmd:
            removed += 1
            # Try to kill the process if still running
            pid = s.get("pid")
            if pid:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except (ProcessLookupError, OSError, PermissionError):
                    pass  # Already dead or not ours
            logger.info(
                f"[SERVICES_MGT] Cleaned up service '{s.get('name')}' "
                f"(PID {pid}, port {s.get('port')}) for deleted project {project_dir}"
            )
        else:
            keep.append(s)
    
    if removed > 0:
        try:
            with open(state_path, "w") as f:
                json.dump(keep, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save cleaned services state: {e}")
    
    return removed
