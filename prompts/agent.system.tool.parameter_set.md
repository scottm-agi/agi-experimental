### `parameter_set`
Use this tool to store deterministic configuration parameters or state that needs to persist. Parameters are stored in a SQLite database and survive server restarts.

- **key**: The parameter name (e.g., `"MAX_RETRIES"`).
- **value**: Any JSON-serializable value (string, number, object, array, etc.).
- **scope**: Where to store the parameter:
  - `"project"` (default): Stores in the active project's scope
  - `"global"`: Stores in global scope, shared across all projects

**When to use:**
- Storing configuration values that agents need to reference later
- Saving state between chat sessions
- Setting project-specific or global settings
- **NOT for secrets** - use `secret_set` for API keys, passwords, tokens

**Example - Project-scoped:**
```json
{
  "tool_name": "parameter_set",
  "tool_args": {
    "key": "DEPLOYMENT_ENV",
    "value": "production",
    "scope": "project"
  }
}
```

**Example - Global:**
```json
{
  "tool_name": "parameter_set",
  "tool_args": {
    "key": "DEFAULT_TIMEOUT",
    "value": 30,
    "scope": "global"
  }
}
```
