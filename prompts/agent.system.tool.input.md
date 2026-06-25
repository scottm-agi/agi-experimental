### input:
Send keyboard input to a running terminal program.
Use when a terminal command is waiting for interactive input (Y/N prompts, password entry, menu selections, confirmations).

- `keyboard`: The text to type (e.g., "y", "yes", "n", "1", a password)
- `session`: Terminal session number (default 0) — must match the session where the dialog appeared

**Output**: After sending input, the tool returns the terminal output (new menus, confirmations, error messages, etc.) so you can read and respond to any follow-up prompts.

**When to use**: After `code_execution_tool` returns with a dialog/prompt detected (e.g., "Ok to proceed? (y)"), use this tool to answer it.

**Prevention**: Prefer `--yes`, `-y`, `--non-interactive`, `CI=true` flags on commands to avoid interactive prompts entirely.

usage:
~~~json
{
    "thoughts": [
        "The program asks for Y/N...",
    ],
    "headline": "Responding to terminal program prompt",
    "tool_name": "input",
    "tool_args": {
        "keyboard": "Y",
        "session": 0
    }
}
~~~
