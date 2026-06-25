### maintain_memory_bank
Maintain the project's Memory Bank (Markdown files in `memory-bank/`).
This ensures high-level project context, active focus, and lessons learned are captured and retrievable.

**Modes:**
- `list`: Lists all files in the current memory bank. Use this to see what's available.
- `read`: Returns the full content of a specific file. Useful for resuming context.
- `append`: Adds content to the end of a file. Best for logging progress or new lessons.
- `overwrite`: Replaces the entire file content. Use for major updates to plans or patterns.

**Note:** The tool automatically targets the correct `memory-bank/` directory based on the active project. If no project is active, it uses the global memory bank at the system root.

**Usage Examples:**

**Listing files:**
~~~json
{
    "thoughts": ["I need to see what files are in the memory bank."],
    "tool_name": "maintain_memory_bank",
    "tool_args": {
        "mode": "list"
    }
}
~~~

**Reading a file:**
~~~json
{
    "thoughts": ["I'll read the techContext to understand the stack."],
    "tool_name": "maintain_memory_bank",
    "tool_args": {
        "file_name": "techContext.md",
        "mode": "read"
    }
}
~~~

**Appending a lesson:**
~~~json
{
    "thoughts": ["I learned that user passwords must be hashed before saving."],
    "tool_name": "maintain_memory_bank",
    "tool_args": {
        "file_name": "lessons-learned.md",
        "content": "- Always hash passwords using bcrypt before DB storage."
    }
}
~~~
