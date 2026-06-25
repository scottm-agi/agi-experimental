### code_execution_tool

execute terminal commands python nodejs code for computation or software tasks
place code in "code" arg; escape carefully and indent properly
select "runtime" arg: "terminal" "python" "nodejs" "output" "reset"
select "session" number, 0 default, others for multitasking
if code runs long, use "output" to wait, "reset" to kill process
use "pip" "npm" "apt-get" in "terminal" to install packages
to output, use print() or console.log()
if tool outputs error, adjust code before retrying; 
important: check code for placeholders or demo data; replace with real variables; don't reuse snippets
don't use with other tools except thoughts; wait for response before using others
check dependencies before running code
output may end with [SYSTEM: ...] information comming from framework, not terminal

**🔴 TRUNCATION RECOVERY — READ FULL ERROR OUTPUT**:
When output is too large, it is automatically truncated with a marker like:
`[... N characters omitted — full output saved to /tmp/last_cmd_output.log — use cat /tmp/last_cmd_output.log to see complete output ...]`
When you see this marker and need the full error details (e.g., to diagnose a build failure):
1. **Preferred**: Use `runtime: "full_output"` to retrieve the saved un-truncated output:
   `{"tool_name": "code_execution_tool", "tool_args": {"runtime": "full_output"}}`
2. **Alternative**: Run `cat /tmp/last_cmd_output.log` in terminal to read it directly.
3. **For service logs**: Use `services_mgt` with `action: "get_service_logs"` to read dev server stderr/stdout.
Do NOT guess at errors hidden by truncation — always retrieve the full output first.


**🔴 NON-INTERACTIVE EXECUTION (DEFAULT — ADR-82)**:
Commands run non-interactively by default — stdin is closed, so ALL CLI tools
skip interactive prompts automatically. No special flags needed:
- `npx create-next-app` — Turbopack prompt auto-skipped
- `npx create-vite` — template selection auto-skipped
- `npm install` — no confirmation prompts
- `shadcn init` — uses defaults
Still use `npx -y` for the "Ok to proceed?" package install prompt (auto-injected).

**INTERACTIVE MODE (rare — opt-in only)**:
Set `"interactive": true` ONLY when you need to:
1. Run database CLIs (psql, mysql, redis-cli) that require session input
2. Authenticate git with credentials
3. Run interactive REPLs (python -i, node)
4. Use the `input` tool to send keystrokes
Example: `{"runtime": "terminal", "interactive": true, "code": "psql -U myuser"}`

**🔴 INTERACTIVE PROMPT RECOVERY (interactive mode only)**:
If output shows a prompt waiting for input (e.g., `? ... (y/N)`, `Ok to proceed?`, `Select ...`):
1. Use the **`input`** tool to send the answer: `{"tool_name": "input", "tool_args": {"keyboard": "y", "session": 0}}`
2. Do NOT use `code_execution_tool` with `runtime: "reset"` — that kills the process. Send input first.
3. After sending input, use `code_execution_tool` with `runtime: "output"` to check progress.

**DO NOT run `grit apply`, `grit check`, or raw GritQL CLI — use the grit_transform tool instead.**
**DO NOT run `ast-grep` or `sg` CLI for Python symbol search — use ast_symbol_search tool instead.**
**DO NOT run `npx create-next-app`, `npx create-vite`, `npm init`, or any scaffolding command directly without the required non-interactive flags.** Always include `--typescript --eslint --app --src-dir --use-npm --no-git` for create-next-app. The system auto-injects `npx -y` but additional flags are your responsibility.
**DO NOT run `npm run dev`, `next dev`, or `vite` directly.** Use the `services_mgt` tool instead (`action: start_service`) — it handles port allocation (5100+), health checks, and lifecycle management. Raw dev commands bind to unmapped ports and create orphan processes.
**DO NOT use `cat`, `head`, `tail`, `less`, or `more` to read source files.** Use the `read_file` tool instead — it supports reading multiple files in a single call via the `files` array parameter. Example: `{"files": [{"path": "src/lib/prisma.ts"}, {"path": "src/types/index.ts"}]}`. Shell-based file reading wastes context tokens and bypasses the framework's file-tracking system.

**🔴 BACKGROUND PROCESS CLEANUP (CRITICAL — Prevents OOM)**:
After starting a dev server for verification (via `services_mgt` tool):
1. **ALWAYS stop the server after verification is complete** — use `services_mgt` with `action: "stop_service"` and the service name. NEVER use `pkill` or `kill` for managed services.
2. **NEVER leave backgrounded processes running** — orphan `npm run dev &`, `next dev &`, or `node server.js &` processes accumulate RAM and cause container OOM kills.
3. **Verification pattern**: Start server → curl health check → stop server → report results. Do NOT leave the server running "for later".
4. **If you used `&` to background a process**, you MUST `kill` it before calling `response`.

**MANDATORY: Use ripgrep (rg) for ALL code/file searches. NEVER use grep -r.**
`grep -r` is slow, lacks smart defaults, and doesn't respect .gitignore. Use `rg` exclusively.

**Default Exclusions (ALWAYS applied, never omit)**:
```
rg "pattern" path/ \
  -g '!node_modules' -g '!__pycache__' -g '!.venv' -g '!venv' \
  -g '!.next' -g '!.nuxt' -g '!dist' -g '!build' -g '!.cache' \
  -g '!.git' -g '!.agix.proj' \
  -g '!*.pkl' -g '!*.tsbuildinfo' -g '!*.lock' -g '!*.map' \
  -g '!*.pyc' -g '!*.min.js' -g '!*.min.css' -g '!*.chunk.js'
```
These exclusions prevent false positives from build caches, binary files, and vendored dependencies.

**Best Practice — Search Source Code Directly**: When looking for imports, exports, or code patterns, target `src/` or `app/` directly with type filters:
```
rg "pattern" src/ -t ts -t tsx -t js -t jsx
rg "pattern" app/ -t py
```
Do NOT search the project root without explicit path targeting — build artifacts WILL pollute results.

**System Directory Exclusions**: For system-wide searches, ALWAYS exclude:
`rg "pattern" / -g '!/proc' -g '!/dev' -g '!/sys' -g '!/run'`

**Dependency Change Protocol**:
After ANY dependency change (`rm -rf node_modules`, `npm install`, `npm ci`, `npm update`):
1. Kill any running dev server for this project
2. Reinstall: `npm install --legacy-peer-deps`
3. Restart dev server using `services_mgt` tool (`action: start_service`)
4. Verify health: `curl -sf http://0.0.0.0:PORT` returns 200 (get PORT from `services_mgt list_services`)
NEVER proceed with route/page verification until step 4 succeeds. A dead dev server will return 500 for ALL routes — this is NOT a code bug, it is an infrastructure issue.

usage:

1 execute python code

~~~json
{
    "thoughts": [
        "Need to do...",
        "I can use...",
        "Then I can...",
    ],
    "headline": "Executing Python code to check current directory",
    "tool_name": "code_execution_tool",
    "tool_args": {
        "runtime": "python",
        "session": 0,
        "code": "import os\nprint(os.getcwd())",
    }
}
~~~

2 execute terminal command
~~~json
{
    "thoughts": [
        "Need to do...",
        "Need to install...",
    ],
    "headline": "Installing zip package via terminal",
    "tool_name": "code_execution_tool",
    "tool_args": {
        "runtime": "terminal",
        "session": 0,
        "code": "apt-get install zip",
    }
}
~~~

2.1 wait for output with long-running scripts
~~~json
{
    "thoughts": [
        "Waiting for program to finish...",
    ],
    "headline": "Waiting for long-running program to complete",
    "tool_name": "code_execution_tool",
    "tool_args": {
        "runtime": "output",
        "session": 0,
    }
}
~~~

2.2 reset terminal
~~~json
{
    "thoughts": [
        "code_execution_tool not responding...",
    ],
    "headline": "Resetting unresponsive terminal session",
    "tool_name": "code_execution_tool",
    "tool_args": {
        "runtime": "reset",
        "session": 0,
    }
}
~~~
