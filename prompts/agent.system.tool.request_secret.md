### `request_secret`
Use this tool to explicitly request missing secrets or credentials (e.g., API keys, tokens) from the user. This triggers a high-priority notification in the Web UI.

- **keys**: List of secret keys to request (e.g., `["RAILWAY_TOKEN"]`).
- **reason**: (Optional) Brief explanation of why these secrets are needed.

**When to use:**
- When you detect a required secret is missing from your `<secrets>` list.
- When an operation fails due to authentication/authorization issues.
- Before starting a multi-step task that will require specific credentials at a later stage.

**Example:**
```json
{
  "tool_name": "request_secret",
  "tool_args": {
    "keys": ["RAILWAY_TOKEN"],
    "reason": "Required for cloud deployment of the backend service."
  }
}
```
