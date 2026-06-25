## system_write

Use the `system_write` tool to make **safe, gated changes** to the AGIX system at runtime.

This tool protects critical system files while allowing you to modify tools, extensions, prompts, and user directories.

### Arguments
- **operation**: `write` | `append` | `delete`
- **path**: Relative or absolute path to the target file
- **content**: File content (for write/append operations)
- **reason**: Required explanation for why this system change is needed

### Protected Paths (CANNOT modify)
- `python/helpers/api.py`, `python/helpers/tool.py`, `python/helpers/extension.py`
- `python/agent.py`, `python/api/poll.py`
- `run_ui.py`, `initialize.py`
- `docker/`, `.git/`, `.env`

### Allowed Directories
- `python/tools/` — New or modified tools
- `python/extensions/` — New or modified extensions
- `prompts/` — System prompt changes
- `usr/` — User data and projects
- `agents/` — Agent profiles
- `helpers/dynamic/` — Dynamic runtime modules
- `webui/components/` — UI components

### Safety Features
- Automatic backups before overwrites or deletes
- Operation logging for audit trail
- Path validation with clear error messages

### Example
~~~json
{
    "operation": "write",
    "path": "python/tools/my_new_tool.py",
    "content": "from python.helpers.tool import Tool, Response\n...",
    "reason": "Adding custom tool for project automation"
}
~~~
