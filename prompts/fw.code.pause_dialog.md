Potential dialog detected in output. Returning control to agent after {{timeout}} seconds since last output update.

**If this is a real interactive prompt** (e.g., "Ok to proceed? (y)", "Y/N", password entry):
→ Use the **`input`** tool to respond. Set `keyboard` to your answer (e.g., "y", "yes", "n") and `session` to the same terminal session number.

**If this is a false positive** (e.g., output just ended with a colon or question mark but no input is needed):
→ Use `code_execution_tool` with `runtime: "output"` and the same `session` to check for more output.

**IMPORTANT**: After sending input via the `input` tool, the terminal output (including any new menus, prompts, or results) will be returned to you automatically. Read the response carefully to determine your next action.

**Prevention**: Prefer non-interactive flags (`--yes`, `-y`, `--no-input`, `--non-interactive`, `CI=true`) when available to avoid interactive prompts entirely.