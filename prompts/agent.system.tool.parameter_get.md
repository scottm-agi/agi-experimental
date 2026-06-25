### `parameter_get`
Use this tool to retrieve stored configuration parameters or state.

- **key**: (Optional) The specific parameter name to retrieve. If omitted, returns all parameters.
- **scope**: Where to look:
  - `"project"` (default — **always use project scope unless the user explicitly directs otherwise**): Only project-scoped parameters
  - `"auto"`: Check project scope first, then global (use only when user requests it)
  - `"global"`: Only global parameters (use only when user explicitly asks for global values)

**When to use:**
- Reading configuration values set earlier
- Checking if a parameter exists before proceeding
- Retrieving state from a previous session
- Listing all available parameters

**Example - Get specific parameter:**
```json
{
  "tool_name": "parameter_get",
  "tool_args": {
    "key": "DEPLOYMENT_ENV"
  }
}
```

**Example - List all parameters:**
```json
{
  "tool_name": "parameter_get",
  "tool_args": {}
}
```

**Example - Get global only:**
```json
{
  "tool_name": "parameter_get",
  "tool_args": {
    "key": "DEFAULT_TIMEOUT",
    "scope": "global"
  }
}
```
