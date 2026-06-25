### `secret_get`
Use this tool to retrieve a stored secret value for use in tool calls or configurations.

- **key**: The secret name to retrieve (e.g., `"GITHUB_TOKEN"`).
- **scope**: Where to look:
  - `"project"` (default — **always use project scope unless the user explicitly directs otherwise**): Only project-scoped secrets
  - `"auto"`: Check project scope first, then global (use only when user requests it)
  - `"global"`: Only global secrets (use only when user explicitly asks for global credentials)

**When to use:**
- Verifying a secret exists before using it
- Checking which secrets are available in the current scope
- Reading a secret value for use in API calls

**Security Notes:**
- Retrieved values are masked in chat history and logs
- If the secret is not found, use `request_secret` to ask the user for it
- Prefer using the `§§secret(KEY_NAME)` placeholder format in commands

**Example:**
```json
{
  "tool_name": "secret_get",
  "tool_args": {
    "key": "GITHUB_TOKEN"
  }
}
```

**Example - Global only:**
```json
{
  "tool_name": "secret_get",
  "tool_args": {
    "key": "OPENAI_API_KEY",
    "scope": "global"
  }
}
```
