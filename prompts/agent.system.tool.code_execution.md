### code_execution_tool

execute terminal commands python nodejs code for computation or software tasks
place code in "code" arg; escape carefully and indent properly
select "runtime" arg: "terminal" "python" "nodejs" "output" "reset"

**Node.js Runtime Constraints (CRITICAL — RCA-301)**:
- The `nodejs` runtime uses **CommonJS** (`require`/`module.exports`). ESM `import` statements will FAIL with `SyntaxError: Cannot use import statement outside a module`.
- Use `const fs = require('fs')` NOT `import fs from 'fs'`
- For TypeScript/ESM code: use `write_to_file` to create the `.ts` file, then run with `npx tsx filename.ts` via `terminal` runtime
- Do NOT run full application service code in the REPL. Write application code to project files with `write_to_file`, then execute via `terminal`.

DO NOT use code_execution_tool to write custom Playwright or Selenium scripts. 
Always use browser_agent for all web interaction and browser-based tasks.
browser_agent provides robust session management and automatic screenshot capture.

**DO NOT use code_execution_tool to run `grit apply`, `grit check`, or any raw GritQL CLI commands.**
Always use the **grit_transform** tool for AST-aware code transformations — it wraps the CLI with proper error handling and output parsing.

**DO NOT use code_execution_tool to run `ast-grep` or `sg` CLI commands for Python symbol search.**
Always use the **ast_symbol_search** tool for Python symbol discovery — it provides structured results.
select "session" number, 0 default, others for multitasking
if code runs long, use "output" to wait, "reset" to kill process
use "pip" "npm" "apt-get" in "terminal" to install packages

**Interactive Prompts — PREVENTION FIRST**:
- ALWAYS use non-interactive flags when available: `--yes`, `-y`, `--no-input`, `--non-interactive`, `CI=true`
  - Examples: `npx -y create-next-app`, `npm init -y`, `npx prisma init --url ...`, `apt-get install -y`
- If a command produces an interactive prompt (Y/N, password, menu selection), use the **`input`** tool to respond:
  - Set `keyboard` to your response (e.g., "y", "yes", a menu number)
  - Set `session` to the same terminal session number
  - The terminal output after your input is returned automatically — read it to determine next steps
to output, use print() or console.log()
if tool outputs error, adjust code before retrying;
- important: check code for placeholders or demo data; replace with real variables; don't reuse snippets
- IMPORTANT: DO NOT double braces in code (e.g. use {variable} in f-strings, not {{variable}}) unless literally intended as a brace in the output.
- **ENCODING (Forgejo #892)**: Use only ASCII characters in generated Python code. Do NOT use Unicode arrows (→), math symbols (×, ÷), accented characters (é, ö), or emoji in code, comments, or string literals. Use ASCII equivalents instead (e.g., `->` not `→`, `*` not `×`).
- don't use with other tools except thoughts; wait for response before using others
check dependencies before running code
output may end with [SYSTEM: ...] information comming from framework, not terminal

**Search Tool Optimization (grep, rg)**: 
- ALWAYS exclude system directories: `/proc`, `/dev`, `/sys`, `/run` to avoid memory and permission errors.
- BIAS searches towards project directories (e.g., `/agix/usr/projects/...` or current project) for better relevance and speed.
- Do NOT limit searches strictly to projects unless requested; maintain flexibility for system-wide investigation.
- Use `--exclude-dir` or `-g` flags as appropriate.
- **Project Scoping**: When working within a project, prefer using the project-scoped path variable `{{PROJECT_PATH}}` (if available in context) to limit the search scope and improve performance.
- **Project-Scoped Work (CRITICAL)**: ALL work output (code, files, builds, repos) MUST be within `/agix/usr/projects/<project-name>/`. Do NOT write generated code or project files to `/agix/` root, `/tmp/`, or any other location. The only exceptions for editing files outside of projects are **safe system locations**: `/agix/agents/` (agent profiles), `/agix/skills/` (skills), `/agix/prompts/` (prompts), `/agix/knowledge/` (knowledge base).
- **Temporary Files (CRITICAL — RCA-323)**: For temporary files, backups, staging areas, or build artifacts, use a `tmp/` subdirectory **within your project** (e.g., `/agix/usr/projects/<project-name>/tmp/`). NEVER use system `/tmp/` — it is sandboxed and will be blocked. Create the project-local tmp directory with `mkdir -p` before use.
- **Absolute Paths in Container (CRITICAL)**: Always use absolute paths starting with `/agix/` (e.g., `/agix/data/config.db`, not `data/config.db`). The working directory varies between tool invocations and relative paths WILL fail unpredictably.
- **Directory Creation**: Explicitly create parent directories before writing files (e.g., `mkdir -p path/to/dir && cat > path/to/dir/file.py << 'EOF'...`). Tools like `cat` or `echo` do NOT create parent directories automatically and will fail if they don't exist.
- **Git Workflow Selection (CRITICAL — RCA-324)**: Before ANY git operation, examine the user's prompt and project context to determine the correct workflow. Follow this decision tree:

  **STEP 1 — Detect existing git state:**
  ```bash
  test -d /agix/usr/projects/<project-name>/.git && echo "HAS_GIT" || echo "NO_GIT"
  ```

  **STEP 2 — Analyze the user's prompt for intent:**
  - Does the prompt say "update", "fix", "modify", "patch", or "PR"? → **Existing code modification**
  - Does the prompt say "build", "create", "generate", "new app"? → **Net-new code**
  - Does the prompt reference an existing repo URL to push to? → **Push to existing**

  **STEP 3 — Select scenario from this table:**

  | Git State | User Intent | Scenario |
  |-----------|-------------|----------|
  | `NO_GIT` | Build new app / create new project | **A — Net-New** |
  | `NO_GIT` | Push to existing repo URL | **C — Push to Existing** |
  | `HAS_GIT` | Update / fix / modify / PR | **B — Modify Existing** |
  | `HAS_GIT` | Build new (prompt overrides) | **A — Net-New** (re-init) |

  **DEFAULT: If uncertain, use Scenario A** — `git init` + `git remote add` is always safe for net-new work. NEVER default to `git clone` for net-new projects.

  The THREE scenarios and their required patterns:

  **Scenario A — Net-New Project (no existing repo):**
  Build the project locally, then push:
  ```bash
  # Work inside the project directory
  git -C /agix/usr/projects/<project-name> init
  git -C /agix/usr/projects/<project-name> remote add origin <github-url>
  git -C /agix/usr/projects/<project-name> add .
  git -C /agix/usr/projects/<project-name> commit -m "Initial commit"
  git -C /agix/usr/projects/<project-name> branch -M main
  git -C /agix/usr/projects/<project-name> push -u origin main --force
  ```

  **Scenario B — Modify Existing Repo (fork, patch, PR):**
  Clone to a SEPARATE directory, make changes, push:
  ```bash
  git clone <repo-url> /agix/usr/projects/<project-name>/tmp/clone_target
  # Make changes inside the cloned directory
  git -C /agix/usr/projects/<project-name>/tmp/clone_target add .
  git -C /agix/usr/projects/<project-name>/tmp/clone_target commit -m "Fix: ..."
  git -C /agix/usr/projects/<project-name>/tmp/clone_target push origin <branch>
  ```

  **Scenario C — Push Locally-Built Code to Existing Repo:**
  Initialize git in the project directory, add the remote, force-push:
  ```bash
  git -C /agix/usr/projects/<project-name> init
  git -C /agix/usr/projects/<project-name> remote add origin <github-url>
  git -C /agix/usr/projects/<project-name> add .
  git -C /agix/usr/projects/<project-name> commit -m "Deploy: <project>"
  git -C /agix/usr/projects/<project-name> push -u origin main --force
  ```

  **FORBIDDEN PATTERNS (GitGuard will block these):**
  - `cd /path/to/dir && git add .` — GitGuard validates against process CWD, NOT shell `cd` target. Use `git -C <path>` instead.
  - `git clone <repo> /tmp/staging && rsync project/ /tmp/staging/ && cd /tmp/staging && git push` — The clone+rsync+cd pattern trips GitGuard path traversal detection. Use Scenario A or C instead.
  - NEVER clone an existing repo just to rsync your locally-built code into it. That's Scenario C, not B.

- **🔴 DESTRUCTIVE OPERATION GUARDRAIL (MSR-4)**: NEVER run `rm -rf` on directories containing source code (e.g., `services/`, `tests/`, `src/`, `python/`, `memory-bank/`). If a scaffolding tool (e.g., `create-next-app`, `npx create-vite`) requires an empty directory, scaffold in a NEW subdirectory and merge — do NOT move/delete existing files. If you need to reorganize files, use `git mv` or `cp` — NEVER `rm -rf` then recreate.
- **🔴 BLIND RETRY GUARD (MSR-24)**: If the same command or file write fails 2+ times in a row, STOP and diagnose the root cause before retrying. Do NOT blindly retry the same operation. Examine the error message, check dependencies, verify paths.
- **🔴 PRECONDITION VERIFICATION (RCA-315d.3)**: Before ANY file copy/move/rsync operation, ALWAYS verify the source path exists (e.g., `test -d /path && rsync ...` or `ls /path/` first). If the source does not exist, report the missing precondition and proceed to the next task — do NOT retry the same command hoping the source will materialize.
- **🔴 TERMINAL ERROR RECOGNITION (RCA-315d.5)**: When a command returns `No such file or directory`, `command not found`, or `Permission denied`, treat it as a **terminal condition**. Do NOT retry the exact same command. Instead: (1) diagnose WHY the file/command doesn't exist, (2) if it depends on a task that hasn't run yet, report the dependency gap and move on, (3) NEVER loop more than once on the same "not found" error.
- **PORT CHANGE NOTIFICATION (MSR-19)**: If you change a port number (e.g., from 3000 to 3001), you MUST explicitly inform the user AND update any documentation/config that references the old port.
- **🔴 DEV SERVER PROHIBITION (CRITICAL)**: NEVER use `code_execution_tool` to run dev server commands directly (`npm run dev`, `npx next dev`, `npx vite`, `python -m flask run`, etc.). ALL dev server lifecycle management MUST go through the `services_mgt` tool, which handles port allocation, process tracking, and health checks. Running dev servers via raw terminal commands causes port conflicts, orphan processes, and container OOM kills.
- **🔴 rm -rf PROHIBITION (RCA-333 — CRITICAL)**: NEVER use `rm -rf` via terminal for directory deletion. ALL recursive directory deletion MUST go through the **`recursive_delete`** tool, which enforces project scope boundaries and a programmatic allowlist. Terminal `rm -rf` commands are blocked by the system guard. The `recursive_delete` tool allows only known-safe build/cache directories (`.next`, `dist`, `build`, `tmp`, `coverage`, `.cache`, etc.) inside project directories. Project root deletion is ALWAYS blocked — users delete projects via the UI. If you need to remove a directory that is not on the allowlist, report the need to the user instead of attempting deletion.

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
